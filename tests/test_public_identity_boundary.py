from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
LEGACY_MARKER = "LEGACY_MIGRATION_RESIDUE"

# These paths are the audited compatibility residue for the current source tree.
# The list is path-based on purpose: it does not encode or legitimize any private
# consumer identity. New reusable behavior must not be added here merely to make
# an identity check pass this test.
LEGACY_RESIDUE_PATHS = {
    ".github/workflows/android.yml",
    ".github/workflows/apple-binary.yml",
    ".github/workflows/apple.yml",
    ".github/workflows/central-ci-dispatch.yml",
    ".github/workflows/node.yml",
    "actions/google-drive/action.yml",
    "tests/test_android_screenshot_review.py",
    "tests/test_android_search_performance_profile.py",
    "tests/test_apple_binary_workflow.py",
    "tests/test_apple_ios_simulator_recovery.py",
    "tests/test_apple_workflow.py",
    "tests/test_ci_helpers.py",
    "tests/test_google_drive_media_put.py",
    "tests/test_google_drive_screenshot_files.py",
}

# Public/shared infrastructure repositories are not private consumers. Generic
# test fixtures are recognized by shape rather than by a consumer-name blacklist.
SHARED_INFRA_REPOSITORIES = {
    "agent-state-supabase",
    "ci-workflows",
    "organization-rules",
}
GENERIC_FIXTURE_REPOSITORY = re.compile(
    r"^(?:example(?:-[a-z0-9.-]+)?|repo|other|library-(?:one|two))$"
)
REPOSITORY_REFERENCE = re.compile(r"StreamScapeTV/([A-Za-z0-9_.-]+)")

TEXT_SUFFIXES = {".json", ".md", ".py", ".sh", ".txt", ".yaml", ".yml"}


def repository_text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {"Dockerfile"}:
            files.append(path)
    return files


def concrete_consumer_repositories(text: str) -> set[str]:
    found = set()
    for repository in REPOSITORY_REFERENCE.findall(text):
        repository = repository.removesuffix(".git.extraheader").removesuffix(".git")
        if repository in SHARED_INFRA_REPOSITORIES:
            continue
        if GENERIC_FIXTURE_REPOSITORY.fullmatch(repository):
            continue
        found.add(repository)
    return found


class PublicIdentityBoundaryTests(unittest.TestCase):
    def test_audited_legacy_residue_paths_are_explicitly_marked(self) -> None:
        for relative in sorted(LEGACY_RESIDUE_PATHS):
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertIn(LEGACY_MARKER, path.read_text(encoding="utf-8"), relative)

    def test_concrete_consumer_repository_refs_exist_only_in_marked_legacy_residue(self) -> None:
        for path in repository_text_files():
            relative = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            concrete = concrete_consumer_repositories(text)
            if not concrete:
                continue
            self.assertIn(relative, LEGACY_RESIDUE_PATHS, relative)
            self.assertIn(LEGACY_MARKER, text, relative)

    def test_generic_public_surfaces_use_no_concrete_consumer_repository_identity(self) -> None:
        generic_surfaces = (
            ROOT / ".github/workflows/repository.yml",
            ROOT / "contracts/repository-ci-v1.json",
            ROOT / "INVENTORY.yaml",
            ROOT / "README.md",
            ROOT / "docs/repository-ci.md",
            ROOT / "docs/repository-ci-capability-audit.md",
        )
        for path in generic_surfaces:
            text = path.read_text(encoding="utf-8")
            self.assertFalse(concrete_consumer_repositories(text), path.as_posix())

    def test_repository_ci_capability_audit_freezes_consumer_neutral_v1_boundary(self) -> None:
        path = ROOT / "docs/repository-ci-capability-audit.md"
        text = path.read_text(encoding="utf-8")
        self.assertFalse(concrete_consumer_repositories(text), path.as_posix())
        for capability in ("private_network", "github_git", "registry_netrc", "gradle_maven"):
            self.assertIn(f"`{capability}`", text)
        self.assertIn("exactly these non-host optional capability types", text)
        self.assertIn("No generic cache capability is required", text)
        self.assertIn("Release and publication migration plan", text)
        self.assertIn("legacy release lane only after its private live-caller count reaches", text)
        self.assertIn("caller-provided secret names", text)
        self.assertIn("arbitrary environment metadata", text)
        self.assertIn("runner labels", text)

    def test_repository_ci_onboarding_guide_covers_v1_contract(self) -> None:
        path = ROOT / "docs/repository-ci.md"
        text = path.read_text(encoding="utf-8")
        self.assertFalse(concrete_consumer_repositories(text), path.as_posix())
        for heading in (
            "## Central execution envelope",
            "## Shared capability catalog",
            "## Complete script environment reference",
            "## Repository responsibilities",
            "## Private Agent State configuration",
            "## Release authorization boundary",
            "## Private evidence, retention and cleanup",
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
        for variable in (
            "CI_HOST_OS",
            "CI_HOST_CLASS",
            "CI_OPERATION",
            "CI_INPUTS_JSON",
            "CI_LOG_DIR",
            "CI_ARTIFACT_DIR",
            "CI_PROGRESS_FILE",
            "CI_APPLE_TEAM_ID",
            "CI_APP_STORE_CONNECT_KEY_ID",
            "CI_APP_STORE_CONNECT_ISSUER_ID",
            "CI_APP_STORE_CONNECT_API_KEY_P8_BASE64",
        ):
            self.assertIn(f"`{variable}`", text)
        for capability in ("private_network", "github_git", "registry_netrc", "gradle_maven"):
            self.assertIn(f"`{capability}`", text)
        self.assertIn('"repository": "organization/example-service"', text)
        self.assertIn("repository-ci-capability-audit.md", text)
        self.assertIn("stdout/stderr is captured automatically", text)
        self.assertIn("no remaining live caller", text)

    def test_unmarked_tests_use_only_shared_or_non_identifying_repository_fixtures(self) -> None:
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            if LEGACY_MARKER in text:
                continue
            self.assertFalse(concrete_consumer_repositories(text), path.name)


if __name__ == "__main__":
    unittest.main()
