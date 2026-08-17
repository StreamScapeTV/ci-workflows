from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "routine": ROOT / ".github/workflows/apple-validation-smoke.yml",
    "release": ROOT / ".github/workflows/apple-certification-smoke.yml",
}
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
EXPECTED_CONCURRENCY = {
    "routine": "group: apple-validation-smoke-pr-${{ github.event.pull_request.number }}",
    "release": "group: apple-release-certification-pr-${{ github.event.pull_request.number }}",
}


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
    def test_fragment_authority_triggers_both_apple_smokes(self) -> None:
        for name, path in WORKFLOWS.items():
            with self.subTest(workflow=name):
                source = path.read_text(encoding="utf-8")
                self.assertTrue(
                    REQUIRED_PATHS.issubset(pull_request_paths(source)),
                    f"{name} Apple smoke does not watch every fragment authority path",
                )

    def test_trigger_and_concurrency_authority_did_not_expand(self) -> None:
        for name, path in WORKFLOWS.items():
            with self.subTest(workflow=name):
                source = path.read_text(encoding="utf-8")
                trigger = source.split("\npermissions:\n", 1)[0]
                self.assertIn("\n  pull_request:\n", trigger)
                self.assertNotIn("\n  push:\n", trigger)
                self.assertNotIn("workflow_dispatch:", trigger)
                self.assertNotIn("workflow_call:", trigger)
                self.assertIn(EXPECTED_CONCURRENCY[name], source)
                self.assertIn("cancel-in-progress: true", source)

    def test_routine_repair_checks_use_private_normalized_temp_root(self) -> None:
        source = WORKFLOWS["routine"].read_text(encoding="utf-8")
        self.assertIn(
            'validation_root="${RUNNER_TEMP}/ciw-apple-repair-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
            source,
        )
        self.assertIn('chmod 0700 "${validation_root}"', source)
        self.assertIn('chmod 0700 "${validation_root}/tmp"', source)
        self.assertIn("stat -c '%a'", source)
        self.assertIn('TMPDIR="${validation_root}/tmp"', source)
        self.assertIn("trap 'rm -rf -- \"${validation_root}\"' EXIT", source)
        self.assertIn('test ! -e "${validation_root}"', source)
        self.assertNotIn("sudo chmod", source)


if __name__ == "__main__":
    unittest.main()
