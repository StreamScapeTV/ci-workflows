"""Public facade for exact source admission and checkout primitives."""
from __future__ import annotations

from .source_admission import admit_source, revalidate_admission
from .source_checkout import exact_checkout
from .source_cli import main, resolve_from_environment
from .source_github import GitHubSourceProvider
from .source_types import (
    AdmissionResult,
    EventContext,
    PullRequestEvidence,
    SourceAdmissionError,
    SourceInputs,
    SourceMode,
    SourceProvider,
    TrustMode,
    load_contract,
    load_event_context,
    validate_inputs,
)

__all__ = (
    "AdmissionResult",
    "EventContext",
    "GitHubSourceProvider",
    "PullRequestEvidence",
    "SourceAdmissionError",
    "SourceInputs",
    "SourceMode",
    "SourceProvider",
    "TrustMode",
    "admit_source",
    "exact_checkout",
    "load_contract",
    "load_event_context",
    "main",
    "resolve_from_environment",
    "revalidate_admission",
    "validate_inputs",
)

if __name__ == "__main__":
    raise SystemExit(main())
