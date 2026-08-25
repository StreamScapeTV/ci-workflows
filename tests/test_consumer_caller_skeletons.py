from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKELETON_ROOT = ROOT / "docs" / "workflows" / "caller-skeletons"
PUBLIC_INDEX = ROOT / "contracts" / "public-workflows.json"
README = SKELETON_ROOT / "README.md"
SOURCE_WORKFLOW = (
    "StreamScapeTV/ci-workflows/.github/workflows/reusable-resolve-source.yml@main"
)
SOURCE_SHA = "${{ needs.source.outputs.source_sha }}"

VALIDATION_FAMILIES = {
    "apple.yml": {
        "api_name": "validation.apple",
        "paths": [
            "TODO_PRODUCT_PATH/**",
            ".github/workflows/TODO_CALLER_WORKFLOW.yml",
            "TODO_PRODUCT_CONFIGURATION_PATH",
        ],
        "portable_backend": False,
        "inputs": {
            "admitted_sha": SOURCE_SHA,
            "validation_scope": "protected-full",
            "validation_plan_json": "TODO_APPLE_VALIDATION_PLAN_JSON",
            "working_directory": "TODO_APPLE_WORKING_DIRECTORY",
        },
    },
    "android.yml": {
        "api_name": "validation.android",
        "paths": [
            "TODO_PRODUCT_PATH/**",
            ".github/workflows/TODO_CALLER_WORKFLOW.yml",
            "TODO_PRODUCT_CONFIGURATION_PATH",
        ],
        "portable_backend": False,
        "inputs": {
            "admitted_sha": SOURCE_SHA,
            "validation_scope": "TODO_ANDROID_VALIDATION_SCOPE",
            "working_directory": "TODO_ANDROID_WORKING_DIRECTORY",
            "validation_plan_json": "TODO_ANDROID_VALIDATION_PLAN_JSON",
        },
    },
    "node.yml": {
        "api_name": "validation.node",
        "paths": [
            "TODO_PRODUCT_PATH/**",
            ".github/workflows/TODO_CALLER_WORKFLOW.yml",
            "TODO_PRODUCT_CONFIGURATION_PATH",
        ],
        "portable_backend": True,
        "inputs": {
            "execution_backend": "TODO_EXECUTION_BACKEND",
            "admitted_sha": SOURCE_SHA,
            "validation_profile": "locked-node",
            "version_file": "TODO_NODE_VERSION_FILE",
            "working_directory": "TODO_NODE_WORKING_DIRECTORY",
            "install_profile": "npm-ci",
            "command_profile": "TODO_NODE_COMMAND_PROFILE",
        },
    },
    "python.yml": {
        "api_name": "validation.python",
        "paths": [
            "TODO_PRODUCT_PATH/**",
            ".github/workflows/TODO_CALLER_WORKFLOW.yml",
            "TODO_PRODUCT_CONFIGURATION_PATH",
        ],
        "portable_backend": True,
        "inputs": {
            "execution_backend": "TODO_EXECUTION_BACKEND",
            "admitted_sha": SOURCE_SHA,
            "validation_profile": "host",
            "python_version": "TODO_PYTHON_VERSION",
            "version_file": "TODO_PYTHON_VERSION_FILE",
            "dependency_file": "TODO_PYTHON_DEPENDENCY_FILE",
            "working_directory": "TODO_PYTHON_WORKING_DIRECTORY",
            "script_path": "TODO_PYTHON_SCRIPT_PATH",
        },
    },
    "script.yml": {
        "api_name": "validation.script",
        "paths": [
            "TODO_PRODUCT_PATH/**",
            ".github/workflows/TODO_CALLER_WORKFLOW.yml",
            "TODO_PRODUCT_CONFIGURATION_PATH",
        ],
        "portable_backend": True,
        "inputs": {
            "execution_backend": "TODO_EXECUTION_BACKEND",
            "admitted_sha": SOURCE_SHA,
            "validation_profile": "general",
            "working_directory": "TODO_SCRIPT_WORKING_DIRECTORY",
            "script_path": "TODO_PRODUCT_SCRIPT_PATH",
        },
    },
    "helm.yml": {
        "api_name": "validation.gitops",
        "paths": [
            "TODO_CHART_PATH/**",
            "TODO_GITOPS_CONSUMER_CONTRACT",
            ".github/workflows/TODO_CALLER_WORKFLOW.yml",
        ],
        "portable_backend": True,
        "inputs": {
            "execution_backend": "TODO_EXECUTION_BACKEND",
            "admitted_sha": SOURCE_SHA,
            "validation_profile": "helm-render",
            "consumer_contract": "TODO_GITOPS_CONSUMER_CONTRACT",
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
    @classmethod
    def setUpClass(cls) -> None:
        index = json.loads(PUBLIC_INDEX.read_text(encoding="utf-8"))
        cls.public_rows = {row["api_name"]: row for row in index["workflows"]}
        cls.contract_rows: dict[str, dict[str, object]] = {}
        for relative in index["fragment_contracts"]:
            fragment = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            for row in fragment["workflows"]:
                cls.contract_rows[row["api_name"]] = row

    def _load(self, filename: str) -> tuple[str, dict[str, object]]:
        source = (SKELETON_ROOT / filename).read_text(encoding="utf-8")
        document = yaml.load(source, Loader=yaml.BaseLoader)
        self.assertIsInstance(document, dict)
        return source, document

    def test_validation_templates_keep_product_triggers_paths_and_ref_concurrency(self) -> None:
        for filename, spec in VALIDATION_FAMILIES.items():
            with self.subTest(filename=filename):
                source, document = self._load(filename)
                triggers = document["on"]
                self.assertEqual(["TODO_PROTECTED_BRANCH"], triggers["pull_request"]["branches"])
                self.assertEqual(["TODO_PROTECTED_BRANCH"], triggers["push"]["branches"])
                self.assertEqual(spec["paths"], triggers["pull_request"]["paths"])
                self.assertEqual(spec["paths"], triggers["push"]["paths"])
                self.assertEqual(
                    {
                        "group": "${{ github.workflow }}-${{ github.ref }}",
                        "cancel-in-progress": "true",
                    },
                    document["concurrency"],
                )
                self.assertIn("choose product-owned paths OR paths-ignore", source)

    def test_validation_templates_compose_source_admission_with_supported_main_api(self) -> None:
        source_contract = self.contract_rows["source.resolve"]
        source_inputs = {row["name"] for row in source_contract["inputs"]}
        source_required = {
            row["name"] for row in source_contract["inputs"] if row["required"]
        }

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
                expected_source_inputs = {
                    "source_mode": "auto",
                    "expected_branch": "TODO_PROTECTED_BRANCH",
                }
                if spec["portable_backend"]:
                    expected_source_inputs["execution_backend"] = "TODO_EXECUTION_BACKEND"
                self.assertEqual(expected_source_inputs, source_job["with"])
                self.assertTrue(source_required <= set(source_job["with"]))
                self.assertTrue(set(source_job["with"]) <= source_inputs)

                api_name = spec["api_name"]
                public = self.public_rows[api_name]
                contract = self.contract_rows[api_name]
                validate = document["jobs"]["validate"]
                self.assertEqual("source", validate["needs"])
                self.assertEqual({"contents": "read"}, validate["permissions"])
                self.assertEqual(
                    f"StreamScapeTV/ci-workflows/{public['file']}@main",
                    validate["uses"],
                )
                self.assertEqual(spec["inputs"], validate["with"])

                contract_inputs = {row["name"] for row in contract["inputs"]}
                required = {
                    row["name"] for row in contract["inputs"] if row["required"]
                }
                self.assertTrue(required <= set(validate["with"]))
                self.assertTrue(set(validate["with"]) <= contract_inputs)

                for job in (source_job, validate):
                    self.assertNotIn("runs-on", job)
                    self.assertNotIn("steps", job)
                    self.assertNotIn("secrets", job)
                self.assertNotRegex(
                    source,
                    r"StreamScapeTV/ci-workflows/.+@[0-9a-f]{40}\b",
                )

    def test_portable_examples_use_one_bounded_backend_for_source_and_validation(self) -> None:
        for filename, spec in VALIDATION_FAMILIES.items():
            _, document = self._load(filename)
            source_with = document["jobs"]["source"]["with"]
            validate_with = document["jobs"]["validate"]["with"]
            with self.subTest(filename=filename):
                if spec["portable_backend"]:
                    self.assertEqual(
                        "TODO_EXECUTION_BACKEND", source_with["execution_backend"]
                    )
                    self.assertEqual(
                        "TODO_EXECUTION_BACKEND", validate_with["execution_backend"]
                    )
                else:
                    self.assertNotIn("execution_backend", source_with)
                    self.assertNotIn("execution_backend", validate_with)

    def test_helm_example_uses_supported_gitops_helm_render_contract(self) -> None:
        source, document = self._load("helm.yml")
        validate = document["jobs"]["validate"]
        self.assertEqual(
            "StreamScapeTV/ci-workflows/.github/workflows/"
            "reusable-gitops-validation.yml@main",
            validate["uses"],
        )
        self.assertEqual("helm-render", validate["with"]["validation_profile"])
        self.assertEqual(
            "TODO_GITOPS_CONSUMER_CONTRACT", validate["with"]["consumer_contract"]
        )
        self.assertNotIn("product_id", source)
        self.assertNotIn("reusable-helm-validate.yml", source)

    def test_release_template_is_normal_tag_push_native_image_chart_call(self) -> None:
        source, document = self._load("release.yml")
        public = self.public_rows["release.native-image-chart"]
        contract = self.contract_rows["release.native-image-chart"]

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
            f"StreamScapeTV/ci-workflows/{public['file']}@main",
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
        self.assertEqual(set(contract["secrets"]), set(release["secrets"]))
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

    def test_templates_do_not_encode_private_products_or_ci_ceremony(self) -> None:
        for path in SKELETON_ROOT.glob("*.yml"):
            source = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_PRODUCT_IDENTITIES:
                with self.subTest(filename=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)
            for forbidden in (
                "secrets: inherit",
                "self-hosted",
                "TODO_CENTRAL_REF",
                "request_id:",
                "release_source_sha:",
            ):
                with self.subTest(filename=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_readme_defines_current_library_backend_helm_and_release_model(self) -> None:
        source = README.read_text(encoding="utf-8")
        for required in (
            "copy/adapt examples",
            "CI_WORKFLOWS.yml",
            "ordinary shared-library consumption",
            "`@main`",
            "`@v1`",
            "not the normal adoption ceremony",
            "`${{ github.workflow }}-${{ github.ref }}`",
            "`execution_backend: organization | github-hosted`",
            "same\n`TODO_EXECUTION_BACKEND`",
            "`organization` is the backwards-compatible default",
            "`github-hosted` never\nfalls back to organization capacity",
            "historical public `helm.validate` facade is no longer",
            "`validation.gitops`",
            "helm-render",
            "The normal caller does not expose `workflow_dispatch`",
            "`release.public-native-image-chart`",
            "`[skip ci]` / `[ci skip]`",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
