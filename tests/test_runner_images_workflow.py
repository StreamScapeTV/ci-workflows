from __future__ import annotations

import os
import subprocess
import tempfile
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

    def test_mobile_flutter_smoke_cleanup_is_bounded_and_fail_closed(self) -> None:
        smoke = (ROOT / "runner-images/mobile/smoke.sh").read_text()
        self.assertIn("trap cleanup_flutter_smoke EXIT", smoke)
        self.assertIn("for attempt in 1 2 3 4 5 6 7 8; do", smoke)
        self.assertIn('rm -rf -- "${flutter_smoke_root}"', smoke)
        self.assertIn('test ! -e "${flutter_smoke_root}"', smoke)
        self.assertIn('test ! -L "${flutter_smoke_root}"', smoke)
        self.assertIn('echo "Mobile Flutter smoke fixture cleanup did not converge" >&2', smoke)
        self.assertIn("    return 1", smoke)
        self.assertIn("  cleanup_flutter_smoke\n  trap - EXIT", smoke)

    def test_mobile_flutter_cleanup_recovers_race_and_rejects_residue(self) -> None:
        smoke = (ROOT / "runner-images/mobile/smoke.sh").read_text()
        start = smoke.index("  cleanup_flutter_smoke() {")
        end = smoke.index("\n  trap cleanup_flutter_smoke EXIT", start)
        function = smoke[start:end]
        with tempfile.TemporaryDirectory() as temp_root:
            fixture = Path(temp_root) / "flutter-fixture"
            fixture.mkdir()
            (fixture / "gradle").mkdir()
            env = {**os.environ, "SMOKE_ROOT": str(fixture)}
            setup = ('set -Eeuo pipefail\n'
                     'flutter_smoke_root="$SMOKE_ROOT"\n'
                     + function + '\n'
                     'tries=0\n'
                     'sleep() { :; }\n')

            recovered = subprocess.run(
                ["bash", "-c", setup
                 + 'rm() { tries=$((tries + 1)); '
                   'if (( tries < 3 )); then return 1; fi; command rm "$@"; }\n'
                   'cleanup_flutter_smoke\n'
                   'test "$tries" -eq 3\n'
                   'test ! -e "$flutter_smoke_root"\n'],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)

            fixture.mkdir()
            (fixture / "gradle").mkdir()
            rejected = subprocess.run(
                ["bash", "-c", setup
                 + 'rm() { tries=$((tries + 1)); return 1; }\n'
                   'if cleanup_flutter_smoke; then exit 11; fi\n'
                   'test "$tries" -eq 8\n'
                   'test -d "$flutter_smoke_root"\n'],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(rejected.returncode, 0, rejected.stderr)
            self.assertIn("cleanup did not converge", rejected.stderr)

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
