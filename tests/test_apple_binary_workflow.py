from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple-binary.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"


class AppleBinaryWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)
        self.job = self.workflow["jobs"]["publish"]

    def step(self, name: str) -> dict:
        return next(step for step in self.job["steps"] if step.get("name") == name)

    def test_api_is_capability_only_and_product_neutral(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "ref", "ci_run_id", "upload_private_log"},
        )
        self.assertTrue(all(not value["required"] for value in call["secrets"].values()))
        self.assertIn("PACKAGE_PUBLISH_USERNAME", call["secrets"])
        self.assertIn("PACKAGE_PUBLISH_TOKEN", call["secrets"])
        self.assertIn("PACKAGE_READ_TOKEN", call["secrets"])
        self.assertFalse(any(name.startswith("MAVEN_") for name in call["secrets"]))
        self.assertEqual(
            self.job["runs-on"],
            "${{ fromJSON((inputs.repository || github.repository) == 'StreamScapeTV/streamscape-media' && '[\"macOS\",\"ARM64\"]' || '[\"macos-latest\"]') }}",
        )
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        self.assertIn("StreamScapeTV/streamscape-media", self.job["runs-on"])
        self.assertIn("[\"macOS\",\"ARM64\"]", self.job["runs-on"])
        self.assertIn("[\"macos-latest\"]", self.job["runs-on"])
        self.assertNotIn("self-hosted", self.job["runs-on"])
        self.assertNotIn("actions/cache", self.text)

        for forbidden in (
            "Streamscape Media",
            "StreamscapePlaybackApple",
            "nativeRuntimeBoundary",
            "MPV",
            "VLC",
            "git.faruqi.dev",
            "registry_url",
            "runner_label",
            "script_path",
            "build_command",
            "publish_command",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_source_identity_and_fixed_wrapper_are_fail_closed(self) -> None:
        checkout = self.step("Check out source")
        self.assertEqual(checkout["with"]["repository"], "${{ inputs.repository || github.repository }}")
        self.assertEqual(checkout["with"]["ref"], "${{ inputs.ref || github.sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])

        identity = self.step("Resolve observed source SHA")
        self.assertEqual(identity["env"]["DIRECT_CALLER"], "${{ inputs.repository == '' && inputs.ref == '' }}")
        self.assertIn('test "${source_sha}" = "${CALLER_SHA}"', identity["run"])
        observe = self.step("Record observed source SHA")
        self.assertEqual(observe["with"]["observed_source_sha"], "${{ steps.source_identity.outputs.source_sha }}")

        wrapper_script = self.step("Validate fixed Apple binary wrapper")["run"]
        self.assertIn('wrapper="scripts/ci/run-apple-binary-publication.sh"', wrapper_script)
        self.assertIn('test ! -L "${wrapper}"', wrapper_script)
        self.assertIn('git ls-files --error-unmatch -- "${wrapper}"', wrapper_script)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            wrapper = root / "scripts/ci/run-apple-binary-publication.sh"
            wrapper.parent.mkdir(parents=True)

            missing = subprocess.run(["bash", "-c", wrapper_script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)

            wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            untracked = subprocess.run(["bash", "-c", wrapper_script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(untracked.returncode, 0)

            wrapper.unlink()
            target = root / "target.sh"
            target.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            wrapper.symlink_to(target)
            symlink = subprocess.run(["bash", "-c", wrapper_script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(symlink.returncode, 0)

            wrapper.unlink()
            wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            subprocess.run(["git", "add", "scripts/ci/run-apple-binary-publication.sh"], cwd=root, check=True)
            tracked = subprocess.run(["bash", "-c", wrapper_script], cwd=root, capture_output=True, text=True)
            self.assertEqual(tracked.returncode, 0, tracked.stderr)

    def test_fixed_wrapper_receives_only_bounded_central_environment(self) -> None:
        commands = self.step("Run fixed Apple binary publication profile")
        env = commands["env"]
        self.assertEqual(env["CI_APPLE_BINARY_PROFILE"], "publish")
        self.assertEqual(env["CI_APPLE_BINARY_SOURCE_SHA"], "${{ steps.source_identity.outputs.source_sha }}")
        self.assertEqual(env["CI_APPLE_BINARY_SOURCE_REPOSITORY"], "${{ inputs.repository || github.repository }}")
        self.assertEqual(env["CI_APPLE_BINARY_SOURCE_REF"], "${{ inputs.ref || github.ref }}")
        self.assertIn("CI_APPLE_BINARY_EVIDENCE_DIR", env)
        self.assertIn("CI_APPLE_BINARY_RESULT_FILE", env)
        self.assertIn("run_logged apple-binary-publish bash scripts/ci/run-apple-binary-publication.sh", commands["run"])
        self.assertNotIn("bash -lc", commands["run"])
        for forbidden in ("COMMAND", "SCRIPT", "RUNNER", "REGISTRY_URL", "PACKAGE_NAME"):
            self.assertFalse(any(forbidden in key for key in env), forbidden)

    def test_generic_evidence_validation_does_not_parse_product_manifest(self) -> None:
        script = self.step("Validate generic Apple binary publication evidence")["run"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "evidence"
            evidence.mkdir()
            result = root / "result.json"
            output = root / "output.txt"
            result.write_text(json.dumps({"schema_version": 1, "publication_id": "library-one-2.0"}) + "\n")
            with zipfile.ZipFile(evidence / "publication.zip", "w") as archive:
                archive.writestr("Library.xcframework/Info.plist", b"opaque-binary-package")
            # Central checks generic JSON shape only; these product-specific fields are intentionally opaque to it.
            (evidence / "manifest.json").write_text(
                json.dumps({"productSpecificGraph": {"arbitrary": ["value"]}, "schema": "owned-elsewhere"}) + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "EVIDENCE_DIR": str(evidence),
                    "RESULT_FILE": str(result),
                    "GITHUB_OUTPUT": str(output),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(values["publication_id"], "library-one-2.0")
            self.assertRegex(values["archive_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(values["manifest_sha256"], r"^[0-9a-f]{64}$")

            result.write_text(json.dumps({"schema_version": 1, "publication_id": "../escape"}) + "\n")
            rejected = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "EVIDENCE_DIR": str(evidence),
                    "RESULT_FILE": str(result),
                    "GITHUB_OUTPUT": str(root / "rejected.txt"),
                },
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)

    def test_release_apple_binary_package_dispatch_is_repository_agnostic(self) -> None:
        workflow = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        validation = next(
            step for step in jobs["request"]["steps"] if step.get("name") == "Validate Apple release request"
        )
        script = validation["run"]
        self.assertEqual(set(validation["env"]), {"TEST_PROFILE", "INPUTS_JSON"})
        self.assertNotIn("repository", script.lower())
        self.assertNotIn("streamscape", script.lower())

        for repository in ("StreamScapeTV/library-one", "StreamScapeTV/library-two"):
            completed = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "TEST_PROFILE": "binary-package",
                    "INPUTS_JSON": "{}",
                    "SOURCE_REPOSITORY": repository,
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, (repository, completed.stderr))

        extra = subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, "TEST_PROFILE": "binary-package", "INPUTS_JSON": '{"command":"x"}'},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(extra.returncode, 0)

        job = jobs["apple_binary"]
        self.assertEqual(
            job["if"],
            "${{ needs.request.outputs.workflow_key == 'release.apple' && needs.request.outputs.test_profile == 'binary-package' }}",
        )
        self.assertEqual(job["uses"], "./.github/workflows/apple-binary.yml")
        self.assertEqual(set(job["with"]), {"repository", "ref", "ci_run_id"})
        self.assertEqual(job["secrets"]["PACKAGE_PUBLISH_USERNAME"], "${{ secrets.MAVEN_PUBLISH_USERNAME }}")
        self.assertEqual(job["secrets"]["PACKAGE_PUBLISH_TOKEN"], "${{ secrets.MAVEN_PUBLISH_TOKEN }}")
        self.assertEqual(job["secrets"]["PACKAGE_READ_TOKEN"], "${{ secrets.MAVEN_READ_TOKEN }}")
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_inventory_and_self_check_have_only_generic_apple_binary_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text(encoding="utf-8"))
        self.assertEqual(inventory["workflows"]["apple_binary"], ".github/workflows/apple-binary.yml")
        self.assertNotIn("streamscape_media_release", inventory["workflows"])
        self.assertNotIn("streamscape_media_apple_binary", inventory["workflows"])
        self.assertNotIn("streamscape_media_release", inventory["scripts"])
        self.assertFalse((ROOT / ".github/workflows/streamscape-media-release.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/streamscape-media-apple-binary.yml").exists())
        self.assertFalse((ROOT / "scripts/ci/streamscape_media_release.py").exists())
        self.assertFalse((ROOT / "tests/test_streamscape_media_release.py").exists())

        dispatch_text = DISPATCH.read_text(encoding="utf-8")
        self.assertNotIn("release.streamscape-media", dispatch_text)
        self.assertNotIn("streamscape_media_release", dispatch_text)
        self.assertIn("tests.test_apple_binary_workflow", (ROOT / ".github/workflows/self-check.yml").read_text())


if __name__ == "__main__":
    unittest.main()
