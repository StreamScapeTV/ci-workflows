"""Central-only parser for private `.github/central-ci.json` configuration.

This module is intentionally not part of the deployed CI broker. The transport
broker never reads product configuration or source repositories.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Mapping

MAX_CONFIG_BYTES = 64 * 1024

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WORKFLOW = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_SCALAR = re.compile(r"[^\r\n\x00]{1,512}\Z")
_DEPENDENCY_ID = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")


class PrivateCiConfigError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PrivateCiConfigError(code)


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
                _require(
                    isinstance(raw_dependency, dict),
                    "private_ci_dependency_invalid",
                )
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
        _require(
            set(automatic).issubset({"push", "tag"}),
            "private_ci_automatic_invalid",
        )
        for event, profile_name in automatic.items():
            _require(
                _safe_profile(profile_name) in profiles,
                "private_ci_automatic_profile_missing",
            )
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
