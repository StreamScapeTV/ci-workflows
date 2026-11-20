#!/usr/bin/env python3
"""Validate one per-purpose CI request against the reviewed repository-ci-v1 contract.

The Agent State CI run carries ``workflow_key``/``test_profile``/``inputs``. A per-purpose
workflow is bound to a fixed set of operations (``--allow``) and one host OS (``--host-os``).
This helper re-uses the reviewed semantic-input schema from ``contracts/repository-ci-v1.json``
so that no new input surface is invented. It prints one JSON object on stdout:
``{"entrypoint": ".ci/build.sh", "semantic_inputs_json": "{...}"}``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

MAX_RAW_BYTES = 16384


def _fail(message: str):
    raise SystemExit(message)


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field: {key}")
        result[key] = value
    return result


def _load(raw: str, label: str):
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicates)
    except (json.JSONDecodeError, ValueError):
        _fail(f"{label} must be one unambiguous JSON object")


def _validate_field(fields: dict, name: str, value) -> None:
    field = fields[name]
    kind = field.get("type")
    if kind == "string":
        pattern = field.get("pattern")
        limit = field.get("maxLength")
        if not isinstance(value, str) or not isinstance(limit, int) or not 1 <= len(value) <= limit:
            _fail(f"semantic field {name} is outside the reviewed bound")
        if not isinstance(pattern, str) or re.fullmatch(pattern, value) is None:
            _fail(f"semantic field {name} is outside the reviewed bound")
    elif kind == "enum":
        if not isinstance(value, str) or value not in field.get("values", []):
            _fail(f"semantic field {name} is outside the reviewed enum")
    elif kind == "string-list":
        if not isinstance(value, list) or len(value) > min(int(field.get("maxItems", 0)), 128):
            _fail(f"semantic field {name} must be a bounded string list")
        pattern = field.get("itemPattern")
        limit = field.get("itemMaxLength")
        for item in value:
            if not isinstance(item, str) or not isinstance(limit, int) or not 1 <= len(item) <= limit:
                _fail(f"semantic field {name} contains an invalid item")
            if not isinstance(pattern, str) or re.fullmatch(pattern, item) is None:
                _fail(f"semantic field {name} contains an invalid item")
            if field.get("rejectLeadingDash") is True and item.startswith("-"):
                _fail(f"semantic field {name} contains a leading-dash item")
    else:
        _fail(f"semantic field {name} has an unsupported type")


def validate(*, contract: dict, operation: str, allowed_operations: set, host_os: str,
             claim_inputs_raw: str, workflow_key: str, accepted_workflow_keys: set) -> dict:
    if workflow_key not in accepted_workflow_keys:
        _fail("CI run workflow_key is not accepted by this per-purpose workflow")
    if operation not in allowed_operations:
        _fail("CI run test_profile is not an operation of this per-purpose workflow")
    if host_os not in {"linux", "macos"}:
        _fail("host OS must be linux or macos")
    if len(claim_inputs_raw.encode("utf-8")) > MAX_RAW_BYTES:
        _fail("CI run inputs exceed the reviewed bound")
    claim = _load(claim_inputs_raw or "{}", "CI run inputs")
    if not isinstance(claim, dict):
        _fail("CI run inputs must be one JSON object")
    # The OS belongs to the workflow, not to the caller. A conflicting legacy host_os is refused.
    requested_os = claim.get("host_os")
    if requested_os not in (None, "", host_os):
        _fail("CI run host_os conflicts with the host OS of this workflow")
    raw_semantic = claim.get("semantic_inputs", "{}")
    if not isinstance(raw_semantic, str):
        _fail("CI run semantic_inputs must be one JSON string")

    semantic = contract.get("semanticInputs")
    adoption = contract.get("adoption")
    if not isinstance(semantic, dict) or not isinstance(adoption, dict) or contract.get("schemaVersion") != 1:
        _fail("repository CI contract is invalid")
    if len(raw_semantic.encode("utf-8")) > min(int(semantic.get("maxRawBytes", 0)), MAX_RAW_BYTES):
        _fail("semantic inputs exceed the reviewed bound")
    values = _load(raw_semantic, "semantic inputs")
    if not isinstance(values, dict):
        _fail("semantic inputs must be one JSON object")

    entrypoint = (adoption.get("fixedEntrypoints") or {}).get(operation)
    if not isinstance(entrypoint, str) or not re.fullmatch(r"\.ci/[a-z0-9-]+\.sh", entrypoint):
        _fail("fixed entrypoint is not defined by the reviewed contract")
    spec = (semantic.get("operations") or {}).get(operation)
    fields = semantic.get("fields") or {}
    if not isinstance(spec, dict):
        _fail("semantic operation schema is missing")
    allowed = set(spec.get("allowed", []))
    required = set(spec.get("required", []))
    if set(values) - allowed:
        _fail("semantic inputs contain an unknown field")
    if required - set(values):
        _fail("semantic inputs omit a required field")
    for name, value in values.items():
        _validate_field(fields, name, value)
    normalized = json.dumps({"schemaVersion": 1, "inputs": values}, sort_keys=True, separators=(",", ":"))
    return {"entrypoint": entrypoint, "semantic_inputs_json": normalized, "inputs": values}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--allow", required=True, help="comma separated operations")
    parser.add_argument("--accept-workflow-key", action="append", required=True)
    parser.add_argument("--host-os", required=True)
    parser.add_argument("--reject-product-target", action="append", default=[])
    args = parser.parse_args(argv)
    contract_path = Path(args.contract)
    if contract_path.is_symlink() or not contract_path.is_file():
        _fail("repository CI contract must be one regular file")
    result = validate(
        contract=json.loads(contract_path.read_text(encoding="utf-8")),
        operation=os.environ["TEST_PROFILE"],
        allowed_operations={item for item in args.allow.split(",") if item},
        host_os=args.host_os,
        claim_inputs_raw=os.environ.get("CLAIM_INPUTS_JSON", "{}"),
        workflow_key=os.environ["WORKFLOW_KEY"],
        accepted_workflow_keys=set(args.accept_workflow_key),
    )
    if result["inputs"].get("product_target") in set(args.reject_product_target):
        _fail("product_target is not an operation of this per-purpose workflow")
    result.pop("inputs")
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
