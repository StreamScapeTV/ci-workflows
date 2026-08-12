"""Fail-closed GitHub Release creation and exact replay verification."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .release_contract import validate_release_tag, validate_release_version
from .release_manifest import canonical_json, sha256_text
from .release_types import ReleaseError, ReleasePlan


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^StreamScapeTV/[A-Za-z0-9_.-]+$")
RELEASE_TAG = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
MARKER_PREFIX = "<!-- streamscape-release-manifest-sha256:"


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


@dataclass(frozen=True)
class DesiredGitHubRelease:
    repository: str
    tag_name: str
    target_commitish: str
    name: str
    body: str
    draft: bool
    prerelease: bool

    def create_payload(self) -> dict[str, Any]:
        # target_commitish is a fail-safe only for GitHub's tag-creation path.
        # Product release authority is the separately resolved and revalidated Git
        # tag; GitHub documents target_commitish as unused when that tag exists.
        return {
            "tag_name": self.tag_name,
            "target_commitish": self.target_commitish,
            "name": self.name,
            "body": self.body,
            "draft": self.draft,
            "prerelease": self.prerelease,
            "generate_release_notes": False,
        }


class ReleaseAPI(Protocol):
    def get_by_tag(self, tag: str) -> Mapping[str, Any] | None: ...

    def create(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


def release_body(manifest_json: str, manifest_sha256: str) -> str:
    require(
        isinstance(manifest_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is not None,
        "manifest_digest_rejected",
    )
    require(isinstance(manifest_json, str), "manifest_json_rejected")
    try:
        parsed = json.loads(manifest_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ReleaseError("manifest_json_rejected") from error
    canonical = canonical_json(parsed)
    require(sha256_text(canonical) == manifest_sha256, "manifest_digest_mismatch")
    return (
        f"{MARKER_PREFIX}{manifest_sha256} -->\n"
        "Immutable release manifest (canonical JSON):\n\n"
        "```json\n"
        f"{canonical}\n"
        "```\n"
    )


def desired_release(
    *,
    plan: ReleasePlan,
    release_tag: str,
    release_version: str,
    source_sha: str,
    manifest_json: str,
    manifest_sha256: str,
) -> DesiredGitHubRelease:
    version = validate_release_version(release_version)
    tag = validate_release_tag(release_tag, version)
    require(
        isinstance(source_sha, str) and FULL_SHA.fullmatch(source_sha) is not None,
        "release_sha_rejected",
    )
    require(REPOSITORY.fullmatch(plan.repository) is not None, "repository_rejected")
    return DesiredGitHubRelease(
        repository=plan.repository,
        tag_name=tag,
        target_commitish=source_sha,
        name=f"{plan.release_id} {version}",
        body=release_body(manifest_json, manifest_sha256),
        draft=False,
        prerelease=False,
    )


def verify_existing_release(
    existing: Mapping[str, Any], desired: DesiredGitHubRelease
) -> str:
    # Source identity is intentionally not inferred from target_commitish. GitHub
    # defines that field as unused when tag_name already exists; the exact source
    # is instead enforced by the release tag authority before this boundary.
    require(existing.get("tag_name") == desired.tag_name, "github_release_conflict")
    require(existing.get("name") == desired.name, "github_release_conflict")
    require(existing.get("body") == desired.body, "github_release_conflict")
    require(existing.get("draft") is desired.draft, "github_release_conflict")
    require(
        existing.get("prerelease") is desired.prerelease,
        "github_release_conflict",
    )
    url = existing.get("html_url")
    expected_url = (
        f"https://github.com/{desired.repository}/releases/tag/{desired.tag_name}"
    )
    require(url == expected_url, "github_release_response_rejected")
    return expected_url


def ensure_github_release(
    api: ReleaseAPI, desired: DesiredGitHubRelease
) -> tuple[str, str]:
    existing = api.get_by_tag(desired.tag_name)
    if existing is not None:
        return verify_existing_release(existing, desired), "existing-matched"
    try:
        created = api.create(desired.create_payload())
    except ReleaseError as error:
        if error.code != "github_release_create_conflict":
            raise
        raced = api.get_by_tag(desired.tag_name)
        require(raced is not None, "github_release_create_failed")
        return verify_existing_release(raced, desired), "existing-matched-after-race"
    return verify_existing_release(created, desired), "created"


class GitHubReleaseAPI:
    """Minimal token-bearing transport isolated to the GitHub Release job."""

    def __init__(self, repository: str, token: str) -> None:
        require(REPOSITORY.fullmatch(repository) is not None, "repository_rejected")
        require(bool(token), "github_token_missing")
        self.repository = repository
        self._token = token

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        url = f"https://api.github.com/repos/{self.repository}{path}"
        data = None if payload is None else canonical_json(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "StreamScapeTV-ci-workflows-release",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read(1024 * 1024)
        except urllib.error.HTTPError as error:
            if error.code == 404 and method == "GET":
                return None
            if error.code == 422 and method == "POST":
                raise ReleaseError("github_release_create_conflict") from error
            raise ReleaseError("github_release_api_failed") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ReleaseError("github_release_api_failed") from error
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReleaseError("github_release_response_rejected") from error
        require(isinstance(parsed, Mapping), "github_release_response_rejected")
        return parsed

    def get_by_tag(self, tag: str) -> Mapping[str, Any] | None:
        require(isinstance(tag, str), "release_tag_rejected")
        candidate = tag.strip()
        require(RELEASE_TAG.fullmatch(candidate) is not None, "release_tag_rejected")
        return self._request(
            "GET", f"/releases/tags/{urllib.parse.quote(candidate, safe='')}"
        )

    def create(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self._request("POST", "/releases", payload)
        require(result is not None, "github_release_response_rejected")
        return result
