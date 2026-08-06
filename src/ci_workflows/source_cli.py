"""Thin environment and CLI adapters for exact source primitives."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .source_admission import admit_source
from .source_checkout import exact_checkout
from .source_github import GitHubSourceProvider
from .source_types import (
    AdmissionResult,
    SourceAdmissionError,
    _require,
    load_contract,
    load_event_context,
    validate_inputs,
)


def _read_event(path: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceAdmissionError("event_payload_unavailable") from error
    _require(isinstance(payload, Mapping), "event_payload_invalid")
    return payload


def _write_outputs(path: str, values: Mapping[str, str]) -> None:
    output = Path(path)
    with output.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            _require(
                "\n" not in value and "\r" not in value,
                "unsafe_output_value",
            )
            stream.write(f"{key}={value}\n")


def _write_summary(path: str, result: AdmissionResult) -> None:
    lines = [
        "## Exact source admission",
        "",
        f"- Repository: `{result.caller_repository}`",
        f"- Trust mode: `{result.trust_mode.value}`",
        f"- Exact source: `{result.source_sha}`",
        f"- Evidence: `{result.evidence_id}`",
        (
            "- Freshness revalidation required: "
            f"`{str(result.requires_freshness).lower()}`"
        ),
    ]
    if result.pr_number is not None:
        lines.append(f"- Pull request: `#{result.pr_number}`")
    if result.tag_name is not None:
        lines.append(f"- Tag: `{result.tag_name}`")
    Path(path).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _input_environment() -> dict[str, Any]:
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
        name: os.environ.get(f"INPUT_{name.upper()}", "")
        for name in names
    }


def resolve_from_environment(root: Path) -> AdmissionResult:
    contract = load_contract(root)
    inputs = validate_inputs(_input_environment(), contract)
    payload = _read_event(os.environ.get("GITHUB_EVENT_PATH", ""))
    event = load_event_context(os.environ, payload)
    provider = GitHubSourceProvider(os.environ.get("GITHUB_TOKEN", ""))
    return admit_source(inputs, event, provider)


def _resolve_command(root: Path) -> int:
    try:
        result = resolve_from_environment(root)
        outputs = result.output_values()
        output_path = os.environ.get("GITHUB_OUTPUT", "")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
        _require(bool(output_path), "github_output_missing")
        _require(bool(summary_path), "github_summary_missing")
        _write_outputs(output_path, outputs)
        _write_summary(summary_path, result)
        return 0
    except SourceAdmissionError as error:
        print(
            f"source admission failed: {error.instruction}",
            file=sys.stderr,
        )
        return 2


def _checkout_command(arguments: argparse.Namespace) -> int:
    try:
        outputs = exact_checkout(
            repository=arguments.repository,
            admitted_sha=arguments.admitted_sha,
            path=arguments.path,
            fetch_depth=arguments.fetch_depth,
            token=os.environ.get("CHECKOUT_TOKEN", ""),
            workspace=Path(os.environ.get("GITHUB_WORKSPACE", ".")),
        )
        output_path = os.environ.get("GITHUB_OUTPUT", "")
        _require(bool(output_path), "github_output_missing")
        _write_outputs(output_path, outputs)
        return 0
    except SourceAdmissionError as error:
        print(
            f"exact checkout failed: {error.instruction}",
            file=sys.stderr,
        )
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
    )
    resolve = subcommands.add_parser(
        "resolve",
        help="Resolve event source admission",
    )
    resolve.add_argument("--root", type=Path, required=True)
    checkout = subcommands.add_parser(
        "exact-checkout",
        help="Check out one admitted SHA",
    )
    checkout.add_argument("--repository", required=True)
    checkout.add_argument("--admitted-sha", required=True)
    checkout.add_argument("--path", default="source")
    checkout.add_argument("--fetch-depth", type=int, default=1)
    arguments = parser.parse_args(argv)
    if arguments.command == "resolve":
        return _resolve_command(arguments.root)
    return _checkout_command(arguments)
