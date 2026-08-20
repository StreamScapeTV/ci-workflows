from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKELETON_ROOT = ROOT / "docs" / "workflows" / "caller-skeletons"
README = SKELETON_ROOT / "README.md"
SOURCE_WORKFLOW = (
    "StreamScapeTV/ci-workflows/.github/workflows/reusable-resolve-source.yml@main"
)
SOURCE_SHA = "${{ needs.source.outputs.source_sha }}"
VALIDATION_FAMILIES = {
    "apple.yml": {
        "workflow": "reusable-apple.yml",
        "paths": "TODO_PRODUCT_PATH/**",
        "inputs": {
            "validation_scope": "protected-full",
            "validation_plan_json": "TODO_APPLE_VALIDATION_PLAN_JSON",
            "working_directory": "TODO_APPLE_WORKING_DIRECTORY",
        },
    },
    "android.yml": {
        "workflow": "reusable-android.yml",
        "paths": "TODO_PRODUCT_PATH/**",
        "inputs": {
            "validation_scope": "TODO_ANDROID_VALIDATION_SCOPE",
            "working_directory": "TODO_ANDROID_WORKING_DIRECTORY",
            "validation_plan_json": "TODO_ANDROID_VALIDATION_PLAN_JSON",
        },
    },
    "node.yml": {
        "workflow": "reusable-node.yml",
        "paths": "TODO_PRODUCT_PATH/**",
        "inputs": {
            "validation_profile": "TODO_NODE_VALIDATION_PROFILE",
            "version_file": "TODO_NODE_VERSION_FILE",
            "working_directory": "TODO_NODE_WORKING_DIRECTORY",
            "install_profile": "TODO_NODE_INSTALL_PROFILE",
            "command_profile": "TODO_NODE_COMMAND_PROFILE",
        },
    },
    "python.yml": {
        "workflow": "reusable-python.yml",
        "paths": "TODO_PRODUCT_PATH/**",
        "inputs": {
            "validation_profile": "TODO_PYTHON_VALIDATION_PROFILE",
            "version_file": "TODO_PYTHON_VERSION_FILE",
            "working_directory": "TODO_PYTHON_WORKING_DIRECTORY",
            "command_profile": "TODO_PYTHON_COMMAND_PROFILE",
        },
    },
    "script.yml": {
        "workflow": "reusable-script.yml",
        "paths": "TODO_PRODUCT_PATH/**",
        "inputs": {
            "validation_profile": "TODO_SCRIPT_VALIDATION_PROFILE",
            "working_directory": "TODO_SCRIPT_WORKING_DIRECTORY",
            "script_path": "TODO_PRODUCT_SCRIPT_PATH",
        },
    },
    "helm.yml": {
        "workflow": "reusable-helm-validate.yml",
        "paths": "TODO_CHART_PATH/**",
        "inputs": {
            "product_id": "TODO_HELM_CALLER_IDENTITY",
            "values_profile": "TODO_HELM_VALUES_PROFILE",
            "policy_path": "TODO_HELM_POLICY_PATH",
        },
    },
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

    def test_validation_templates_keep_triggers_paths_and_ref_concurrency_consumer_owned(self) -> None:
        for filename, spec in VALIDATION_FAMILIES.items():
            with self.subTest(filename=filename):
                source, document = self._load(filename)
                triggers = document["on"]
                self.assertEqual(["TODO_PROTECTED_BRANCH"], triggers["pull_request"]["branches"])
                self.assertEqual(["TODO_PROTECTED_BRANCH"], triggers["push"]["branches"])
                expected_paths = [
                    spec["paths"],
                    ".github/workflows/TODO_CALLER_WORKFLOW.yml",
                    "TODO_PRODUCT_CONFIGURATION_PATH",
                ]
                self.assertEqual(expected_paths, triggers["pull_request"]["paths"])
                self.assertEqual(expected_paths, triggers["push"]["paths"])
                self.assertEqual(
                    {
                        "group": "${{ github.workflow }}-${{ github.ref }}",
                        "cancel-in-progress": "true",
                    },
                    document["concurrency"],
                )
                self.assertIn("choose product-owned paths OR paths-ignore", source)
                self.assertNotIn("TODO_OPTIONAL_FEATURE_BRANCH_GLOB", source)

    def test_validation_templates_compose_exact_source_then_live_main_api(self) -> None:
        for filename, spec in VALIDATION_FAMILIES.items():
            with self.subTest(filename=filename):
                source, document = self._load(filename)
                self.assertEqual(["source", "validate"], list(document["jobs"]))
                source_job = document["jobs"]["source"]
                self.assertEqual(SOURCE_WORKFLOW, source_job["uses"])
                self.assertEqual(
                    {"contents": "read", "pull-requests": "read"},
                    source_job["permissions"],
                )
                self.assertEqual(
                    {"source_mode": "auto", "expected_branch": "TODO_PROTECTED_BRANCH"},
                    source_job["with"],
                )

                validate = document["jobs"]["validate"]
                self.assertEqual("source", validate["needs"])
                self.assertEqual({"contents": "read"}, validate["permissions"])
                self.assertEqual(
                    "StreamScapeTV/ci-workflows/.github/workflows/"
                    f"{spec['workflow']}@main",
                    validate["uses"],
                )
                expected_inputs = {"admitted_sha": SOURCE_SHA, **spec["inputs"]}
                self.assertEqual(expected_inputs, validate["with"])
                for job in (source_job, validate):
                    self.assertNotIn("runs-on", job)
                    self.assertNotIn("steps", job)
                    self.assertNotIn("secrets", job)
                for forbidden in (
                    "secrets: inherit",
                    "actions/cache",
                    "upload-artifact",
                    "download-artifact",
                    "self-hosted",
                    "TODO_CENTRAL_REF",
                    "TODO_REUSABLE_",
                    "TODO_BOUNDED_",
                ):
                    self.assertNotIn(forbidden, source)

    def test_release_template_is_normal_tag_push_native_image_chart_call(self) -> None:
        source, document = self._load("release.yml")
        self.assertEqual({"push": {"tags": ["*.*.*"]}}, document["on"])
        self.assertEqual(
            {
                "group": "${{ github.workflow }}-${{ github.ref }}",
                "cancel-in-progress": "true",
            },
            document["concurrency"],
        )
        self.assertEqual(["release"], list(document["jobs"]))
        release = document["jobs"]["release"]
        self.assertEqual({"contents": "read"}, release["permissions"])
        self.assertEqual(
            "StreamScapeTV/ci-workflows/.github/workflows/reusable-native-image-chart.yml@main",
            release["uses"],
        )
        self.assertEqual(
            {
                "image_name": "TODO_IMAGE_NAME",
                "chart_name": "TODO_CHART_NAME",
                "chart_path": "TODO_CHART_PATH",
                "dockerfile_path": "TODO_DOCKERFILE_PATH",
                "build_context": "TODO_BUILD_CONTEXT",
            },
            release["with"],
        )
        self.assertEqual(
            {
                "registry_username": "${{ secrets.FORGEJO_REGISTRY_USERNAME }}",
                "registry_token": "${{ secrets.FORGEJO_REGISTRY_TOKEN }}",
            },
            release["secrets"],
        )
        for forbidden in (
            "workflow_dispatch",
            "existing-tag",
            "release_mode",
            "release_version",
            "release_source_sha",
            "request_id",
            "TODO_CENTRAL_REF",
        ):
            self.assertNotIn(forbidden, source)

    def test_templates_do_not_encode_application_repository_identity(self) -> None:
        for path in SKELETON_ROOT.glob("*.yml"):
            source = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_PRODUCT_IDENTITIES:
                with self.subTest(filename=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_readme_defines_direct_library_consumption_and_release_boundary(self) -> None:
        source = README.read_text(encoding="utf-8")
        for required in (
            "copy/adapt examples",
            "ordinary shared-library consumption",
            "not a\nper-product bootstrap or registration phase",
            "`@main`",
            "`@v1`",
            "Full 40-character Central SHAs",
            "optional unless a later reviewed policy explicitly requires them",
            "reusable-resolve-source.yml@main",
            "Exact source SHA is evidence identity, not concurrency identity.",
            "`[skip ci]`",
            "`[ci skip]`",
            "The product\ntag is release-version authority.",
            "does **not** expose `workflow_dispatch`",
            "not a Central registration key",
            "issue-dependency synchronization remains",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        self.assertNotIn("#283, #322, and #323", source)
        self.assertNotIn("active development/bootstrap", source)


if __name__ == "__main__":
    unittest.main()
