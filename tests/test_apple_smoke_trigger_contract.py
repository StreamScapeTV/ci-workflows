from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple-validation-smoke.yml"
REQUIRED_PATHS = {
    "src/ci_workflows/apple_contract.py",
    "src/ci_workflows/apple_contract_fragments.py",
    "src/ci_workflows/ciw_apple.py",
    "contracts/apple-validation.json",
    "contracts/apple-validation-*.json",
    "tests/test_apple_contract_fragments.py",
    "tests/test_apple_contract_fragment_path_safety.py",
    "tests/test_apple_fragment_loader_isolation.py",
    "tests/test_apple_smoke_trigger_contract.py",
}
EXPECTED_CONCURRENCY = "group: apple-validation-smoke-pr-${{ github.event.pull_request.number }}"
HOSTED_CONTROL_SELECTOR = "runs-on: [ubuntu-latest]"
HOSTED_APPLE_SELECTOR = "runs-on: [macos-latest]"
OWNER_GATE = "github.event.pull_request.user.login == 'mimranfaruqi'"
REPOSITORY_GATE = "github.event.pull_request.head.repo.full_name == github.repository"


def pull_request_paths(source: str) -> set[str]:
    try:
        block = source.split("    paths:\n", 1)[1].split("\n\npermissions:\n", 1)[0]
    except IndexError as error:
        raise AssertionError("Apple smoke pull_request paths block is missing") from error
    return {
        line.removeprefix("      - ").strip()
        for line in block.splitlines()
        if line.startswith("      - ")
    }


class AppleSmokeTriggerContractTests(unittest.TestCase):
    def test_fragment_authority_triggers_retained_apple_smoke(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertTrue(REQUIRED_PATHS.issubset(pull_request_paths(source)))

    def test_trigger_and_concurrency_authority_did_not_expand(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        trigger = source.split("\npermissions:\n", 1)[0]
        self.assertIn("\n  pull_request:\n", trigger)
        self.assertNotIn("\n  push:\n", trigger)
        self.assertNotIn("workflow_dispatch:", trigger)
        self.assertNotIn("workflow_call:", trigger)
        self.assertIn(EXPECTED_CONCURRENCY, source)
        self.assertIn("cancel-in-progress: true", source)

    def test_public_repository_apple_self_ci_is_github_hosted_and_owner_gated(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(source.count(HOSTED_CONTROL_SELECTOR), 2)
        self.assertEqual(source.count(HOSTED_APPLE_SELECTOR), 1)
        self.assertNotIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", source)
        self.assertNotIn("runs-on: [linux, amd64, general, small]", source)
        self.assertGreaterEqual(source.count(OWNER_GATE), 3)
        self.assertGreaterEqual(source.count(REPOSITORY_GATE), 3)
        self.assertNotIn("github.event.repository.private", source)
        self.assertNotIn("REPOSITORY_PRIVATE", source)
        self.assertIn('test "${APPLE_RESULT}" = success', source)
        self.assertIn('["macOS","ARM64"]', source)
        self.assertIn("Real protected-full Apple smoke", source)

    def test_profile_specific_and_legacy_apple_entrypoints_are_retired(self) -> None:
        self.assertFalse((ROOT / ".github/workflows/apple-certification-smoke.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/apple-test.yml").exists())
        self.assertTrue((ROOT / "tests/test_apple_release_profiles.py").is_file())


if __name__ == "__main__":
    unittest.main()
