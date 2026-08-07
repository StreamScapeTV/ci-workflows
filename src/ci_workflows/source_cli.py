"""Compatibility CLI adapters for exact source primitives."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .source_admission import admit_source
from .source_github import GitHubSourceProvider
from .source_types import (
    AdmissionResult,
    SourceAdmissionError,
    _require,
    load_contract,
    load_event_context,
    validate_inputs,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read_event(path: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceAdmissionError("event_payload_unavailable") from error
    _require(isinstance(payload, Mapping), "event_payload_invalid")
    return payload


def _input_environment(environment: Mapping[str, str]) -> dict[str, Any]:
    names = (
        "source_mode",
        "requested_sha",
        "expected_branch",
        "release_contract",
        "history_depth",
        "caller_repository",
        "caller_default_branch",
        "caller_integration_branch",
        "pr_number",
        "expected_pr_head_sha",
        "expected_pr_base_sha",
        "expected_pr_merge_sha",
    )
    return {
        name: environment.get(f"INPUT_{name.upper()}", "")
        for name in names
    }


def resolve_from_environment(
    root: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> AdmissionResult:
    """Resolve source from an explicit environment mapping or ``os.environ``."""

    values = os.environ if environment is None else environment
    contract = load_contract(root)
    inputs = validate_inputs(_input_environment(values), contract)
    payload = _read_event(values.get("GITHUB_EVENT_PATH", ""))
    event = load_event_context(values, payload)
    provider = GitHubSourceProvider(values.get("GITHUB_TOKEN", ""))
    return admit_source(inputs, event, provider)


def _legacy_parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command", required=True)
    resolve = subcommands.add_parser(
        "resolve",
        help="Resolve event source admission",
    )
    resolve.add_argument("--root", type=Path, default=_REPOSITORY_ROOT)
    checkout = subcommands.add_parser(
        "exact-checkout",
        help="Check out one admitted SHA",
    )
    checkout.add_argument("--repository", required=True)
    checkout.add_argument("--admitted-sha", required=True)
    checkout.add_argument("--path", default="source")
    checkout.add_argument("--fetch-depth", type=int, default=1)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Preserve the existing source CLI while delegating to ``ciw``."""

    arguments = _legacy_parser().parse_args(argv)
    from .ciw import main as ciw_main

    if arguments.command == "resolve":
        return ciw_main(
            [
                "--root",
                str(arguments.root),
                "source",
                "resolve",
            ]
        )
    return ciw_main(
        [
            "--root",
            str(_REPOSITORY_ROOT),
            "source",
            "exact-checkout",
            "--repository",
            arguments.repository,
            "--admitted-sha",
            arguments.admitted_sha,
            "--path",
            arguments.path,
            "--fetch-depth",
            str(arguments.fetch_depth),
        ]
    )
