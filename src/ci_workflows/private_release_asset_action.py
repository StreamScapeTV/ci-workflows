"""Action adapter that resolves one bounded private release asset from checked-out product config."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from typing import Mapping, Sequence

from .private_release_asset import (
    PrivateReleaseAssetError,
    PrivateReleaseAssetSpec,
    main as release_asset_main,
)

_MAX_CONFIG_BYTES = 64 * 1024
_RELEASE_ENV = {
    "repository": "INPUT_RELEASE_ASSET_REPOSITORY",
    "tag": "INPUT_RELEASE_ASSET_TAG",
    "commit_sha": "INPUT_RELEASE_ASSET_COMMIT_SHA",
    "asset_name": "INPUT_RELEASE_ASSET_NAME",
    "sha256": "INPUT_RELEASE_ASSET_SHA256",
    "archive_subpath": "INPUT_RELEASE_ASSET_ARCHIVE_SUBPATH",
    "destination": "INPUT_RELEASE_ASSET_DESTINATION",
    "id": "INPUT_RELEASE_ASSET_ID",
}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PrivateReleaseAssetError(code)


def _source_root(environment: Mapping[str, str]) -> Path:
    value = environment.get("INPUT_SOURCE_ROOT", "source")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        metadata = os.lstat(candidate)
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise PrivateReleaseAssetError("release_config_source_invalid") from None
    _require(
        stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode) and resolved.is_dir(),
        "release_config_source_invalid",
    )
    return resolved


def _read_config(source_root: Path) -> Mapping[str, object]:
    github = source_root / ".github"
    config = github / "central-ci.json"
    try:
        github_metadata = os.lstat(github)
        config_metadata = os.lstat(config)
    except OSError:
        raise PrivateReleaseAssetError("release_config_missing") from None
    _require(
        stat.S_ISDIR(github_metadata.st_mode)
        and not stat.S_ISLNK(github_metadata.st_mode)
        and stat.S_ISREG(config_metadata.st_mode)
        and not stat.S_ISLNK(config_metadata.st_mode),
        "release_config_invalid_path",
    )
    try:
        raw = config.read_bytes()
    except OSError:
        raise PrivateReleaseAssetError("release_config_unreadable") from None
    _require(0 < len(raw) <= _MAX_CONFIG_BYTES, "release_config_size")

    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            _require(key not in result, "release_config_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=hook)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PrivateReleaseAssetError("release_config_invalid_json") from None
    _require(isinstance(value, dict), "release_config_invalid_json")
    return value


def _config_spec(source_root: Path, workflow_key: str) -> PrivateReleaseAssetSpec | None:
    config = _read_config(source_root)
    profiles = config.get("profiles")
    _require(isinstance(profiles, dict) and 1 <= len(profiles) <= 16, "release_config_profiles_invalid")
    matches: dict[tuple[str, ...], PrivateReleaseAssetSpec] = {}
    for profile in profiles.values():
        _require(isinstance(profile, dict), "release_config_profile_invalid")
        if profile.get("workflow_key") != workflow_key:
            continue
        raw_release = profile.get("private_release_asset")
        if raw_release is None:
            continue
        _require("private_dependency" not in profile, "private_dependency_kind_conflict")
        _require(isinstance(raw_release, dict), "private_release_asset_invalid")
        spec = PrivateReleaseAssetSpec.parse(raw_release)
        key = tuple(spec.as_payload()[name] for name in (
            "repository",
            "tag",
            "commit_sha",
            "asset_name",
            "sha256",
            "archive_subpath",
            "destination",
            "id",
        ))
        matches[key] = spec
    _require(len(matches) <= 1, "private_release_asset_ambiguous")
    return next(iter(matches.values()), None)


def hydrate_environment(environment: Mapping[str, str]) -> dict[str, str]:
    result = dict(environment)
    workflow_key = result.get("INPUT_CONFIG_WORKFLOW_KEY", "")
    explicit = {key: result.get(name, "") for key, name in _RELEASE_ENV.items()}
    if not workflow_key:
        return result
    _require(not any(explicit.values()), "private_release_asset_source_conflict")
    spec = _config_spec(_source_root(result), workflow_key)
    if spec is None:
        return result
    for key, value in spec.as_payload().items():
        result[_RELEASE_ENV[key]] = value
    return result


def main(argv: Sequence[str] | None = None, environment: Mapping[str, str] = os.environ) -> int:
    try:
        hydrated = hydrate_environment(environment)
    except PrivateReleaseAssetError as error:
        print(error.code, file=sys.stderr)
        return 1
    return release_asset_main(argv, hydrated)


__all__ = ("hydrate_environment", "main")
