from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]

GENERIC_EXECUTION_SURFACES = (
    ROOT / ".github/workflows/repository.yml",
    ROOT / ".github/workflows/repository-plan.yml",
    ROOT / "contracts/repository-ci-v1.json",
)

CONCRETE_REPOSITORY_BRANCH = re.compile(
    r"(?:inputs\.repository|SOURCE_REPOSITORY|repository).{0,160}"
    r"(?:==|!=|=~)\s*['\"][A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+['\"]",
    re.DOTALL,
)


class PublicIdentityBoundaryTests(unittest.TestCase):
    def test_generic_execution_surfaces_do_not_branch_on_concrete_repository_identity(self) -> None:
        for path in GENERIC_EXECUTION_SURFACES:
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, CONCRETE_REPOSITORY_BRANCH, path.as_posix())

    def test_generic_repository_contract_is_fixed_entrypoint_only(self) -> None:
        text = (ROOT / ".github/workflows/repository.yml").read_text(encoding="utf-8")
        for entrypoint in (
            ".ci/build.sh",
            ".ci/test.sh",
            ".ci/test-full.sh",
            ".ci/test-ui.sh",
            ".ci/release.sh",
        ):
            self.assertIn(entrypoint, text)
        for forbidden in (
            "command_input",
            "script_path",
            "runner_label",
            "secret_name",
            "environment_map",
        ):
            self.assertNotIn(forbidden, text)

    def test_public_repository_ci_docs_keep_generic_onboarding_contract(self) -> None:
        text = (ROOT / "docs/repository-ci.md").read_text(encoding="utf-8")
        for heading in (
            "## Central execution envelope",
            "## Shared capability catalog",
            "## Repository responsibilities",
            "## Migration checklist",
        ):
            self.assertIn(heading, text)
        for entrypoint in (
            ".ci/build.sh",
            ".ci/test.sh",
            ".ci/test-full.sh",
            ".ci/test-ui.sh",
            ".ci/release.sh",
        ):
            self.assertIn(entrypoint, text)


if __name__ == "__main__":
    unittest.main()
