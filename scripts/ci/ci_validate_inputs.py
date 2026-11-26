#!/usr/bin/env python3
"""Validate one ecosystem CI request (workflow_dispatch inputs) before any credential is used.

The agent runner dispatches an ecosystem workflow with ``request_id``, ``repository``, ``ref``,
``is_tag``, ``operation`` and ``options`` (a JSON object string). Nothing here talks to Agent State.
``options`` are checked against the reviewed semantic-input schema of
``contracts/repository-ci-v1.json`` (the same fields the product ``.ci`` scripts already accept)
plus a few ecosystem specific fields. Arbitrary commands, environment maps, runner labels and
secret names are not representable.

Prints one JSON object: ``{"stages": [...], "options": {...}}``. A native (Xcode, Gradle) release adds
``release_options``: the options of the release stage, with ``build_identity`` derived from the tag
(``native_release_options``); the repository's ``.ci/release.sh`` receives them as ``CI_INPUTS_JSON``.
A Gradle release also adds ``release_version``, the marketing-version part of its tag (``release_version``).
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
# Hierarchy lint < test < build: the top requested operation runs every operation below it as a stage.
HIERARCHY = ("lint", "test", "build")
OPERATIONS = {name: HIERARCHY[: index + 1] for index, name in enumerate(HIERARCHY)}
# Ecosystems whose workflow also publishes (operation release runs lint, test, build, release).
RELEASE_ECOSYSTEMS = ("node", "python", "xcode", "gradle")
RELEASE_OPERATIONS = {**OPERATIONS, "release": (*HIERARCHY, "release")}
# Native releases run the repository's .ci/release.sh (App Store Connect, Play internal draft, prepare only) from a tag.
NATIVE_RELEASE_ECOSYSTEMS = ("xcode", "gradle")
# Mobile release tag <marketing-version>_<build> (the Apple and Android app tag form, as before OP 7160). Gradle (Android)
# takes the build number as its identity (the versionCode); Xcode receives the whole tag and its .ci/release.sh splits it.
MOBILE_TAG = re.compile(r"(?P<version>[0-9]+(?:\.[0-9]+){1,3})_(?P<build>[1-9][0-9]{0,9})")
EXTRA_FIELDS = {
    "node": {},
    "xcode": {"scheme": {"type": "string", "maxLength": 64, "pattern": r"[A-Za-z0-9._-]{1,64}"}},
    "gradle": {},
    "python": {},
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
             is_tag: str, operation: str, options_raw: str) -> dict:
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
    operations_table = RELEASE_OPERATIONS if ecosystem in RELEASE_ECOSYSTEMS else OPERATIONS
    if operation not in operations_table:
        _fail("operation must be one of: " + ", ".join(operations_table))
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
    stages = list(operations_table[operation])
    allowed = set(EXTRA_FIELDS[ecosystem])
    for stage in stages:
        spec = operations.get(stage)
        if not isinstance(spec, dict):
            _fail("semantic operation schema is missing")
        allowed |= set(spec.get("allowed", []))
    if set(options) - allowed:
        _fail("options contain a field this operation does not accept")
    for name, value in options.items():
        _validate_field(fields[name], name, value)
    result = {"stages": stages, "options": options}
    if operation == "release" and ecosystem in NATIVE_RELEASE_ECOSYSTEMS:
        result["release_options"] = native_release_options(fields, ecosystem, ref, is_tag, options)
        if ecosystem == "gradle":
            result["release_version"] = release_version(ref)
    return result


def native_release_options(fields: dict, ecosystem: str, ref: str, is_tag: str, options: dict) -> dict:
    """Options of a native release stage: the request's release_kind (and product_target) plus build_identity.

    The release identity comes only from the immutable tag (as the pre-OP 7160 tag route did), never from
    the caller: an Xcode release passes the tag itself, a Gradle release the build number of its
    ``<marketing-version>_<build>`` tag (any other Gradle tag is refused here, before a build runs).
    release_kind comes from the project's CI profile; the repository script decides what it accepts.
    """
    if is_tag != "true":
        _fail("a native release runs only from an immutable tag (is_tag true)")
    if "build_identity" in options:
        _fail("build_identity is derived from the tag; the request must not supply it")
    if "release_kind" not in options:
        _fail("a native release needs options.release_kind (prepare or publish) from the project's CI profile")
    identity = ref
    if ecosystem == "gradle":
        identity = _mobile_tag(ref).group("build")
    _validate_field(fields["build_identity"], "build_identity (from the tag)", identity)
    return {**options, "build_identity": identity}


def _mobile_tag(ref: str):
    match = MOBILE_TAG.fullmatch(ref)
    if match is None:
        _fail(f"a Gradle release tag must be <marketing-version>_<build> (for example 1.2.3_45), got {ref!r}")
    return match


def release_version(ref: str) -> str:
    """Marketing version of a Gradle release tag (1.2.3 of 1.2.3_45); the release checks it against versionName."""
    return _mobile_tag(ref).group("version")


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
        is_tag=os.environ["IS_TAG"], operation=os.environ["OPERATION"], options_raw=os.environ.get("OPTIONS", "{}"),
    )
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
