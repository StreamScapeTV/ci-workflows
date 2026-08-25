"""Bounded Central product CI profile projection."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
import stat
import sys
from typing import Mapping, Sequence

from .ci_broker import (
    BrokerError,
    MAX_CONFIG_BYTES,
    _safe_product_scalar,
    _safe_profile,
    _safe_project,
    _safe_repository,
    _safe_sha,
    _safe_workflow_key,
    _safe_workspace,
)
from .private_release_asset import PrivateReleaseAssetError, PrivateReleaseAssetSpec, optional_spec

CONFIG_RELATIVE_PATH = ".github/central-ci.json"
_SUPPORTED_PROJECTIONS = {("validation.apple", "apple-host-test")}


class CentralProfileError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise CentralProfileError(code)


def apple_host_validation_plan(workspace: object, scheme: object, test_target: object) -> str:
    checked_workspace = _safe_workspace(workspace)
    checked_scheme = _safe_product_scalar(scheme, "invalid_scheme")
    checked_target = _safe_product_scalar(test_target, "invalid_test_target")
    return json.dumps(
        {
            "stages": [
                {
                    "id": "central-host-test",
                    "platform": "macos",
                    "operation": "test",
                    "working_directory": ".",
                    "container": {"kind": "workspace", "path": checked_workspace},
                    "scheme": checked_scheme,
                    "configuration": "Debug",
                    "test_plan": "",
                    "package_resolution_mode": "resolve-only",
                    "resolved_files": [],
                    "script": None,
                    "xcodebuild_arguments": [],
                    "test_selectors": [checked_target],
                    "expected_outputs": [],
                    "cleanup_paths": [],
                }
            ]
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _source_root(value: object) -> Path:
    _require(isinstance(value, str) and bool(value), "source_root_invalid")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        metadata = os.lstat(candidate)
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise CentralProfileError("source_root_invalid") from None
    _require(stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode), "source_root_invalid")
    _require(resolved.is_dir(), "source_root_invalid")
    return resolved


def _read_config(source_root: Path) -> Mapping[str, object]:
    github_dir = source_root / ".github"
    config_path = source_root / CONFIG_RELATIVE_PATH
    try:
        github_metadata = os.lstat(github_dir)
        config_metadata = os.lstat(config_path)
    except OSError:
        raise CentralProfileError("private_ci_config_missing") from None
    _require(stat.S_ISDIR(github_metadata.st_mode) and not stat.S_ISLNK(github_metadata.st_mode), "private_ci_config_invalid_path")
    _require(stat.S_ISREG(config_metadata.st_mode) and not stat.S_ISLNK(config_metadata.st_mode), "private_ci_config_invalid_path")
    try:
        raw = config_path.read_bytes()
    except OSError:
        raise CentralProfileError("private_ci_config_unreadable") from None
    _require(0 < len(raw) <= MAX_CONFIG_BYTES, "private_ci_config_size")

    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CentralProfileError("private_ci_config_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=hook)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CentralProfileError("private_ci_config_invalid_json") from None
    _require(isinstance(value, dict), "private_ci_config_invalid_json")
    return value


def _strip_release_assets(
    value: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, PrivateReleaseAssetSpec]]:
    """Project the additive active-only release asset while preserving the legacy config parser."""
    sanitized = dict(value)
    raw_profiles = value.get("profiles")
    if not isinstance(raw_profiles, dict):
        return sanitized, {}
    profiles: dict[str, object] = {}
    release_assets: dict[str, PrivateReleaseAssetSpec] = {}
    for raw_name, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict):
            profiles[str(raw_name)] = raw_profile
            continue
        profile = dict(raw_profile)
        raw_release = profile.pop("private_release_asset", None)
        if raw_release is not None:
            _require("private_dependency" not in profile, "private_dependency_kind_conflict")
            try:
                parsed = optional_spec(raw_release)
            except PrivateReleaseAssetError as error:
                raise CentralProfileError(error.code) from None
            assert parsed is not None
            release_assets[str(raw_name)] = parsed
        profiles[str(raw_name)] = profile
    sanitized["profiles"] = profiles
    return sanitized, release_assets


@dataclass(frozen=True, slots=True)
class CentralProfileResolution:
    project_key: str
    test_profile: str
    workflow_key: str
    capability: str
    source_repository: str
    admitted_sha: str
    validation_scope: str
    validation_plan_json: str
    private_dependency_repository: str = ""
    private_dependency_sha: str = ""
    private_dependency_subdirectory: str = "."
    private_dependency_id: str = ""
    private_release_asset_repository: str = ""
    private_release_asset_tag: str = ""
    private_release_asset_commit_sha: str = ""
    private_release_asset_name: str = ""
    private_release_asset_sha256: str = ""
    private_release_asset_archive_subpath: str = ""
    private_release_asset_destination: str = ""
    private_release_asset_id: str = ""

    def output_values(self) -> dict[str, str]:
        return {
            "project_key": self.project_key,
            "test_profile": self.test_profile,
            "workflow_key": self.workflow_key,
            "capability": self.capability,
            "source_repository": self.source_repository,
            "admitted_sha": self.admitted_sha,
            "validation_scope": self.validation_scope,
            "validation_plan_json": self.validation_plan_json,
            "private_dependency_repository": self.private_dependency_repository,
            "private_dependency_sha": self.private_dependency_sha,
            "private_dependency_subdirectory": self.private_dependency_subdirectory,
            "private_dependency_id": self.private_dependency_id,
            "private_release_asset_repository": self.private_release_asset_repository,
            "private_release_asset_tag": self.private_release_asset_tag,
            "private_release_asset_commit_sha": self.private_release_asset_commit_sha,
            "private_release_asset_name": self.private_release_asset_name,
            "private_release_asset_sha256": self.private_release_asset_sha256,
            "private_release_asset_archive_subpath": self.private_release_asset_archive_subpath,
            "private_release_asset_destination": self.private_release_asset_destination,
            "private_release_asset_id": self.private_release_asset_id,
        }

    def release_asset(self) -> PrivateReleaseAssetSpec | None:
        if not self.private_release_asset_repository:
            return None
        return PrivateReleaseAssetSpec.parse(
            {
                "repository": self.private_release_asset_repository,
                "tag": self.private_release_asset_tag,
                "commit_sha": self.private_release_asset_commit_sha,
                "asset_name": self.private_release_asset_name,
                "sha256": self.private_release_asset_sha256,
                "archive_subpath": self.private_release_asset_archive_subpath,
                "destination": self.private_release_asset_destination,
                "id": self.private_release_asset_id,
            }
        )


def resolve_profile(*, source_root: object, project_key: object, workflow_key: object, test_profile: object, source_repository: object, admitted_sha: object) -> CentralProfileResolution:
    checked_project = _safe_project(project_key)
    checked_workflow = _safe_workflow_key(workflow_key)
    checked_profile = _safe_profile(test_profile)
    checked_repository = _safe_repository(source_repository)
    checked_sha = _safe_sha(admitted_sha)
    value = _read_config(_source_root(source_root))

    from .ci_broker_dependencies import BrokerProductConfig

    sanitized, release_assets = _strip_release_assets(value)
    config = BrokerProductConfig.parse(sanitized)
    _require(config.project_key == checked_project, "project_config_mismatch")
    profile = config.profile(checked_profile, checked_workflow)
    _require((profile.workflow_key, profile.capability) in _SUPPORTED_PROJECTIONS, "central_profile_unsupported")
    dependency = profile.private_dependency
    release = release_assets.get(profile.name)
    return CentralProfileResolution(
        project_key=checked_project,
        test_profile=profile.name,
        workflow_key=profile.workflow_key,
        capability=profile.capability,
        source_repository=checked_repository,
        admitted_sha=checked_sha,
        validation_scope="protected-full",
        validation_plan_json=apple_host_validation_plan(profile.workspace, profile.scheme, profile.test_target),
        private_dependency_repository=(dependency.repository if dependency else ""),
        private_dependency_sha=(dependency.sha if dependency else ""),
        private_dependency_subdirectory=(dependency.subdirectory if dependency else "."),
        private_dependency_id=(dependency.dependency_id if dependency else ""),
        private_release_asset_repository=(release.repository if release else ""),
        private_release_asset_tag=(release.tag if release else ""),
        private_release_asset_commit_sha=(release.commit_sha if release else ""),
        private_release_asset_name=(release.asset_name if release else ""),
        private_release_asset_sha256=(release.sha256 if release else ""),
        private_release_asset_archive_subpath=(release.archive_subpath if release else ""),
        private_release_asset_destination=(release.destination if release else ""),
        private_release_asset_id=(release.dependency_id if release else ""),
    )


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    _require(bool(value), f"missing_{name.lower()}")
    return value


def resolve_from_environment(environment: Mapping[str, str] = os.environ) -> CentralProfileResolution:
    resolution = resolve_profile(
        source_root=_required(environment, "INPUT_SOURCE_ROOT"),
        project_key=_required(environment, "INPUT_PROJECT_KEY"),
        workflow_key=_required(environment, "INPUT_WORKFLOW_KEY"),
        test_profile=_required(environment, "INPUT_TEST_PROFILE"),
        source_repository=_required(environment, "INPUT_SOURCE_REPOSITORY"),
        admitted_sha=_required(environment, "INPUT_ADMITTED_SHA"),
    )
    output_path = Path(_required(environment, "GITHUB_OUTPUT"))
    _require(output_path.is_absolute(), "invalid_github_output")
    try:
        with output_path.open("a", encoding="utf-8") as handle:
            for key, value in resolution.output_values().items():
                _require("\n" not in value and "\r" not in value, "invalid_profile_output")
                handle.write(f"{key}={value}\n")
    except OSError:
        raise CentralProfileError("github_output_unavailable") from None
    return resolution


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Resolve bounded Central product CI profile")
    result.add_argument("command", choices=("resolve",))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "resolve":
            resolve_from_environment()
    except (BrokerError, CentralProfileError, PrivateReleaseAssetError) as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


__all__ = (
    "CONFIG_RELATIVE_PATH",
    "CentralProfileError",
    "CentralProfileResolution",
    "apple_host_validation_plan",
    "main",
    "resolve_from_environment",
    "resolve_profile",
)
