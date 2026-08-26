"""Bounded Central product CI profile projection."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Mapping, Sequence

from .private_release_asset import PrivateReleaseAssetError, PrivateReleaseAssetSpec, optional_spec

CONFIG_RELATIVE_PATH = ".github/central-ci.json"
MAX_CONFIG_BYTES = 64 * 1024
_LEGACY_APPLE_PROJECTION = ("validation.apple", "apple-host-test")
_GENERIC_CAPABILITIES = {
    "validation.apple": "apple-hosted",
    "validation.android": "android-hosted",
    "validation.python": "python-hosted",
}
_APPLE_HOSTED_PROFILES = {"source-audit", "swift-package"}
_PYTHON_PROFILES = {"audit", "host", "podman", "podman-postgres"}
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WORKFLOW = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_SCALAR = re.compile(r"[^\r\n\x00]{1,512}\Z")
_DEPENDENCY_ID = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")


class CentralProfileError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise CentralProfileError(code)


def _safe(value: object, pattern: re.Pattern[str], code: str) -> str:
    _require(isinstance(value, str) and pattern.fullmatch(value) is not None, code)
    return value


def _safe_project(value: object) -> str:
    return _safe(value, _PROJECT, "invalid_project_key")


def _safe_profile(value: object) -> str:
    return _safe(value, _PROFILE, "invalid_test_profile")


def _safe_workflow_key(value: object) -> str:
    return _safe(value, _WORKFLOW, "invalid_workflow_key")


def _safe_repository(value: object) -> str:
    return _safe(value, _REPOSITORY, "invalid_repository")


def _safe_sha(value: object) -> str:
    return _safe(value, _SHA, "invalid_source_sha")


def _safe_product_scalar(value: object, code: str) -> str:
    return _safe(value, _SCALAR, code)


def _safe_workspace(value: object) -> str:
    text = _safe_product_scalar(value, "invalid_workspace")
    path = PurePosixPath(text)
    _require(
        not path.is_absolute()
        and ".." not in path.parts
        and text not in (".", "")
        and text.endswith(".xcworkspace"),
        "invalid_workspace",
    )
    return text


def _safe_dependency_subdirectory(value: object) -> str:
    _require(isinstance(value, str), "invalid_dependency_subdirectory")
    text = value.strip()
    _require(
        bool(text)
        and len(text.encode("utf-8")) <= 1024
        and "\\" not in text
        and not any(character in text for character in ("\x00", "\r", "\n")),
        "invalid_dependency_subdirectory",
    )
    if text == ".":
        return text
    path = PurePosixPath(text)
    _require(
        not path.is_absolute()
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts),
        "invalid_dependency_subdirectory",
    )
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class PrivateDependency:
    repository: str
    sha: str
    subdirectory: str
    dependency_id: str

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> "PrivateDependency":
        _require(
            set(value) == {"repository", "sha", "subdirectory", "id"},
            "private_ci_dependency_invalid",
        )
        repository = _safe_repository(value.get("repository"))
        _require(
            repository.startswith("StreamScapeTV/"),
            "private_ci_dependency_repository_unsupported",
        )
        return cls(
            repository=repository,
            sha=_safe_sha(value.get("sha")),
            subdirectory=_safe_dependency_subdirectory(value.get("subdirectory")),
            dependency_id=_safe(value.get("id"), _DEPENDENCY_ID, "invalid_dependency_id"),
        )


@dataclass(frozen=True, slots=True)
class LegacyAppleProfile:
    name: str
    workflow_key: str
    capability: str
    workspace: str
    scheme: str
    test_target: str
    private_dependency: PrivateDependency | None = None


@dataclass(frozen=True, slots=True)
class LegacyAppleConfig:
    project_key: str
    profiles: dict[str, LegacyAppleProfile]

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> "LegacyAppleConfig":
        allowed = {"schema_version", "project_key", "profiles", "automatic"}
        _require(set(value).issubset(allowed), "private_ci_config_unsupported")
        _require(value.get("schema_version") == 1, "private_ci_config_version")
        project_key = _safe_project(value.get("project_key"))
        raw_profiles = value.get("profiles")
        _require(
            isinstance(raw_profiles, dict) and 1 <= len(raw_profiles) <= 16,
            "private_ci_profiles_invalid",
        )
        profiles: dict[str, LegacyAppleProfile] = {}
        required = {
            "workflow_key",
            "capability",
            "workspace",
            "scheme",
            "test_target",
        }
        allowed_profile = required | {"private_dependency"}
        for raw_name, raw_profile in raw_profiles.items():
            name = _safe_profile(raw_name)
            _require(isinstance(raw_profile, dict), "private_ci_profile_invalid")
            fields = set(raw_profile)
            _require(
                required.issubset(fields) and fields.issubset(allowed_profile),
                "private_ci_profile_invalid",
            )
            workflow_key = _safe_workflow_key(raw_profile.get("workflow_key"))
            capability = raw_profile.get("capability")
            _require(
                isinstance(capability, str) and capability == "apple-host-test",
                "private_ci_capability_unsupported",
            )
            raw_dependency = raw_profile.get("private_dependency")
            dependency = None
            if raw_dependency is not None:
                _require(isinstance(raw_dependency, dict), "private_ci_dependency_invalid")
                dependency = PrivateDependency.parse(raw_dependency)
            profiles[name] = LegacyAppleProfile(
                name=name,
                workflow_key=workflow_key,
                capability=capability,
                workspace=_safe_workspace(raw_profile.get("workspace")),
                scheme=_safe_product_scalar(raw_profile.get("scheme"), "invalid_scheme"),
                test_target=_safe_product_scalar(
                    raw_profile.get("test_target"), "invalid_test_target"
                ),
                private_dependency=dependency,
            )

        automatic = value.get("automatic", {})
        _require(isinstance(automatic, dict), "private_ci_automatic_invalid")
        _require(set(automatic).issubset({"push", "tag"}), "private_ci_automatic_invalid")
        for profile_name in automatic.values():
            _require(_safe_profile(profile_name) in profiles, "private_ci_automatic_profile_missing")
        return cls(project_key=project_key, profiles=profiles)

    def profile(self, name: object, workflow_key: object) -> LegacyAppleProfile:
        checked_name = _safe_profile(name)
        _require(checked_name in self.profiles, "private_ci_profile_missing")
        profile = self.profiles[checked_name]
        _require(
            profile.workflow_key == _safe_workflow_key(workflow_key),
            "workflow_profile_mismatch",
        )
        return profile


def apple_host_validation_plan(workspace: object, scheme: object, test_target: object) -> str:
    checked_workspace = _safe_workspace(workspace)
    checked_scheme = _safe_product_scalar(scheme, "invalid_scheme")
    checked_target = _safe_product_scalar(test_target, "invalid_test_target")
    return json.dumps(
        {"stages": [{
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
        }]},
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


def _strip_release_assets(value: Mapping[str, object]) -> tuple[dict[str, object], dict[str, PrivateReleaseAssetSpec]]:
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


def _relative_path(value: object, code: str, *, allow_empty: bool = False) -> str:
    _require(isinstance(value, str), code)
    text = value.strip()
    if allow_empty and not text:
        return ""
    _require(bool(text) and len(text.encode("utf-8")) <= 1024, code)
    _require("\\" not in text and not any(ch in text for ch in ("\x00", "\r", "\n")), code)
    if text == ".":
        return text
    path = PurePosixPath(text)
    _require(not path.is_absolute() and ".." not in path.parts and all(part not in {"", "."} for part in path.parts), code)
    return path.as_posix()


def _json_plan(value: object, code: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value in (None, ""):
        return ""
    if isinstance(value, str):
        _require(bool(value) and len(value.encode("utf-8")) <= MAX_CONFIG_BYTES, code)
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise CentralProfileError(code) from None
    else:
        parsed = value
    _require(isinstance(parsed, (dict, list)), code)
    rendered = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    _require(len(rendered.encode("utf-8")) <= MAX_CONFIG_BYTES, code)
    return rendered


def _generic_inputs(workflow_key: str, value: object) -> dict[str, str]:
    _require(isinstance(value, dict), "private_ci_profile_inputs_invalid")
    raw = dict(value)
    if workflow_key == "validation.apple":
        _require(set(raw) == {"command_profile", "validation_profile"}, "private_ci_profile_inputs_invalid")
        validation_profile = raw.get("validation_profile")
        _require(isinstance(validation_profile, str) and validation_profile in _APPLE_HOSTED_PROFILES, "invalid_apple_validation_profile")
        return {
            "command_profile": _safe_product_scalar(raw.get("command_profile"), "invalid_apple_command_profile"),
            "validation_profile": validation_profile,
        }
    if workflow_key == "validation.android":
        allowed = {"working_directory", "gradle_wrapper_path", "validation_plan_json", "dependency_prebuild_plan_json"}
        _require(set(raw).issubset(allowed) and "validation_plan_json" in raw, "private_ci_profile_inputs_invalid")
        return {
            "working_directory": _relative_path(raw.get("working_directory", "."), "invalid_working_directory"),
            "gradle_wrapper_path": _relative_path(raw.get("gradle_wrapper_path", "gradlew"), "invalid_gradle_wrapper_path"),
            "validation_plan_json": _json_plan(raw.get("validation_plan_json"), "invalid_validation_plan"),
            "dependency_prebuild_plan_json": _json_plan(raw.get("dependency_prebuild_plan_json"), "invalid_dependency_prebuild_plan", allow_empty=True),
        }
    if workflow_key == "validation.python":
        allowed = {"validation_profile", "python_version", "version_file", "dependency_file", "working_directory", "script_path"}
        required = {"validation_profile", "python_version", "script_path"}
        _require(required.issubset(raw) and set(raw).issubset(allowed), "private_ci_profile_inputs_invalid")
        validation_profile = raw.get("validation_profile")
        _require(isinstance(validation_profile, str) and validation_profile in _PYTHON_PROFILES, "invalid_python_validation_profile")
        return {
            "validation_profile": validation_profile,
            "python_version": _safe_product_scalar(raw.get("python_version"), "invalid_python_version"),
            "version_file": _relative_path(raw.get("version_file", ""), "invalid_version_file", allow_empty=True),
            "dependency_file": _relative_path(raw.get("dependency_file", ""), "invalid_dependency_file", allow_empty=True),
            "working_directory": _relative_path(raw.get("working_directory", "."), "invalid_working_directory"),
            "script_path": _relative_path(raw.get("script_path"), "invalid_script_path"),
            "artifact_exception_id": "",
        }
    raise CentralProfileError("central_profile_unsupported")


def _select_v2_profile(raw_profiles: Mapping[str, object], *, checked_profile: str, checked_workflow: str) -> dict[str, object]:
    raw_profile = raw_profiles.get(checked_profile)
    _require(isinstance(raw_profile, dict), "private_ci_profile_missing")
    if "workflows" not in raw_profile:
        return dict(raw_profile)
    _require(set(raw_profile) == {"workflows"}, "private_ci_profile_invalid")
    raw_workflows = raw_profile.get("workflows")
    _require(isinstance(raw_workflows, dict) and 1 <= len(raw_workflows) <= len(_GENERIC_CAPABILITIES) and set(raw_workflows).issubset(_GENERIC_CAPABILITIES), "private_ci_profile_invalid")
    selected = raw_workflows.get(checked_workflow)
    _require(isinstance(selected, dict), "private_ci_profile_missing")
    _require(set(selected).issubset({"capability", "inputs", "private_dependency"}), "private_ci_profile_invalid")
    result = dict(selected)
    result["workflow_key"] = checked_workflow
    return result


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
    executor_family: str = "macos"
    canonical_inputs_json: str = "{}"
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
            "executor_family": self.executor_family,
            "canonical_inputs_json": self.canonical_inputs_json,
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

    def canonical_inputs(self) -> dict[str, str]:
        value = json.loads(self.canonical_inputs_json)
        _require(isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()), "invalid_canonical_inputs")
        return value

    def release_asset(self) -> PrivateReleaseAssetSpec | None:
        if not self.private_release_asset_repository:
            return None
        return PrivateReleaseAssetSpec.parse({
            "repository": self.private_release_asset_repository,
            "tag": self.private_release_asset_tag,
            "commit_sha": self.private_release_asset_commit_sha,
            "asset_name": self.private_release_asset_name,
            "sha256": self.private_release_asset_sha256,
            "archive_subpath": self.private_release_asset_archive_subpath,
            "destination": self.private_release_asset_destination,
            "id": self.private_release_asset_id,
        })


def _resolve_v1_apple(value: Mapping[str, object], *, checked_project: str, checked_workflow: str, checked_profile: str, checked_repository: str, checked_sha: str) -> CentralProfileResolution:
    sanitized, release_assets = _strip_release_assets(value)
    config = LegacyAppleConfig.parse(sanitized)
    profile = config.profile(checked_profile, checked_workflow)
    _require(config.project_key == checked_project, "project_config_mismatch")
    _require((profile.workflow_key, profile.capability) == _LEGACY_APPLE_PROJECTION, "central_profile_unsupported")
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
        executor_family="macos",
        canonical_inputs_json="{}",
        private_dependency_repository=dependency.repository if dependency else "",
        private_dependency_sha=dependency.sha if dependency else "",
        private_dependency_subdirectory=dependency.subdirectory if dependency else ".",
        private_dependency_id=dependency.dependency_id if dependency else "",
        private_release_asset_repository=release.repository if release else "",
        private_release_asset_tag=release.tag if release else "",
        private_release_asset_commit_sha=release.commit_sha if release else "",
        private_release_asset_name=release.asset_name if release else "",
        private_release_asset_sha256=release.sha256 if release else "",
        private_release_asset_archive_subpath=release.archive_subpath if release else "",
        private_release_asset_destination=release.destination if release else "",
        private_release_asset_id=release.dependency_id if release else "",
    )


def _resolve_v2_generic(value: Mapping[str, object], *, checked_project: str, checked_workflow: str, checked_profile: str, checked_repository: str, checked_sha: str) -> CentralProfileResolution:
    _require(set(value).issubset({"schema_version", "project_key", "profiles"}), "private_ci_config_unsupported")
    _require(_safe_project(value.get("project_key")) == checked_project, "project_config_mismatch")
    raw_profiles = value.get("profiles")
    _require(isinstance(raw_profiles, dict) and 1 <= len(raw_profiles) <= 16, "private_ci_profiles_invalid")
    raw_profile = _select_v2_profile(raw_profiles, checked_profile=checked_profile, checked_workflow=checked_workflow)
    _require(set(raw_profile).issubset({"workflow_key", "capability", "inputs", "private_dependency"}), "private_ci_profile_invalid")
    workflow_key = _safe_workflow_key(raw_profile.get("workflow_key"))
    _require(workflow_key == checked_workflow, "workflow_profile_mismatch")
    capability = raw_profile.get("capability")
    _require(isinstance(capability, str) and capability == _GENERIC_CAPABILITIES.get(workflow_key), "central_profile_unsupported")
    inputs = _generic_inputs(workflow_key, raw_profile.get("inputs"))

    dependency_repository = ""
    dependency_sha = ""
    dependency_subdirectory = "."
    dependency_id = ""
    raw_dependency = raw_profile.get("private_dependency")
    if raw_dependency is not None:
        _require(workflow_key != "validation.apple", "apple_private_dependency_unsupported")
        _require(isinstance(raw_dependency, dict), "private_ci_dependency_invalid")
        dependency = PrivateDependency.parse(raw_dependency)
        dependency_repository = dependency.repository
        dependency_sha = dependency.sha
        dependency_subdirectory = dependency.subdirectory
        dependency_id = dependency.dependency_id

    apple = workflow_key == "validation.apple"
    return CentralProfileResolution(
        project_key=checked_project,
        test_profile=checked_profile,
        workflow_key=workflow_key,
        capability=capability,
        source_repository=checked_repository,
        admitted_sha=checked_sha,
        validation_scope="legacy" if apple else "protected-full",
        validation_plan_json=inputs.get("validation_plan_json", ""),
        executor_family="macos" if apple else "linux",
        canonical_inputs_json=json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        private_dependency_repository=dependency_repository,
        private_dependency_sha=dependency_sha,
        private_dependency_subdirectory=dependency_subdirectory,
        private_dependency_id=dependency_id,
    )


def resolve_profile(*, source_root: object, project_key: object, workflow_key: object, test_profile: object, source_repository: object, admitted_sha: object) -> CentralProfileResolution:
    checked_project = _safe_project(project_key)
    checked_workflow = _safe_workflow_key(workflow_key)
    checked_profile = _safe_profile(test_profile)
    checked_repository = _safe_repository(source_repository)
    checked_sha = _safe_sha(admitted_sha)
    value = _read_config(_source_root(source_root))
    version = value.get("schema_version")
    if version == 1:
        return _resolve_v1_apple(value, checked_project=checked_project, checked_workflow=checked_workflow, checked_profile=checked_profile, checked_repository=checked_repository, checked_sha=checked_sha)
    if version == 2:
        return _resolve_v2_generic(value, checked_project=checked_project, checked_workflow=checked_workflow, checked_profile=checked_profile, checked_repository=checked_repository, checked_sha=checked_sha)
    raise CentralProfileError("private_ci_config_version")


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
    except (CentralProfileError, PrivateReleaseAssetError) as error:
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
