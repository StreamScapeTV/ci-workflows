"Resolve and revalidate exact release-tag authority for trusted publication."
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)"
    r"\.(?:0|[1-9][0-9]*)"
    r"\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
FULL_SHA = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
WORKFLOW_PATH = re.compile(r"\.github/workflows/[A-Za-z0-9._/-]+\.(?:yml|yaml)")
ALLOWED_MODES = {"tag-push", "existing-tag"}
WRITE_PERMISSIONS = {"admin", "maintain", "write"}
MAX_TAG_DEREFERENCE_DEPTH = 8


class ReleaseTagError(RuntimeError):
    """Fail-closed, response-free release tag error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReleaseInputs:
    release_mode: str = "tag-push"
    release_version: str | None = None
    release_source_sha: str | None = None


@dataclass(frozen=True)
class ReleaseEvent:
    event_name: str
    repository: str
    event_repository: str
    event_repository_fork: bool
    ref: str
    ref_type: str
    ref_name: str
    sha: str
    actor: str
    workflow_ref: str


@dataclass(frozen=True)
class TagResolution:
    tag_name: str
    tag_object_sha: str
    tag_commit_sha: str
    dereference_depth: int


@dataclass(frozen=True)
class ReleaseAuthority:
    release_mode: str
    release_version: str
    release_source_sha: str
    tag_object_sha: str
    tag_commit_sha: str


class TagProvider(Protocol):
    def repository_metadata(self, repository: str) -> Mapping[str, Any]: ...
    def collaborator_permission(
        self, repository: str, actor: str
    ) -> Mapping[str, Any]: ...
    def branch_sha(self, repository: str, branch: str) -> str: ...
    def tag_ref(self, repository: str, tag_name: str) -> Mapping[str, Any]: ...
    def tag_object(self, repository: str, sha: str) -> Mapping[str, Any]: ...
    def commit(self, repository: str, sha: str) -> Mapping[str, Any]: ...


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseTagError(code)


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), code)
    return value


def _exact_optional(value: str | None, code: str) -> str | None:
    if value in {None, ""}:
        return None
    _require(isinstance(value, str), code)
    _require(value == value.strip(), code)
    return value


def _full_sha(value: Any, code: str) -> str:
    _require(isinstance(value, str) and FULL_SHA.fullmatch(value) is not None, code)
    return value


def _semver(value: Any, code: str = "invalid_release_version") -> str:
    _require(isinstance(value, str) and SEMVER.fullmatch(value) is not None, code)
    return value


def _repository(value: Any, code: str = "invalid_repository") -> str:
    _require(
        isinstance(value, str) and REPOSITORY.fullmatch(value) is not None,
        code,
    )
    return value


def _object(payload: Mapping[str, Any], code: str) -> tuple[str, str]:
    obj = _mapping(payload.get("object"), code)
    object_type = obj.get("type")
    _require(isinstance(object_type, str) and bool(object_type), code)
    return object_type, _full_sha(obj.get("sha"), "invalid_tag_object_sha")


def resolve_tag(
    provider: TagProvider,
    repository: str,
    tag_name: str,
) -> TagResolution:
    """Resolve one lightweight or bounded annotated tag to an exact commit."""

    repository = _repository(repository)
    tag_name = _semver(tag_name)
    object_type, current_sha = _object(
        provider.tag_ref(repository, tag_name),
        "invalid_tag_ref",
    )
    tag_object_sha = current_sha
    visited: set[str] = set()
    depth = 0
    while True:
        _require(current_sha not in visited, "tag_object_cycle")
        visited.add(current_sha)
        if object_type == "commit":
            commit = _mapping(
                provider.commit(repository, current_sha),
                "invalid_tag_commit",
            )
            _require(
                _full_sha(commit.get("sha"), "invalid_tag_commit_sha")
                == current_sha,
                "tag_commit_mismatch",
            )
            return TagResolution(
                tag_name=tag_name,
                tag_object_sha=tag_object_sha,
                tag_commit_sha=current_sha,
                dereference_depth=depth,
            )
        _require(object_type == "tag", "unsupported_tag_object_type")
        _require(
            depth < MAX_TAG_DEREFERENCE_DEPTH,
            "tag_dereference_too_deep",
        )
        nested = _mapping(
            provider.tag_object(repository, current_sha),
            "invalid_tag_object",
        )
        object_type, current_sha = _object(
            nested,
            "invalid_tag_object",
        )
        depth += 1


def _validate_event_repository(event: ReleaseEvent) -> None:
    repository = _repository(event.repository)
    _require(event.event_repository == repository, "caller_repository_mismatch")
    _require(not event.event_repository_fork, "fork_repository_forbidden")


def _validate_existing_tag_caller(
    event: ReleaseEvent,
    provider: TagProvider,
    *,
    require_current_branch: bool = True,
) -> None:
    _validate_event_repository(event)
    _require(
        event.event_name == "workflow_dispatch",
        "existing_tag_event_forbidden",
    )
    metadata = _mapping(
        provider.repository_metadata(event.repository),
        "invalid_repository_metadata",
    )
    _require(metadata.get("full_name") == event.repository, "repository_identity_mismatch")
    _require(metadata.get("fork") is False, "fork_repository_forbidden")
    default_branch = metadata.get("default_branch")
    _require(
        isinstance(default_branch, str) and bool(default_branch),
        "invalid_default_branch",
    )
    _require(event.ref_type in {"", "branch"}, "trusted_caller_ref_type_mismatch")
    _require(event.ref_name == default_branch, "trusted_caller_branch_mismatch")
    _require(
        event.ref == f"refs/heads/{default_branch}",
        "trusted_caller_ref_mismatch",
    )
    caller_sha = _full_sha(event.sha, "invalid_caller_sha")
    if require_current_branch:
        _require(
            caller_sha == provider.branch_sha(event.repository, default_branch),
            "stale_trusted_caller_source",
        )
    workflow_prefix = f"{event.repository}/"
    _require(event.workflow_ref.startswith(workflow_prefix), "caller_workflow_mismatch")
    workflow_and_ref = event.workflow_ref[len(workflow_prefix):]
    _require(
        "@" in workflow_and_ref,
        "caller_workflow_mismatch",
    )
    workflow_path, workflow_ref = workflow_and_ref.rsplit("@", 1)
    _require(
        WORKFLOW_PATH.fullmatch(workflow_path) is not None,
        "caller_workflow_mismatch",
    )
    _require(
        workflow_ref == f"refs/heads/{default_branch}",
        "caller_workflow_not_default_branch",
    )
    _require(
        isinstance(event.actor, str) and bool(event.actor),
        "missing_trusted_actor",
    )
    permission = _mapping(
        provider.collaborator_permission(event.repository, event.actor),
        "invalid_actor_permission",
    ).get("permission")
    _require(permission in WRITE_PERMISSIONS, "trusted_actor_write_required")


def resolve_release_authority(
    inputs: ReleaseInputs,
    event: ReleaseEvent,
    provider: TagProvider,
) -> ReleaseAuthority:
    """Validate mode/event authority and resolve the exact immutable tag tuple."""

    _validate_event_repository(event)
    mode = inputs.release_mode or "tag-push"
    _require(mode in ALLOWED_MODES, "unknown_release_mode")
    requested_version = _exact_optional(
        inputs.release_version,
        "invalid_release_version",
    )
    requested_source = _exact_optional(
        inputs.release_source_sha,
        "invalid_release_source_sha",
    )
    has_version = requested_version is not None
    has_source = requested_source is not None
    _require(has_version == has_source, "partial_explicit_release_tuple")

    if mode == "tag-push":
        _require(not has_version, "mixed_release_authority")
        _require(event.event_name == "push", "tag_push_event_required")
        _require(event.ref_type == "tag", "tag_push_ref_type_required")
        _require(bool(event.ref_name), "tag_push_ref_name_required")
        version = _semver(event.ref_name)
        _require(event.ref == f"refs/tags/{version}", "tag_push_ref_mismatch")
        event_sha = _full_sha(event.sha, "invalid_tag_event_sha")
        resolution = resolve_tag(provider, event.repository, version)
        _require(
            event_sha
            in {resolution.tag_object_sha, resolution.tag_commit_sha},
            "tag_event_sha_mismatch",
        )
        source_sha = resolution.tag_commit_sha
    else:
        _require(has_version and has_source, "explicit_release_tuple_required")
        version = _semver(requested_version)
        source_sha = _full_sha(
            requested_source,
            "invalid_release_source_sha",
        )
        _validate_existing_tag_caller(event, provider)
        resolution = resolve_tag(provider, event.repository, version)
        _require(
            resolution.tag_commit_sha == source_sha,
            "tag_source_mismatch",
        )

    return ReleaseAuthority(
        release_mode=mode,
        release_version=version,
        release_source_sha=source_sha,
        tag_object_sha=resolution.tag_object_sha,
        tag_commit_sha=resolution.tag_commit_sha,
    )


def revalidate_release_authority(
    authority: ReleaseAuthority,
    event: ReleaseEvent,
    provider: TagProvider,
) -> ReleaseAuthority:
    """Revalidate the exact tag object and commit immediately before publication."""

    _validate_event_repository(event)
    _require(authority.release_mode in ALLOWED_MODES, "unknown_release_mode")
    version = _semver(authority.release_version)
    source_sha = _full_sha(
        authority.release_source_sha,
        "invalid_release_source_sha",
    )
    expected_object = _full_sha(
        authority.tag_object_sha,
        "invalid_expected_tag_object_sha",
    )
    expected_commit = _full_sha(
        authority.tag_commit_sha,
        "invalid_expected_tag_commit_sha",
    )
    _require(source_sha == expected_commit, "expected_tag_source_mismatch")

    if authority.release_mode == "tag-push":
        _require(event.event_name == "push", "tag_push_event_required")
        _require(event.ref_type == "tag", "tag_push_ref_type_required")
        _require(event.ref_name == version, "tag_push_ref_name_mismatch")
        _require(event.ref == f"refs/tags/{version}", "tag_push_ref_mismatch")
        _require(
            _full_sha(event.sha, "invalid_tag_event_sha")
            in {expected_object, expected_commit},
            "tag_event_sha_mismatch",
        )
    else:
        _validate_existing_tag_caller(
            event,
            provider,
            require_current_branch=False,
        )

    current = resolve_tag(provider, event.repository, version)
    _require(
        current.tag_object_sha == expected_object,
        "release_tag_moved",
    )
    _require(
        current.tag_commit_sha == expected_commit,
        "release_tag_source_changed",
    )
    return authority


class GitHubTagProvider:
    """Minimal read-only GitHub REST client with sanitized failures."""

    def __init__(
        self,
        *,
        api_url: str,
        token: str,
        opener: Any = urllib.request.urlopen,
    ) -> None:
        parsed = urllib.parse.urlsplit(api_url.rstrip("/"))
        _require(
            parsed.scheme == "https"
            and parsed.hostname == "api.github.com"
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
            and parsed.query == ""
            and parsed.fragment == "",
            "unsupported_github_api_url",
        )
        _require(isinstance(token, str) and bool(token), "github_token_required")
        self._api_url = api_url.rstrip("/")
        self._token = token
        self._opener = opener

    def _get(
        self,
        repository: str,
        path: str,
        *,
        missing_code: str,
    ) -> Mapping[str, Any]:
        repository = _repository(repository)
        owner, name = repository.split("/", 1)
        url = (
            f"{self._api_url}/repos/"
            f"{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(name, safe='')}/{path}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ReleaseTagError(missing_code) from None
            raise ReleaseTagError(f"github_api_http_{error.code}") from None
        except (OSError, ValueError, json.JSONDecodeError):
            raise ReleaseTagError("github_api_unavailable") from None
        return _mapping(payload, "github_api_invalid_response")

    def repository_metadata(self, repository: str) -> Mapping[str, Any]:
        return self._get(repository, "", missing_code="repository_missing")

    def collaborator_permission(
        self,
        repository: str,
        actor: str,
    ) -> Mapping[str, Any]:
        _require(
            isinstance(actor, str)
            and re.fullmatch(r"[A-Za-z0-9-]{1,39}", actor) is not None,
            "invalid_actor",
        )
        return self._get(
            repository,
            f"collaborators/{urllib.parse.quote(actor, safe='')}/permission",
            missing_code="actor_not_repository_collaborator",
        )

    def branch_sha(self, repository: str, branch: str) -> str:
        _require(isinstance(branch, str) and bool(branch), "invalid_branch")
        payload = self._get(
            repository,
            f"git/ref/heads/{urllib.parse.quote(branch, safe='')}",
            missing_code="default_branch_missing",
        )
        object_type, sha = _object(payload, "invalid_branch_ref")
        _require(object_type == "commit", "default_branch_not_commit")
        return sha

    def tag_ref(
        self,
        repository: str,
        tag_name: str,
    ) -> Mapping[str, Any]:
        return self._get(
            repository,
            f"git/ref/tags/{urllib.parse.quote(tag_name, safe='')}",
            missing_code="release_tag_missing",
        )

    def tag_object(
        self,
        repository: str,
        sha: str,
    ) -> Mapping[str, Any]:
        return self._get(
            repository,
            f"git/tags/{_full_sha(sha, 'invalid_tag_object_sha')}",
            missing_code="annotated_tag_object_missing",
        )

    def commit(
        self,
        repository: str,
        sha: str,
    ) -> Mapping[str, Any]:
        return self._get(
            repository,
            f"git/commits/{_full_sha(sha, 'invalid_tag_commit_sha')}",
            missing_code="tag_commit_missing",
        )


def event_from_environment(environment: Mapping[str, str]) -> ReleaseEvent:
    """Build the exact caller event context without trusting caller inputs."""

    event_path = environment.get("GITHUB_EVENT_PATH", "")
    _require(bool(event_path), "github_event_path_required")
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ReleaseTagError("github_event_unavailable") from None
    event_payload = _mapping(payload, "github_event_invalid")
    event_repository = _mapping(
        event_payload.get("repository"),
        "github_event_repository_missing",
    )
    return ReleaseEvent(
        event_name=environment.get("GITHUB_EVENT_NAME", ""),
        repository=environment.get("GITHUB_REPOSITORY", ""),
        event_repository=str(event_repository.get("full_name", "")),
        event_repository_fork=event_repository.get("fork") is True,
        ref=environment.get("GITHUB_REF", ""),
        ref_type=environment.get("GITHUB_REF_TYPE", ""),
        ref_name=environment.get("GITHUB_REF_NAME", ""),
        sha=environment.get("GITHUB_SHA", ""),
        actor=environment.get("GITHUB_ACTOR", ""),
        workflow_ref=environment.get("GITHUB_WORKFLOW_REF", ""),
    )


def authority_from_expected(
    *,
    release_mode: str,
    release_version: str,
    release_source_sha: str,
    tag_object_sha: str,
    tag_commit_sha: str,
) -> ReleaseAuthority:
    return ReleaseAuthority(
        release_mode=release_mode,
        release_version=release_version,
        release_source_sha=release_source_sha,
        tag_object_sha=tag_object_sha,
        tag_commit_sha=tag_commit_sha,
    )


def write_outputs(path: Path, authority: ReleaseAuthority) -> None:
    values: Sequence[tuple[str, str]] = (
        ("release_mode", authority.release_mode),
        ("release_version", authority.release_version),
        ("release_source_sha", authority.release_source_sha),
        ("tag_object_sha", authority.tag_object_sha),
        ("tag_commit_sha", authority.tag_commit_sha),
    )
    with path.open("a", encoding="utf-8") as output:
        for key, value in values:
            output.write(f"{key}={value}\n")
