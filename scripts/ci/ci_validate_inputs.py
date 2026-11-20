#!/usr/bin/env python3
"""Validate one ecosystem CI request (workflow_dispatch inputs) before any credential is used.

The agent runner dispatches an ecosystem workflow with ``request_id``, ``repository``, ``ref``,
``is_tag``, ``profile`` and ``options`` (a JSON object string). Nothing here talks to Agent State.
``options`` are checked against the reviewed semantic-input schema of
``contracts/repository-ci-v1.json`` (the same fields the product ``.ci`` scripts already accept)
plus a few ecosystem specific fields. Arbitrary commands, environment maps, runner labels and
secret names are not representable.

Prints one JSON object: ``{"stages": [...], "options": {...}}``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ORGANIZATION = "StreamScapeTV"
MAX_OPTIONS_BYTES = 4096
STAGES = ("lint", "build", "test")
PROFILES = {"lint": ("lint",), "build": ("build",), "test": ("test",), "check": STAGES}
EXTRA_FIELDS = {
    "xcode": {"scheme": {"type": "string", "maxLength": 64, "pattern": r"[A-Za-z0-9._-]{1,64}"}},
    "gradle": {},
}
REPOSITORY = re.compile(ORGANIZATION + r"/[A-Za-z0-9_.-]{1,100}")
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,63}")


def _fail(message: str):
    raise SystemExit(message)


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field: {key}")
        result[key] = value
    return result


def _validate_field(field: dict, name: str, value) -> None:
    kind = field.get("type")
    if kind == "string":
        limit, pattern = field.get("maxLength"), field.get("pattern")
        if not isinstance(value, str) or not isinstance(limit, int) or not 1 <= len(value) <= limit:
            _fail(f"option {name} is outside the reviewed bound")
        if not isinstance(pattern, str) or re.fullmatch(pattern, value) is None:
            _fail(f"option {name} is outside the reviewed bound")
    elif kind == "enum":
        if not isinstance(value, str) or value not in field.get("values", []):
            _fail(f"option {name} is outside the reviewed enum")
    elif kind == "string-list":
        if not isinstance(value, list) or len(value) > min(int(field.get("maxItems", 0)), 128):
            _fail(f"option {name} must be a bounded string list")
        pattern, limit = field.get("itemPattern"), field.get("itemMaxLength")
        for item in value:
            if not isinstance(item, str) or not isinstance(limit, int) or not 1 <= len(item) <= limit:
                _fail(f"option {name} contains an invalid item")
            if not isinstance(pattern, str) or re.fullmatch(pattern, item) is None:
                _fail(f"option {name} contains an invalid item")
            if field.get("rejectLeadingDash") is True and item.startswith("-"):
                _fail(f"option {name} contains a leading-dash item")
    else:
        _fail(f"option {name} has an unsupported type")


def validate(*, contract: dict, ecosystem: str, request_id: str, repository: str, ref: str,
             is_tag: str, profile: str, options_raw: str) -> dict:
    if ecosystem not in EXTRA_FIELDS:
        _fail("unsupported ecosystem")
    if REQUEST_ID.fullmatch(request_id) is None:
        _fail("request_id is outside the reviewed bound")
    if REPOSITORY.fullmatch(repository) is None:
        _fail(f"repository must be one {ORGANIZATION}/<name> repository")
    if not 1 <= len(ref) <= 255 or any(ord(c) < 32 or ord(c) == 127 for c in ref):
        _fail("ref is outside the reviewed bound")
    if is_tag not in ("true", "false"):
        _fail("is_tag must be true or false")
    if profile not in PROFILES:
        _fail("profile must be one of: " + ", ".join(PROFILES))
    if len(options_raw.encode("utf-8")) > MAX_OPTIONS_BYTES:
        _fail("options exceed the reviewed bound")
    try:
        options = json.loads(options_raw or "{}", object_pairs_hook=_reject_duplicates)
    except (json.JSONDecodeError, ValueError):
        _fail("options must be one unambiguous JSON object")
    if not isinstance(options, dict):
        _fail("options must be one JSON object")

    semantic = contract.get("semanticInputs") or {}
    fields = dict(semantic.get("fields") or {})
    operations = semantic.get("operations") or {}
    fields.update(EXTRA_FIELDS[ecosystem])
    stages = list(PROFILES[profile])
    allowed = set(EXTRA_FIELDS[ecosystem])
    for stage in stages:
        spec = operations.get(stage)
        if not isinstance(spec, dict):
            _fail("semantic operation schema is missing")
        allowed |= set(spec.get("allowed", []))
    if set(options) - allowed:
        _fail("options contain a field this profile does not accept")
    for name, value in options.items():
        _validate_field(fields[name], name, value)
    return {"stages": stages, "options": options}


def stage_inputs(contract: dict, stage: str, options: dict) -> dict:
    """Project the validated options onto the fields the product script of one stage accepts."""
    allowed = set(((contract.get("semanticInputs") or {}).get("operations") or {}).get(stage, {}).get("allowed", []))
    return {"schemaVersion": 1, "inputs": {key: value for key, value in options.items() if key in allowed}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--ecosystem", required=True)
    args = parser.parse_args(argv)
    path = Path(args.contract)
    if path.is_symlink() or not path.is_file():
        _fail("repository CI contract must be one regular file")
    result = validate(
        contract=json.loads(path.read_text(encoding="utf-8")), ecosystem=args.ecosystem,
        request_id=os.environ["REQUEST_ID"], repository=os.environ["REPOSITORY"], ref=os.environ["REF"],
        is_tag=os.environ["IS_TAG"], profile=os.environ["PROFILE"], options_raw=os.environ.get("OPTIONS", "{}"),
    )
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
