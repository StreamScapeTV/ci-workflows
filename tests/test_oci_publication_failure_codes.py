from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_MODULES = (
    "src/ci_workflows/oci_publish.py",
    "src/ci_workflows/oci_publish_assertions.py",
    "src/ci_workflows/oci_publish_contract.py",
    "src/ci_workflows/oci_publish_guards.py",
    "src/ci_workflows/oci_supply_evidence.py",
    "src/ci_workflows/ciw_oci.py",
)
PUBLICATION_FAILURE_CODES = frozenset(
    {
        "assertion_failed",
        "build_evidence_mismatch",
        "build_evidence_missing",
        "capacity_host_invalid",
        "capacity_identity_invalid",
        "capacity_marker_invalid",
        "capacity_mount_invalid",
        "capacity_root_invalid",
        "cleanup_failed",
        "engine_isolation_failed",
        "immutable_reference_conflict",
        "invalid_contract",
        "invalid_platform_set",
        "invalid_product",
        "invalid_request",
        "invalid_source",
        "invalid_version",
        "metadata_mismatch",
        "mutable_tag_forbidden",
        "oci_digest_mismatch",
        "oci_layout_malformed",
        "platform_mismatch",
        "platform_override_forbidden",
        "publication_dependency_missing",
        "publication_not_ready",
        "publication_output_invalid",
        "publication_ref_forbidden",
        "publication_state_missing",
        "publication_untrusted",
        "registry_auth_failed",
        "registry_auth_invalid",
        "registry_auth_missing",
        "registry_copy_failed",
        "registry_digest_mismatch",
        "registry_inspection_failed",
        "registry_readback_mismatch",
        "registry_tool_unavailable",
        "registry_write_policy_not_ready",
        "release_authority_invalid",
        "release_authority_mismatch",
        "remote_reference_missing",
        "residue_detected",
        "terminal_contract_mismatch",
        "terminal_evidence_invalid",
        "terminal_toolchain_invalid",
        "terminal_toolchain_mismatch",
        "unsupported_consumer",
        "unsupported_product",
    }
)
DYNAMIC_PASSTHROUGH_CODES = frozenset(
    {
        "capacity_host_invalid",
        "capacity_marker_invalid",
        "capacity_mount_invalid",
    }
)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _literal_string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _module_failure_codes(path: Path) -> set[str]:
    """Collect stable literals routed into OciPublishError by public adapters."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    codes: set[str] = set()
    code_argument = {
        "OciPublishError": 0,
        "_encoded_mapping": 1,
        "_mapping": 1,
        "_require": 1,
        "_strings": 1,
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            position = code_argument.get(name)
            if position is not None and len(node.args) > position:
                code = _literal_string(node.args[position])
                if code is not None:
                    codes.add(code)
            for keyword in node.keywords:
                if keyword.arg == "code":
                    code = _literal_string(keyword.value)
                    if code is not None:
                        codes.add(code)
            if name == "getattr" and len(node.args) >= 3:
                attribute = _literal_string(node.args[1])
                default = _literal_string(node.args[2])
                if attribute == "code" and default is not None:
                    codes.add(default)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != "_require" or not node.args.defaults:
                continue
            code = _literal_string(node.args.defaults[-1])
            if code is not None:
                codes.add(code)
    return codes


class OciPublicationFailureCodeTests(unittest.TestCase):
    def test_publication_source_literals_match_authoritative_inventory(self) -> None:
        emitted = set()
        for relative in PUBLICATION_MODULES:
            emitted.update(_module_failure_codes(ROOT / relative))
        emitted.update(DYNAMIC_PASSTHROUGH_CODES)
        self.assertEqual(PUBLICATION_FAILURE_CODES, emitted)

    def test_product_contract_registers_every_publication_failure_code(self) -> None:
        contract = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        registered = contract["failure_codes"]
        self.assertEqual(sorted(registered), registered)
        self.assertEqual(len(set(registered)), len(registered))
        self.assertEqual(
            set(),
            PUBLICATION_FAILURE_CODES - set(registered),
        )


if __name__ == "__main__":
    unittest.main()
