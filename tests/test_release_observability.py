from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReleaseObservabilityTests(unittest.TestCase):
    def test_exact_tag_source_sha_and_readable_private_log_are_terminal_gates(self) -> None:
        path = ROOT / ".github/workflows/public-native-image-chart.yml"
        workflow = yaml.safe_load(path.read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps if step.get("name")}

        authority = by_name["Verify exact tag authority"]
        publication = by_name["Resolve product-owned publication version"]
        authenticate = by_name["Authenticate and require unused immutable version"]
        observe = by_name["Record exact tagged product source SHA"]
        revalidate = by_name["Revalidate exact tag before publication"]
        publish = by_name["Publish immutable image and chart"]
        readback = by_name["Authenticated private registry read-back"]
        cleanup = by_name["Clean publication state"]
        scrub = by_name["Scrub private release log"]
        drive = by_name["Upload private release log to Google Drive"]
        finish = by_name["Finish Agent State run"]

        self.assertIn('source_sha="$(git -C source rev-parse HEAD)"', authority["run"])
        self.assertIn('tag_sha="$(git -C source rev-parse "refs/tags/${RELEASE_TAG}^{commit}")"', authority["run"])
        self.assertIn('test "${source_sha}" = "${tag_sha}"', authority["run"])
        self.assertEqual(authority["env"]["RELEASE_TAG"], "${{ inputs.ref }}")
        self.assertNotIn("MAJOR.MINOR.PATCH", authority["run"])
        self.assertNotIn("[0-9]*)\\.", authority["run"])
        self.assertNotIn("version=%s", authority["run"])

        self.assertEqual(publication["env"]["CHART_PATH"], "${{ inputs.chart_path }}")
        self.assertIn('helm show chart "source/${CHART_PATH}"', publication["run"])
        self.assertIn('/^version:[[:space:]]+/', publication["run"])
        self.assertNotIn('$1 == "version:"', publication["run"])
        self.assertIn("found != 1", publication["run"])
        self.assertIn("publication_version=%s", publication["run"])
        self.assertNotIn("RELEASE_TAG", publication["run"])
        self.assertNotIn("inputs.ref", publication["run"])
        self.assertEqual(observe["uses"], "StreamScapeTV/ci-workflows/actions/agent-state@main")
        self.assertEqual(observe["with"]["phase"], "observe-source")
        self.assertEqual(observe["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        self.assertEqual(observe["with"]["observed_source_sha"], "${{ steps.authority.outputs.source_sha }}")
        self.assertNotIn("github.sha", observe["with"]["observed_source_sha"])

        self.assertEqual(drive["with"]["file_name"], "${{ github.run_id }}-${{ github.run_attempt }}.txt")
        self.assertEqual(drive["with"]["gzip"], "false")
        self.assertEqual(drive["with"]["mime_type"], "text/plain")
        self.assertEqual(drive["if"], "${{ always() && steps.scrub.outcome == 'success' }}")

        self.assertLess(names.index("Check out exact tagged product source"), names.index("Verify exact tag authority"))
        self.assertLess(names.index("Verify exact tag authority"), names.index("Record exact tagged product source SHA"))
        self.assertLess(names.index("Record exact tagged product source SHA"), names.index("Resolve product-owned publication version"))
        self.assertLess(names.index("Resolve product-owned publication version"), names.index("Revalidate exact tag before publication"))
        self.assertLess(names.index("Revalidate exact tag before publication"), names.index("Publish immutable image and chart"))
        self.assertLess(names.index("Publish immutable image and chart"), names.index("Authenticated private registry read-back"))
        self.assertLess(names.index("Authenticated private registry read-back"), names.index("Clean publication state"))
        self.assertLess(names.index("Clean publication state"), names.index("Scrub private release log"))
        self.assertLess(names.index("Scrub private release log"), names.index("Upload private release log to Google Drive"))
        self.assertLess(names.index("Upload private release log to Google Drive"), names.index("Finish Agent State run"))

        self.assertIn('test "$(git -C source rev-parse HEAD)" = "${SOURCE_SHA}"', revalidate["run"])
        self.assertIn('refs/tags/${RELEASE_TAG}^{commit}', revalidate["run"])
        self.assertEqual(revalidate["env"]["RELEASE_TAG"], "${{ inputs.ref }}")
        version_expr = "${{ steps.publication.outputs.publication_version }}"
        prepare = by_name["Build image and package chart"]
        self.assertEqual(prepare["env"]["VERSION"], version_expr)
        self.assertEqual(authenticate["env"]["VERSION"], version_expr)
        self.assertEqual(publish["env"]["VERSION"], version_expr)
        self.assertEqual(readback["env"]["VERSION"], version_expr)
        self.assertIn(version_expr, publish["env"]["REMOTE_IMAGE_REFERENCE"])
        self.assertIn(version_expr, readback["env"]["REMOTE_IMAGE_REFERENCE"])
        self.assertNotIn("steps.authority.outputs.version", path.read_text())
        self.assertIn("Immutable publication already exists", authenticate["run"])
        self.assertIn('require_unused "${image_reference}"', authenticate["run"])
        self.assertIn('require_unused "${chart_reference}"', authenticate["run"])
        self.assertIn("buildah push", publish["run"])
        self.assertIn("helm push", publish["run"])
        self.assertIn("skopeo inspect", readback["run"])
        self.assertIn("helm pull", readback["run"])
        self.assertEqual(cleanup["if"], "always()")
        self.assertEqual(scrub["if"], "always()")
        status = finish["with"]["status"]
        for required in (
            "steps.publication.outcome == 'success'",
            "steps.revalidate.outcome == 'success'",
            "steps.publish.outcome == 'success'",
            "steps.readback.outcome == 'success'",
            "steps.cleanup.outcome == 'success'",
            "steps.scrub.outcome == 'success'",
            "steps.drive.outcome == 'success'",
        ):
            self.assertIn(required, status)

    def test_chart_metadata_version_is_emitted_unchanged_independent_of_source_tag(self) -> None:
        path = ROOT / ".github/workflows/public-native-image-chart.yml"
        workflow = yaml.safe_load(path.read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        publication = next(step for step in steps if step.get("name") == "Resolve product-owned publication version")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            helm = bin_dir / "helm"
            helm.write_text(
                "#!/usr/bin/env bash\n"
                "set -Eeuo pipefail\n"
                "test \"$1\" = show && test \"$2\" = chart\n"
                "printf '%s\\n' 'apiVersion: v2' 'name: fixture' "
                "'version: 2.4.0-build.253+linux' 'dependencies:' "
                "'  - name: valkey' '    version: 0.11.0'\n",
                encoding="utf-8",
            )
            helm.chmod(0o755)
            output = root / "github-output"
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "CHART_PATH": "charts/fixture",
                "GITHUB_OUTPUT": str(output),
                "RELEASE_TAG": "source-release-candidate",
            }
            completed = subprocess.run(
                ["bash", "-c", publication["run"]],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(output.read_text(), "publication_version=2.4.0-build.253+linux\n")

            for metadata in (
                ("apiVersion: v2", "name: fixture"),
                ("version: 1.0.0", "version: 2.0.0"),
            ):
                helm.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -Eeuo pipefail\n"
                    "test \"$1\" = show && test \"$2\" = chart\n"
                    + "printf '%s\\n' "
                    + " ".join(repr(value) for value in metadata)
                    + "\n",
                    encoding="utf-8",
                )
                output.unlink(missing_ok=True)
                rejected = subprocess.run(
                    ["bash", "-c", publication["run"]],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(rejected.returncode, 0)


if __name__ == "__main__":
    unittest.main()
