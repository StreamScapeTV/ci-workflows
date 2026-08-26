from __future__ import annotations
import ast, base64, gzip, hashlib, importlib.util, unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "ci_workflows"
BASELINE = "54756ade2a502ae83157b975513ed60838c407e2"
EXPECTED = 162
KEEP = set("""__init__.py android.py android_policy.py apple.py apple_primitives.py central_profile.py ci_private.py ciw.py device.py device_lock.py distribution_primitives.py evidence.py flutter.py gitops.py gitops_runtime.py gradle_dependency_warm.py gradle_maven_publish.py gradle_seed.py gradle_seed_internal.py helm.py helm_registry.py language_primitives.py node.py oci.py oci_registry_download.py package_primitives.py packaging_primitives.py policy.py public_api.py public_api_contract.py python.py python_execution.py release_tag_authority.py runner_images.py runners.py source.py source_checkout.py tooling.py validation_harness.py validation_runtime.py workspace.py""".split())
COMPAT = set("""ciw_android.py ciw_android_completion.py ciw_apple.py ciw_device.py ciw_device_lock.py ciw_flutter.py ciw_gitops.py ciw_gradle_seed.py ciw_helm.py ciw_native.py ciw_network.py ciw_node.py ciw_oci.py ciw_packages.py ciw_python.py ciw_web.py device_execution.py devices.py foundation_cli.py private_release_asset_action.py source_cli.py""".split())
META = {
    'android': ('src/ci_workflows/android.py', 'Android validation/build/test planning and execution.'),
    'apple': ('src/ci_workflows/apple.py', 'Apple validation/build/test planning and execution.'),
    'private_ci': ('src/ci_workflows/ci_private.py', 'Opaque private-CI profile projection and product-neutral trusted execution.'),
    'agent_state': ('src/ci_workflows/agent_state.py', 'CI-only Agent State lifecycle request/evidence operations.'),
    'r2': ('src/ci_workflows/r2.py', 'Cloudflare R2 private diagnostic storage, read-back, compression, digest, and receipt helpers.'),
    'core': ('src/ci_workflows/ciw.py', 'Product-neutral CIW command registry, shared types, language primitives, and generated foundation documentation.'),
    'device': ('src/ci_workflows/device.py', 'Physical/synthetic device admission, planning, lifecycle, execution, evidence, and fencing.'),
    'distribution': ('src/ci_workflows/distribution_primitives.py', 'Signing and store-distribution primitives.'),
    'flutter': ('src/ci_workflows/flutter.py', 'Flutter validation planning and execution.'),
    'gitops': ('src/ci_workflows/gitops.py', 'GitOps source/render/SOPS/Kustomize/Helm validation.'),
    'gradle': ('src/ci_workflows/gradle_seed.py', 'Gradle dependency warming, Maven publication, and cache-seed synchronization.'),
    'helm': ('src/ci_workflows/helm.py', 'Helm validation, packaging, dependency policy, registry publication, and read-back.'),
    'native': ('src/ci_workflows/native.py', 'Native/CMake validation primitives and adapter.'),
    'network': ('src/ci_workflows/network.py', 'Bounded HTTP/download/archive network primitives and adapter.'),
    'node': ('src/ci_workflows/node.py', 'Node.js/npm validation planning and execution.'),
    'oci': ('src/ci_workflows/oci.py', 'OCI acquisition, build, inspection, reproducibility, and publication policy.'),
    'package': ('src/ci_workflows/package_primitives.py', 'Language package build/inspection/publication primitives.'),
    'packaging': ('src/ci_workflows/packaging_primitives.py', 'Container and Helm packaging tool primitives.'),
    'policy': ('src/ci_workflows/policy.py', 'Repository security/policy checks, readability rules, and bounded evidence projection.'),
    'python': ('src/ci_workflows/python.py', 'Python validation planning and execution across hosted and organization backends.'),
    'release': ('src/ci_workflows/release.py', 'Shared Git tag, release, private release-asset, and publication mechanics.'),
    'runners': ('src/ci_workflows/runners.py', 'Semantic runner profiles, backend resolution, and runner-image product contracts.'),
    'service': ('src/ci_workflows/service.py', 'Service/Compose/PostgreSQL validation primitives and Service runner smoke lifecycle.'),
    'source': ('src/ci_workflows/source.py', 'GitHub source resolution, trust admission, exact checkout, dependencies, and repository-token acquisition.'),
    'tooling': ('src/ci_workflows/tooling.py', 'Pinned tooling acquisition, verification, and capability inspection.'),
    'validation': ('src/ci_workflows/validation_harness.py', 'Repository validation harness, graph/model/policy/expression checks, and locked validation runtime.'),
    'public_api': ('src/ci_workflows/public_api.py', 'Public reusable-workflow API registry, compatibility contract, and public CI admission policy.'),
    'web': ('src/ci_workflows/web.py', 'Static-web build/output verification and deployment primitives.'),
    'workspace': ('src/ci_workflows/workspace.py', 'Isolated CI workspace/state setup, cleanup, residue, and core runtime primitives.'),
}
MULTI = {
    'android': ('consolidation-required', 'Contract/execution/metrics/types are fragmented; retain policy as the second boundary.', 'android.py + android_policy.py'),
    'apple': ('consolidation-required', 'Contracts/execution/simulator/types are fragmented; reusable primitives are the second boundary.', 'apple.py + apple_primitives.py'),
    'private_ci': ('consolidation-required', 'Generic private CI still depends on Apple-private internals; preserve profile parsing as the second boundary.', 'ci_private.py + central_profile.py'),
    'core': ('consolidation-required', 'Registry/types/foundation/docs are fragmented; keep the registry and language primitives.', 'ciw.py + language_primitives.py'),
    'device': ('consolidation-required', 'Planning/lifecycle/evidence/types are fragmented; exact device fencing is the second security boundary.', 'device.py + device_lock.py'),
    'flutter': ('consolidation-required', 'Contract/execution/types/CLI should converge on the Flutter provider.', 'flutter.py'),
    'gitops': ('consolidation-required', 'Source/plan/render/primitives/types are fragmented; runtime isolation is the second boundary.', 'gitops.py + gitops_runtime.py'),
    'gradle': ('justified', 'Dependency warm, Maven publication, and cache-seed transport have independent privilege/lifecycle boundaries; do not combine authority automatically.', 'owner review before flattening'),
    'helm': ('consolidation-required', 'Validation/packaging/policy/runtime/types are fragmented; registry I/O is the second boundary.', 'helm.py + helm_registry.py'),
    'node': ('consolidation-required', 'Contract/execution/types/CLI should converge on the Node provider.', 'node.py'),
    'oci': ('consolidation-required', 'Supply-chain logic is heavily fragmented; registry acquisition remains the second isolation boundary.', 'oci.py + oci_registry_download.py'),
    'policy': ('consolidation-required', 'Policy/readability/evidence are split; evidence projection is the second boundary.', 'policy.py + evidence.py'),
    'python': ('consolidation-required', 'Contract/host/docker/types/CLI are fragmented; backend-neutral execution is the second boundary.', 'python.py + python_execution.py'),
    'release': ('consolidation-required', 'Release primitives/assets/action are fragmented; tag authority is the second security boundary.', 'release.py + release_tag_authority.py'),
    'runners': ('consolidation-required', 'Selection/backend mapping/image policy span three modules; runner-image policy remains the second boundary.', 'runners.py + runner_images.py'),
    'service': ('consolidation-required', 'Compose/service/runner-smoke behavior is three modules for one concern.', 'service.py'),
    'source': ('consolidation-required', 'Admission/evidence/GitHub/private-source/dependency/token logic is fragmented; exact checkout is the second credential boundary.', 'source.py + source_checkout.py'),
    'validation': ('consolidation-required', 'Contracts/graph/helpers/model/policy/expressions are fragmented; locked runtime is the second boundary.', 'validation_harness.py + validation_runtime.py'),
    'public_api': ('consolidation-required', 'Registry/contract/admission are three files; fold admission into the contract/policy boundary.', 'public_api.py + public_api_contract.py'),
}

def _load_helpers():
    path = ROOT / "scripts" / "ci" / "python_inventory_bootstrap.py"
    spec = importlib.util.spec_from_file_location("_python_inventory_bootstrap", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def _groups(helper):
    groups = {key: list(value) for key, value in helper.DOMAIN_GROUPS.items() if key != "broker"}
    groups["private_ci"] = ["central_profile.py", "ci_private.py", "ci_private_apple.py"]
    groups["agent_state"] = ["ci_lifecycle.py"]
    groups["r2"] = ["r2_diagnostics.py"]
    groups["core"].remove("execution_backends.py")
    groups["runners"].append("execution_backends.py")
    groups["package"].remove("packaging_primitives.py")
    groups["packaging"] = ["packaging_primitives.py"]
    for name in ["public_api.py", "public_api_contract.py", "public_ci_admission.py"]:
        groups["validation"].remove(name)
    groups["public_api"] = ["public_api.py", "public_api_contract.py", "public_ci_admission.py"]
    return groups

def _sentence(text, fallback):
    value = " ".join((text or "").split()) or fallback
    cut = value.find(". ")
    if cut >= 0:
        value = value[:cut + 1]
    if len(value) > 200:
        value = value[:197].rstrip() + "..."
    if value[-1] not in ".!?":
        value += "."
    return value

def _sig(node):
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    suffix = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix}{node.name}({ast.unparse(node.args)}){suffix}"

def _decls(tree, summary):
    classes, functions = [], []
    trivial = {"__init__", "__repr__", "__str__", "__hash__", "__eq__", "__post_init__"}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name not in trivial:
                    methods.append({
                        "name": child.name,
                        "signature": _sig(child),
                        "purpose": _sentence(ast.get_docstring(child, clean=True), f"Implements {node.name}.{child.name} behavior for {summary.rstrip('.')}"),
                    })
            classes.append({
                "name": node.name,
                "purpose": _sentence(ast.get_docstring(node, clean=True), f"Defines {node.name} for {summary.rstrip('.')}"),
                "methods": methods,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                "name": node.name,
                "signature": _sig(node),
                "purpose": _sentence(ast.get_docstring(node, clean=True), f"Implements {node.name} for {summary.rstrip('.')}"),
            })
    return classes, functions

def _deps(tree, names):
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            candidates = []
            if node.level and node.module:
                candidates.append(node.module.split(".")[0])
            elif node.module and node.module.startswith("ci_workflows."):
                candidates.append(node.module.removeprefix("ci_workflows.").split(".")[0])
            elif node.level and not node.module:
                candidates.extend(alias.name.split(".")[0] for alias in node.names)
            result.update(item for item in candidates if item in names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ci_workflows."):
                    item = alias.name.removeprefix("ci_workflows.").split(".")[0]
                    if item in names:
                        result.add(item)
    return sorted(result)

def _build():
    helper = _load_helpers()
    groups = _groups(helper)
    paths = sorted(SOURCE.glob("*.py"))
    names = {path.name for path in paths}
    classified = {name for values in groups.values() for name in values}
    assert len(paths) == EXPECTED and len(names) == EXPECTED
    assert classified == names
    assert not (KEEP & COMPAT)
    assert KEEP | COMPAT <= names
    module_names = {path.stem for path in paths if path.name != "__init__.py"}
    domains = {}
    for domain, filenames in groups.items():
        target, responsibility = META[domain]
        files = []
        for filename in sorted(filenames):
            path = SOURCE / filename
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            summary = _sentence(ast.get_docstring(tree, clean=True), f"Implementation module {filename}")
            classes, functions = _decls(tree, summary)
            status = "keep" if filename in KEEP else "compatibility" if filename in COMPAT else "consolidate"
            files.append({
                "path": path.relative_to(ROOT).as_posix(),
                "responsibility": summary,
                "status": status,
                "classes": classes,
                "functions": functions,
                "internal_dependencies": _deps(tree, module_names),
            })
        domains[domain] = {"target_module": target, "responsibility": responsibility, "files": files}
    multi = []
    for domain, filenames in groups.items():
        if len(filenames) <= 2:
            continue
        assessment, reason, target = MULTI[domain]
        multi.append({
            "domain": domain,
            "current_file_count": len(filenames),
            "files": [f"src/ci_workflows/{name}" for name in sorted(filenames)],
            "assessment": assessment,
            "reason": reason,
            "intended_target": target,
        })
    return {
        "version": 1,
        "source_root": "src/ci_workflows",
        "generated_from_sha": BASELINE,
        "inventory_complete": True,
        "new_module_creation_locked": False,
        "summary": {
            "python_files_discovered": EXPECTED,
            "status": "complete-human-reviewed-ast-verified",
            "note": "The original issue estimate and #589 bootstrap count were stale; protected main contains exactly 162 Python implementation files after #576 removed seven superseded broker/relay modules.",
        },
        "rules": [
            "Read this inventory before creating or substantially modifying Python implementation.",
            "Reuse or refactor an existing owner before creating a parallel module/function/class.",
            "Cross-cutting infrastructure belongs to product-neutral domain providers.",
            "Default to one cohesive implementation module per domain; use a second only for a real boundary.",
            "More than two implementation modules for one domain/build/release concern requires owner review first.",
            "Every finalized Python add/remove/rename/move/material responsibility change updates this inventory.",
        ],
        "status_values": ["keep", "consolidate", "compatibility", "remove-after-migration", "review"],
        "review": {
            "mode": "human-reviewed-classification-with-deterministic-ast-coverage",
            "issue": 590,
            "notes": [
                "Domain ownership is the reviewed #590 map derived from the prior exact-tree review and explicitly reconciled to #576 removals; no filename-prefix fallback exists.",
                "Keep/compatibility sets are explicit. Every remaining reviewed module is deliberately classified consolidate.",
                "File responsibilities come from module docstrings; declaration names/signatures and internal dependency direction come from deterministic AST inspection.",
                "Direct class methods are inventoried except trivial data-model dunders.",
            ],
        },
        "multi_file_domains": multi,
        "domains": domains,
    }

class CurrentPythonInventoryBootstrapTests(unittest.TestCase):
    def test_emit_current_reviewed_inventory_payload(self):
        payload = _build()
        rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=180).encode()
        encoded = base64.b64encode(gzip.compress(rendered, compresslevel=9, mtime=0)).decode()
        print("PYTHON_INVENTORY_COMPACT_GZIP_BASE64_BEGIN")
        for i, start in enumerate(range(0, len(encoded), 16000)):
            print(f"{i:04d}:{encoded[start:start+16000]}")
        print("PYTHON_INVENTORY_COMPACT_GZIP_BASE64_END")
        print("PYTHON_INVENTORY_SHA256=" + hashlib.sha256(rendered).hexdigest())
        print("PYTHON_INVENTORY_RENDERED_BYTES=" + str(len(rendered)))
        print("PYTHON_INVENTORY_ENTRIES=" + str(sum(len(v["files"]) for v in payload["domains"].values())))
