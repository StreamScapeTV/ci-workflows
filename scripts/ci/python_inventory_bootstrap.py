from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "ci_workflows"
REVIEWED_SOURCE_COMMIT = "986ed0a29ee171841566bcf22d7b58c97c8fdbb4"
EXPECTED_FILE_COUNT = 169

STATUS_VALUES = [
    "facade",
    "cli-adapter",
    "contract-policy",
    "typed-model",
    "execution-runtime",
    "primitive-library",
    "compatibility-wrapper",
    "documentation-renderer",
    "broker-runtime",
    "validation-harness",
]
DOMAIN_VALUES = [
    "android",
    "apple",
    "broker",
    "core",
    "device",
    "distribution",
    "flutter",
    "gitops",
    "gradle",
    "helm",
    "native",
    "network",
    "node",
    "oci",
    "package",
    "policy",
    "python",
    "release",
    "runner",
    "service",
    "source",
    "tooling",
    "validation",
    "web",
    "workspace",
]

# These groups are deliberately explicit. They are the human-reviewed responsibility
# classification for the exact source tree, not a filename-prefix classifier.
DOMAIN_GROUPS = {
    "android": "android.py android_contract.py android_execution.py android_policy.py android_resource_metrics.py android_types.py ciw_android.py ciw_android_completion.py".split(),
    "apple": "apple.py apple_contract.py apple_contract_fragments.py apple_execution.py apple_multistage.py apple_plan_guard.py apple_primitives.py apple_simulator_confidence.py apple_simulator_script.py apple_types.py ciw_apple.py".split(),
    "broker": "central_profile.py ci_broker.py ci_broker_action.py ci_broker_dependencies.py ci_broker_fallback.py ci_broker_start_guard.py ci_lifecycle.py ci_private.py ci_private_apple.py ci_relay.py ci_relay_server.py r2_diagnostics.py".split(),
    "core": "__init__.py ciw.py ciw_types.py foundation_cli.py foundation_docs.py foundation_types.py execution_backends.py language_primitives.py ciw_docs.py".split(),
    "device": "device.py device_admission.py device_cleanup.py device_contract.py device_contract_common.py device_evidence.py device_execution.py device_inventory.py device_lifecycle.py device_live.py device_live_safe.py device_lock.py device_plan_contract.py device_primitives.py device_profile_contract.py device_test_lock.py device_types.py devices.py ciw_device.py ciw_device_lock.py physical_log_policy.py".split(),
    "distribution": "distribution_primitives.py".split(),
    "flutter": "flutter.py flutter_contract.py flutter_execution.py flutter_types.py ciw_flutter.py".split(),
    "gitops": "gitops.py gitops_composition.py gitops_contract.py gitops_execution.py gitops_plan.py gitops_primitives.py gitops_render.py gitops_runtime.py gitops_source.py gitops_types.py ciw_gitops.py".split(),
    "gradle": "gradle_dependency_warm.py gradle_maven_publish.py gradle_seed.py gradle_seed_internal.py ciw_gradle_seed.py".split(),
    "helm": "helm.py helm_archive.py helm_contract.py helm_dependency_policy.py helm_execution.py helm_manifest.py helm_measurement.py helm_policy.py helm_product_layout.py helm_registry.py helm_release.py helm_runtime.py helm_simple.py helm_types.py helm_upstream_policy.py ciw_helm.py".split(),
    "native": "native_primitives.py ciw_native.py".split(),
    "network": "network_primitives.py ciw_network.py".split(),
    "node": "node.py node_contract.py node_execution.py node_types.py ciw_node.py".split(),
    "oci": "oci.py oci_base_inspection.py oci_contract.py oci_execution.py oci_execution_safe.py oci_input_contract.py oci_input_download.py oci_publish.py oci_publish_contract.py oci_registry_download.py oci_reproducibility.py oci_types.py ciw_oci.py".split(),
    "package": "package_primitives.py packaging_primitives.py ciw_packages.py".split(),
    "policy": "policy.py readability.py evidence.py".split(),
    "python": "python.py python_contract.py python_docker_execution.py python_execution.py python_host_execution.py python_types.py ciw_python.py".split(),
    "release": "release_primitives.py release_tag_authority.py private_release_asset.py private_release_asset_action.py".split(),
    "runner": "runner_images.py runners.py".split(),
    "service": "service_compose_primitives.py service_primitives.py service_runner_smoke.py".split(),
    "source": "source.py source_admission.py source_checkout.py source_cli.py source_evidence.py source_github.py source_types.py private_source.py dependencies.py github_app_token.py".split(),
    "tooling": "tooling.py".split(),
    "validation": "public_api.py public_api_contract.py public_ci_admission.py validation_contracts.py validation_expression_contexts.py validation_graph.py validation_harness.py validation_helpers.py validation_model.py validation_policy.py validation_runtime.py".split(),
    "web": "web_primitives.py ciw_web.py".split(),
    "workspace": "workspace.py runtime_primitives.py".split(),
}
STATUS_GROUPS = {
    "broker-runtime": "central_profile.py ci_broker.py ci_broker_action.py ci_broker_dependencies.py ci_broker_fallback.py ci_broker_start_guard.py ci_lifecycle.py ci_private.py ci_private_apple.py ci_relay.py ci_relay_server.py r2_diagnostics.py".split(),
    "cli-adapter": "ciw.py ciw_android.py ciw_android_completion.py ciw_apple.py ciw_device.py ciw_device_lock.py ciw_flutter.py ciw_gitops.py ciw_gradle_seed.py ciw_helm.py ciw_native.py ciw_network.py ciw_node.py ciw_oci.py ciw_packages.py ciw_python.py ciw_web.py foundation_cli.py private_release_asset_action.py source_cli.py".split(),
    "compatibility-wrapper": "device_execution.py devices.py".split(),
    "contract-policy": "android_contract.py android_policy.py apple_contract.py apple_contract_fragments.py apple_plan_guard.py apple_simulator_confidence.py device_admission.py device_cleanup.py device_contract.py device_contract_common.py device_evidence.py device_inventory.py device_plan_contract.py device_profile_contract.py execution_backends.py flutter_contract.py gitops_composition.py gitops_contract.py gitops_plan.py gitops_source.py helm_contract.py helm_dependency_policy.py helm_policy.py helm_product_layout.py helm_upstream_policy.py node_contract.py oci_contract.py oci_execution_safe.py oci_input_contract.py oci_publish_contract.py physical_log_policy.py policy.py public_api_contract.py public_ci_admission.py python_contract.py readability.py release_tag_authority.py runner_images.py runners.py source_admission.py source_evidence.py source_types.py".split(),
    "documentation-renderer": "ciw_docs.py foundation_docs.py".split(),
    "execution-runtime": "android_execution.py android_resource_metrics.py apple_execution.py apple_multistage.py apple_simulator_script.py dependencies.py device_lifecycle.py device_live.py device_live_safe.py device_lock.py device_test_lock.py flutter_execution.py github_app_token.py gitops_execution.py gitops_render.py gitops_runtime.py gradle_dependency_warm.py gradle_maven_publish.py gradle_seed_internal.py helm_archive.py helm_execution.py helm_manifest.py helm_measurement.py helm_registry.py helm_release.py helm_runtime.py helm_simple.py node_execution.py oci_base_inspection.py oci_execution.py oci_input_download.py oci_publish.py oci_registry_download.py oci_reproducibility.py private_release_asset.py private_source.py python_docker_execution.py python_execution.py python_host_execution.py service_runner_smoke.py source_checkout.py source_github.py workspace.py".split(),
    "facade": "__init__.py android.py apple.py device.py flutter.py gitops.py helm.py node.py oci.py public_api.py python.py source.py".split(),
    "primitive-library": "apple_primitives.py device_primitives.py distribution_primitives.py evidence.py gitops_primitives.py gradle_seed.py language_primitives.py native_primitives.py network_primitives.py package_primitives.py packaging_primitives.py release_primitives.py runtime_primitives.py service_compose_primitives.py service_primitives.py tooling.py web_primitives.py".split(),
    "typed-model": "android_types.py apple_types.py ciw_types.py device_types.py flutter_types.py foundation_types.py gitops_types.py helm_types.py node_types.py oci_types.py python_types.py".split(),
    "validation-harness": "validation_contracts.py validation_expression_contexts.py validation_graph.py validation_harness.py validation_helpers.py validation_model.py validation_policy.py validation_runtime.py".split(),
}


def _reviewed_map(groups: dict[str, list[str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for classification, names in groups.items():
        for name in names:
            if name in result:
                raise AssertionError(f"duplicate reviewed classification for {name}")
            result[name] = classification
    return result


def _doc(node: ast.AST) -> str:
    return " ".join((ast.get_docstring(node, clean=True) or "").split())


def _summary(tree: ast.Module) -> str:
    text = _doc(tree)
    if not text:
        raise AssertionError("every reviewed module must carry a module docstring")
    if len(text) <= 360:
        return text
    boundary = text.rfind(". ", 0, 357)
    if boundary >= 120:
        return text[: boundary + 1]
    return text[:357].rstrip() + "..."


def _short_purpose(node: ast.AST, *, kind: str, name: str, module_summary: str) -> str:
    text = _doc(node)
    if text:
        if len(text) <= 280:
            return text
        boundary = text.rfind(". ", 0, 277)
        if boundary >= 80:
            return text[: boundary + 1]
        return text[:277].rstrip() + "..."
    return f"Undocumented internal {kind} `{name}` supporting the reviewed module responsibility: {module_summary}"


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    arguments = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix}{node.name}({arguments}){returns}"


def _class_signature(node: ast.ClassDef) -> str:
    arguments = [ast.unparse(base) for base in node.bases]
    arguments.extend(
        f"{keyword.arg}={ast.unparse(keyword.value)}" if keyword.arg else f"**{ast.unparse(keyword.value)}"
        for keyword in node.keywords
    )
    suffix = f"({', '.join(arguments)})" if arguments else ""
    return f"class {node.name}{suffix}"


def _top_level(tree: ast.Module, module_summary: str) -> tuple[list[dict[str, str]], dict[str, list[dict[str, str]]]]:
    declarations: list[dict[str, str]] = []
    class_methods: dict[str, list[dict[str, str]]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            declarations.append(
                {
                    "kind": "class",
                    "name": node.name,
                    "signature": _class_signature(node),
                    "purpose": _short_purpose(node, kind="class", name=node.name, module_summary=module_summary),
                }
            )
            methods: list[dict[str, str]] = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(
                        {
                            "name": child.name,
                            "signature": _function_signature(child),
                            "purpose": _short_purpose(
                                child,
                                kind="method",
                                name=f"{node.name}.{child.name}",
                                module_summary=module_summary,
                            ),
                        }
                    )
            if methods:
                class_methods[node.name] = methods
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            declarations.append(
                {
                    "kind": "function",
                    "name": node.name,
                    "signature": _function_signature(node),
                    "purpose": _short_purpose(node, kind="function", name=node.name, module_summary=module_summary),
                }
            )
    return declarations, class_methods


def _internal_dependencies(tree: ast.Module, module_names: set[str]) -> list[str]:
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    candidate = node.module.split(".")[0]
                    if candidate in module_names:
                        dependencies.add(candidate)
                else:
                    for alias in node.names:
                        candidate = alias.name.split(".")[0]
                        if candidate in module_names:
                            dependencies.add(candidate)
            elif node.module == "ci_workflows":
                for alias in node.names:
                    candidate = alias.name.split(".")[0]
                    if candidate in module_names:
                        dependencies.add(candidate)
            elif node.module and node.module.startswith("ci_workflows."):
                candidate = node.module.removeprefix("ci_workflows.").split(".")[0]
                if candidate in module_names:
                    dependencies.add(candidate)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ci_workflows."):
                    candidate = alias.name.removeprefix("ci_workflows.").split(".")[0]
                    if candidate in module_names:
                        dependencies.add(candidate)
    return sorted(dependencies)


def _build_inventory() -> dict[str, object]:
    paths = sorted(SOURCE_ROOT.rglob("*.py"))
    names = {path.name for path in paths}
    if len(paths) != EXPECTED_FILE_COUNT or len(names) != EXPECTED_FILE_COUNT:
        raise AssertionError(f"expected {EXPECTED_FILE_COUNT} unique Python modules, found {len(paths)}")

    domain_by_name = _reviewed_map(DOMAIN_GROUPS)
    status_by_name = _reviewed_map(STATUS_GROUPS)
    if set(domain_by_name) != names:
        raise AssertionError(f"domain review drift: missing={sorted(names - set(domain_by_name))}, extra={sorted(set(domain_by_name) - names)}")
    if set(status_by_name) != names:
        raise AssertionError(f"status review drift: missing={sorted(names - set(status_by_name))}, extra={sorted(set(status_by_name) - names)}")

    module_names = {path.stem for path in paths if path.name != "__init__.py"}
    entries: list[dict[str, object]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_summary = _summary(tree)
        top_level, class_methods = _top_level(tree, module_summary)
        entries.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "domain": domain_by_name[path.name],
                "status": status_by_name[path.name],
                "summary": module_summary,
                "top_level": top_level,
                "class_methods": class_methods,
                "internal_dependencies": _internal_dependencies(tree, module_names),
            }
        )

    return {
        "version": 1,
        "scope": "src/ci_workflows",
        "generated_from": REVIEWED_SOURCE_COMMIT,
        "expected_python_files": EXPECTED_FILE_COUNT,
        "review": {
            "mode": "human-reviewed",
            "issue": 590,
            "notes": [
                "The issue's original 173-file estimate was stale; the exact reviewed source contains 169 Python files.",
                "Domain and status are explicit reviewed path groups; no filename-prefix fallback is permitted.",
                "Module summaries are reviewed from source docstrings and AST/import direction at the exact source baseline.",
                "Definition purposes prefer source docstrings; undocumented internals use a neutral fallback tied to the reviewed module responsibility.",
                "Every direct class method is recorded so behavior-bearing methods cannot drift silently.",
            ],
        },
        "status_values": STATUS_VALUES,
        "domain_values": DOMAIN_VALUES,
        "entries": entries,
    }


class PythonInventoryBootstrapTests(unittest.TestCase):
    def test_emit_reviewed_inventory_payload(self) -> None:
        payload = _build_inventory()
        rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=160).encode("utf-8")
        digest = hashlib.sha256(rendered).hexdigest()
        encoded = base64.b64encode(gzip.compress(rendered, compresslevel=9, mtime=0)).decode("ascii")

        print("PYTHON_INVENTORY_GZIP_BASE64_BEGIN")
        for index, offset in enumerate(range(0, len(encoded), 900)):
            print(f"{index:04d}:{encoded[offset:offset + 900]}")
        print("PYTHON_INVENTORY_GZIP_BASE64_END")
        print(f"PYTHON_INVENTORY_SHA256={digest}")
        print(f"PYTHON_INVENTORY_RENDERED_BYTES={len(rendered)}")
        print(f"PYTHON_INVENTORY_ENTRIES={len(payload['entries'])}")

        self.assertEqual(EXPECTED_FILE_COUNT, len(payload["entries"]))
        self.assertEqual(STATUS_VALUES, list(dict.fromkeys(STATUS_VALUES)))
        self.assertEqual(DOMAIN_VALUES, list(dict.fromkeys(DOMAIN_VALUES)))


if __name__ == "__main__":
    unittest.main()
