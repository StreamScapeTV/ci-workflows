"""Typed identities and gateway contracts for issue dependency reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, Sequence

ORGANIZATION = "StreamScapeTV"
GITHUB_WEB_ROOT = "https://github.com"
MANIFEST_PATH = "ISSUE_DEPENDENCIES.yml"
AGENTS_PATH = "AGENTS.md"

_REPOSITORY_RE = re.compile(r"^StreamScapeTV/[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$")
_ISSUE_URL_RE = re.compile(
    r"^https://github\.com/StreamScapeTV/"
    r"(?P<repo>[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99}))"
    r"/issues/(?P<number>[1-9][0-9]*)$"
)
_NATIVE_ISSUE_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99}))"
    r"/issues/(?P<number>[1-9][0-9]*)$"
)
_PROTECTED_BRANCH_RE = re.compile(
    r"^- Protected integration branch: `(?P<branch>[^`\r\n]+)`$",
    re.MULTILINE,
)
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

class DependencySyncError(RuntimeError):
    """Base dependency reconciliation failure."""


class ManifestValidationError(DependencySyncError):
    """Manifest or semantic contract validation failure."""


class ConvergenceError(DependencySyncError):
    """Native GitHub dependency state did not converge."""


@dataclass(frozen=True, order=True)
class RepositoryRecord:
    full_name: str
    default_branch: str


@dataclass(frozen=True, order=True)
class IssueRef:
    repository: str
    number: int

    def __post_init__(self) -> None:
        if not _REPOSITORY_RE.fullmatch(self.repository):
            raise ManifestValidationError(
                f"unsupported repository identity: {self.repository!r}"
            )
        if isinstance(self.number, bool) or not isinstance(self.number, int) or self.number < 1:
            raise ManifestValidationError(f"invalid issue number: {self.number!r}")

    @property
    def repo_name(self) -> str:
        return self.repository.split("/", 1)[1]

    @property
    def url(self) -> str:
        return f"{GITHUB_WEB_ROOT}/{self.repository}/issues/{self.number}"

    @classmethod
    def from_url(cls, value: str) -> "IssueRef":
        match = _ISSUE_URL_RE.fullmatch(value)
        if not match:
            raise ManifestValidationError(f"invalid blocker issue URL: {value!r}")
        return cls(
            repository=f"{ORGANIZATION}/{match.group('repo')}",
            number=int(match.group("number")),
        )


@dataclass(frozen=True)
class IssueRecord:
    ref: IssueRef
    issue_id: int
    state: str
    state_reason: str | None
    is_pull_request: bool = False


@dataclass(frozen=True)
class NativeDependency:
    url: str
    issue_id: int

    def __post_init__(self) -> None:
        if not _NATIVE_ISSUE_URL_RE.fullmatch(self.url):
            raise DependencySyncError(
                f"GitHub returned a non-issue dependency URL: {self.url!r}"
            )
        if isinstance(self.issue_id, bool) or not isinstance(self.issue_id, int) or self.issue_id < 1:
            raise DependencySyncError(
                f"GitHub returned an invalid issue id for {self.url!r}"
            )


@dataclass(frozen=True)
class ManagedIssue:
    dependent: IssueRef
    blockers: tuple[IssueRef, ...]


@dataclass(frozen=True)
class RepositoryManifest:
    repository: str
    integration_branch: str
    issues: tuple[ManagedIssue, ...]


@dataclass(frozen=True)
class DependencyPlan:
    dependent: IssueRef
    desired_urls: tuple[str, ...]
    additions: tuple[IssueRecord, ...]
    removals: tuple[NativeDependency, ...]


@dataclass(frozen=True)
class SyncSummary:
    repositories_scanned: int
    managed_repositories: int
    managed_issues: int
    desired_edges: int
    additions: int
    removals: int
    mutations: int

    def as_dict(self) -> dict[str, int]:
        return {
            "additions": self.additions,
            "desired_edges": self.desired_edges,
            "managed_issues": self.managed_issues,
            "managed_repositories": self.managed_repositories,
            "mutations": self.mutations,
            "removals": self.removals,
            "repositories_scanned": self.repositories_scanned,
        }


class IssueDependencyGateway(Protocol):
    """Bounded GitHub surface used by the pure reconciliation engine."""

    def list_repositories(self) -> Sequence[RepositoryRecord]:
        ...

    def read_file(self, repository: str, path: str, ref: str) -> str | None:
        ...

    def get_issue(self, ref: IssueRef) -> IssueRecord | None:
        ...

    def list_blocked_by(self, ref: IssueRef) -> Sequence[NativeDependency]:
        ...

    def add_blocked_by(self, dependent: IssueRef, blocker: IssueRecord) -> None:
        ...

    def remove_blocked_by(
        self, dependent: IssueRef, blocker: NativeDependency
    ) -> None:
        ...


def parse_protected_integration_branch(agents_text: str) -> str | None:
    """Return the one standard protected branch declaration or ``None``."""
    matches = [match.group("branch") for match in _PROTECTED_BRANCH_RE.finditer(agents_text)]
    if len(matches) != 1:
        return None
    branch = matches[0]
    if not _SAFE_BRANCH_RE.fullmatch(branch):
        return None
    if (
        branch.startswith("/")
        or branch.endswith("/")
        or branch.endswith(".")
        or branch.endswith(".lock")
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or "\\" in branch
    ):
        return None
    return branch
