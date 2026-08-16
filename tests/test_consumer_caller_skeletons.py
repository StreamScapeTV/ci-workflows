from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKELETON_ROOT = ROOT / "docs" / "workflows" / "caller-skeletons"
README = SKELETON_ROOT / "README.md"

FAMILIES = {
    "apple.yml": "APPLE",
    "android.yml": "ANDROID",
    "node.yml": "NODE",
    "python.yml": "PYTHON",
    "script.yml": "SCRIPT",
}

FORBIDDEN_PRODUCT_IDENTITIES = (
    "iptv-apple",
    "iptv-android",
    "iptv-backend",
    "streamscape-media",
    "StreamScapeWeb",
    "directus-front",
    "finance-hub",
    "agent-state-dashboard",
)


class ConsumerCallerSkeletonTests(unittest.TestCase):
    def _load(self, filename: str) -> tuple[str, dict[str, object]]:
        source = (SKELETON_ROOT / filename).read_text(encoding="utf-8")
        document = yaml.load(source, Loader=yaml.BaseLoader)
        self.assertIsInstance(document, dict)
        return source, document

    def test_templates_keep_trigger_path_and_ref_concurrency_ownership_explicit(self) -> None:
        for filename in FAMILIES:
            with self.subTest(filename=filename):
                source, document = self._load(filename)
                triggers = document["on"]
                for event in ("pull_request", "push"):
                    self.assertIn("TODO_PROTECTED_BRANCH", triggers[event]["branches"])
                    self.assertEqual(
                        [
                            "TODO_PRODUCT_PATH/**",
                            ".github/workflows/TODO_CALLER_WORKFLOW.yml",
                            "TODO_PRODUCT_CONFIGURATION_PATH",
                        ],
                        triggers[event]["paths"],
                    )
                self.assertIn(
                    "TODO_OPTIONAL_FEATURE_BRANCH_GLOB",
                    triggers["push"]["branches"],
                )
                self.assertEqual(
                    {
                        "group": "${{ github.workflow }}-${{ github.ref }}",
                        "cancel-in-progress": "true",
                    },
                    document["concurrency"],
                )
                self.assertIn("choose product-owned paths OR paths-ignore", source)

    def test_templates_use_minimum_permissions_and_unresolved_generic_call_only(self) -> None:
        for filename, token in FAMILIES.items():
            with self.subTest(filename=filename):
                source, document = self._load(filename)
                self.assertEqual({"contents": "read"}, document["permissions"])
                self.assertEqual(["validate"], list(document["jobs"]))
                job = document["jobs"]["validate"]
                self.assertEqual(
                    "StreamScapeTV/ci-workflows/.github/workflows/"
                    f"TODO_REUSABLE_{token}_WORKFLOW.yml@TODO_CENTRAL_REF",
                    job["uses"],
                )
                self.assertEqual(
                    {f"TODO_BOUNDED_{token}_INPUT": "TODO_PRODUCT_OWNED_VALUE"},
                    job["with"],
                )
                self.assertNotIn("runs-on", job)
                self.assertNotIn("steps", job)
                self.assertNotIn("secrets", job)
                for forbidden in (
                    "secrets: inherit",
                    "actions/cache",
                    "upload-artifact",
                    "download-artifact",
                    "self-hosted",
                    "runs-on:",
                ):
                    self.assertNotIn(forbidden, source)

    def test_templates_do_not_encode_application_repository_identity(self) -> None:
        for filename in FAMILIES:
            source, _ = self._load(filename)
            for forbidden in FORBIDDEN_PRODUCT_IDENTITIES:
                with self.subTest(filename=filename, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_readme_marks_scaffolding_and_finalization_dependencies(self) -> None:
        source = README.read_text(encoding="utf-8")
        for required in (
            "copy/adapt scaffolding",
            "TODO_PROTECTED_BRANCH",
            "TODO_OPTIONAL_FEATURE_BRANCH_GLOB",
            "paths-ignore",
            "${{ github.workflow }}-${{ github.ref }}",
            "Exact source SHA is evidence identity, not concurrency identity.",
            "`[skip ci]`",
            "`[ci skip]`",
            "merged shared\n`organization-rules@main/RULES.md` policy is authoritative",
            "A skipped HEAD is\ncheckpoint-only",
            "#283, #322, and #323",
            "not introduce a planner, queue, database, custom CI command language",
            "issue-dependency synchronization remains",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        self.assertNotIn("Until #66 is merged", source)
        self.assertNotIn("[skip push ci]", source)


if __name__ == "__main__":
    unittest.main()
