#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Any

MOBILE_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}")
MOBILE_TAG_RE = re.compile(r"(?P<version>[0-9]+(?:\.[0-9]+){1,3})_(?P<build>[1-9][0-9]{0,9})")
PLAIN_VERSION_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
ANDROID_VERSION_CODE_MAX = 2_100_000_000


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseIdentity:
    mode: str
    version: str
    build_number: str

    def as_dict(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "version": self.version,
            "build_number": self.build_number,
        }


def _inputs_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError("release inputs must be one JSON object") from exc
    if not isinstance(value, dict):
        raise ContractError("release inputs must be one JSON object")
    return value


def _explicit_build_number(inputs: dict[str, Any], *, android: bool) -> str:
    if set(inputs) != {"build_number"}:
        raise ContractError("explicit release requires exactly inputs.build_number")
    value = inputs["build_number"]
    if not isinstance(value, str):
        raise ContractError("build_number must be a string")
    if android:
        if re.fullmatch(r"[1-9][0-9]{0,9}", value) is None:
            raise ContractError("Android build_number must be one positive decimal versionCode")
        if int(value) > ANDROID_VERSION_CODE_MAX:
            raise ContractError("Android build_number exceeds maximum versionCode")
    elif PLAIN_VERSION_TAG_RE.fullmatch(value) is None:
        raise ContractError("build_number must be one bounded release identifier")
    return value


def parse_mobile_tag(tag: str, *, android: bool) -> ReleaseIdentity:
    match = MOBILE_TAG_RE.fullmatch(tag)
    if match is None:
        raise ContractError("mobile release tag must match <marketing-version>_<positive-build> without a v prefix")
    version = match.group("version")
    build = match.group("build")
    if MOBILE_VERSION_RE.fullmatch(version) is None:
        raise ContractError("mobile marketing version is invalid")
    if android and int(build) > ANDROID_VERSION_CODE_MAX:
        raise ContractError("Android tag build exceeds maximum versionCode")
    return ReleaseIdentity(mode="mobile_tag", version=version, build_number=build)


def parse_plain_version_tag(tag: str) -> ReleaseIdentity:
    if PLAIN_VERSION_TAG_RE.fullmatch(tag) is None or "_" in tag:
        raise ContractError("plain release tag must be one bounded non-mobile version identifier")
    return ReleaseIdentity(mode="version_tag", version=tag, build_number=tag)


def _require_zero_tag_inputs(inputs: dict[str, Any]) -> None:
    if inputs:
        raise ContractError("tag-driven release accepts no semantic inputs; release identity comes only from the tag")


def resolve_release_identity(
    *,
    workflow_key: str,
    profile: str,
    is_tag: bool,
    ref: str,
    inputs: dict[str, Any],
) -> ReleaseIdentity:
    if workflow_key == "release.android":
        if profile != "play":
            raise ContractError("release.android supports only the play profile")
        if is_tag:
            _require_zero_tag_inputs(inputs)
            return parse_mobile_tag(ref, android=True)
        return ReleaseIdentity(
            mode="explicit",
            version="",
            build_number=_explicit_build_number(inputs, android=True),
        )

    if workflow_key == "release.apple":
        if profile == "testflight":
            if is_tag:
                _require_zero_tag_inputs(inputs)
                return parse_mobile_tag(ref, android=False)
            return ReleaseIdentity(
                mode="explicit",
                version="",
                build_number=_explicit_build_number(inputs, android=False),
            )
        if profile in {"binary-package", "swiftpm-package"}:
            if inputs:
                raise ContractError(f"release.apple/{profile} accepts no semantic inputs")
            return ReleaseIdentity(mode="passthrough", version="", build_number="")
        raise ContractError("release.apple supports only testflight, binary-package and swiftpm-package profiles")

    if workflow_key == "release.maven":
        if profile != "publish":
            raise ContractError("release.maven supports only the publish profile")
        if is_tag:
            _require_zero_tag_inputs(inputs)
            return parse_plain_version_tag(ref)
        return ReleaseIdentity(
            mode="explicit",
            version="",
            build_number=_explicit_build_number(inputs, android=False),
        )

    raise ContractError("unsupported release workflow")


def _resolve_command(args: argparse.Namespace) -> int:
    identity = resolve_release_identity(
        workflow_key=args.workflow_key,
        profile=args.profile,
        is_tag=args.is_tag == "true",
        ref=args.ref,
        inputs=_inputs_object(args.inputs_json),
    )
    print(json.dumps(identity.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve bounded tag-driven Central release identity")
    subparsers = parser.add_subparsers(dest="command", required=True)
    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--workflow-key", required=True)
    resolve.add_argument("--profile", required=True)
    resolve.add_argument("--is-tag", choices=("true", "false"), required=True)
    resolve.add_argument("--ref", required=True)
    resolve.add_argument("--inputs-json", required=True)
    resolve.set_defaults(func=_resolve_command)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return args.func(args)
    except ContractError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
