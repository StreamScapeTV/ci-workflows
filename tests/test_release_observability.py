from pathlib import Path
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

        authority = by_name["Verify exact stable tag authority"]
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
        self.assertEqual(observe["uses"], "StreamScapeTV/ci-workflows/actions/agent-state@main")
        self.assertEqual(observe["with"]["phase"], "observe-source")
        self.assertEqual(observe["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        self.assertEqual(observe["with"]["observed_source_sha"], "${{ steps.authority.outputs.source_sha }}")
        self.assertNotIn("github.sha", observe["with"]["observed_source_sha"])

        self.assertEqual(drive["with"]["file_name"], "${{ github.run_id }}-${{ github.run_attempt }}.txt")
        self.assertEqual(drive["with"]["gzip"], "false")
        self.assertEqual(drive["with"]["mime_type"], "text/plain")
        self.assertEqual(drive["if"], "${{ always() && steps.scrub.outcome == 'success' }}")

        self.assertLess(names.index("Check out exact tagged product source"), names.index("Verify exact stable tag authority"))
        self.assertLess(names.index("Verify exact stable tag authority"), names.index("Record exact tagged product source SHA"))
        self.assertLess(names.index("Record exact tagged product source SHA"), names.index("Revalidate exact tag before publication"))
        self.assertLess(names.index("Revalidate exact tag before publication"), names.index("Publish immutable image and chart"))
        self.assertLess(names.index("Publish immutable image and chart"), names.index("Authenticated private registry read-back"))
        self.assertLess(names.index("Authenticated private registry read-back"), names.index("Clean publication state"))
        self.assertLess(names.index("Clean publication state"), names.index("Scrub private release log"))
        self.assertLess(names.index("Scrub private release log"), names.index("Upload private release log to Google Drive"))
        self.assertLess(names.index("Upload private release log to Google Drive"), names.index("Finish Agent State run"))

        self.assertIn('test "$(git -C source rev-parse HEAD)" = "${SOURCE_SHA}"', revalidate["run"])
        self.assertIn('refs/tags/${RELEASE_TAG}^{commit}', revalidate["run"])
        self.assertIn("buildah push", publish["run"])
        self.assertIn("helm push", publish["run"])
        self.assertIn("skopeo inspect", readback["run"])
        self.assertIn("helm pull", readback["run"])
        self.assertEqual(cleanup["if"], "always()")
        self.assertEqual(scrub["if"], "always()")
        status = finish["with"]["status"]
        for required in (
            "steps.revalidate.outcome == 'success'",
            "steps.publish.outcome == 'success'",
            "steps.readback.outcome == 'success'",
            "steps.cleanup.outcome == 'success'",
            "steps.scrub.outcome == 'success'",
            "steps.drive.outcome == 'success'",
        ):
            self.assertIn(required, status)


if __name__ == "__main__":
    unittest.main()
