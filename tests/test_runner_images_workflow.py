from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-images.yml"
KNOWN_IMAGES = ["general", "mobile", "buildah", "service", "docker"]


class RunnerImagesWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load(WORKFLOW.read_text())

    def test_pull_request_validation_is_read_only_and_bounded(self) -> None:
        trigger = self.workflow["on"]
        self.assertEqual(trigger["pull_request"]["branches"], ["main"])
        self.assertEqual(
            trigger["pull_request"]["paths"],
            ["runner-images/**", ".github/workflows/runner-images.yml"],
        )
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

        jobs = self.workflow["jobs"]
        plan = jobs["plan"]
        self.assertEqual(plan["if"], "${{ github.event_name == 'pull_request' }}")
        planner = next(step for step in plan["steps"] if step.get("name") == "Resolve changed runner images")
        script = planner["run"]
        self.assertIn('["git", "diff", "--name-only", base, head]', script)
        self.assertIn("unrecognized runner-image path", script)
        for image in KNOWN_IMAGES:
            self.assertIn(f'"{image}"', script)

        validate = jobs["validate"]
        self.assertEqual(validate["needs"], "plan")
        self.assertEqual(validate["if"], "${{ needs.plan.outputs.has_images == 'true' }}")
        self.assertNotIn("permissions", validate)
        self.assertEqual(validate["strategy"]["matrix"]["image"], "${{ fromJSON(needs.plan.outputs.images) }}")
        validate_text = yaml.safe_dump(validate)
        self.assertIn("docker build", validate_text)
        self.assertIn("runner-image-smoke", validate_text)
        self.assertNotIn("docker login", validate_text)
        self.assertNotIn("docker push", validate_text)
        self.assertNotIn("ghcr.io/streamscapetv", validate_text)

    def test_tag_publication_keeps_write_permission_and_all_images(self) -> None:
        trigger = self.workflow["on"]
        self.assertEqual(trigger["push"]["tags"], ["runner-images-*"])

        build = self.workflow["jobs"]["build"]
        self.assertEqual(build["if"], "${{ github.event_name == 'push' }}")
        self.assertEqual(build["permissions"], {"contents": "read", "packages": "write"})
        self.assertEqual(build["strategy"]["matrix"]["image"], KNOWN_IMAGES)
        build_text = yaml.safe_dump(build)
        self.assertIn("docker build", build_text)
        self.assertIn("runner-image-smoke", build_text)
        self.assertIn("docker login ghcr.io", build_text)
        self.assertIn("docker push", build_text)


if __name__ == "__main__":
    unittest.main()
