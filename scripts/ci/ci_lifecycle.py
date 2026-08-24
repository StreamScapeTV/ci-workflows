#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.ci_lifecycle import (  # noqa: E402
    AgentStateCiClient,
    CiLifecycleError,
    WorkflowIdentity,
)


def _required(environment: dict[str, str], name: str) -> str:
    value = environment.get(name, "")
    if not value:
        raise CiLifecycleError(f"missing_{name.lower()}")
    return value


def _write_output(name: str, value: str, environment: dict[str, str]) -> None:
    path = environment.get("GITHUB_OUTPUT", "")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _identity(environment: dict[str, str]) -> WorkflowIdentity:
    return WorkflowIdentity.from_values(
        project_key=_required(environment, "INPUT_PROJECT_KEY"),
        repository=_required(environment, "INPUT_REPOSITORY"),
        ref=_required(environment, "INPUT_REF"),
        is_tag=_required(environment, "INPUT_IS_TAG"),
        workflow_key=_required(environment, "INPUT_WORKFLOW_KEY"),
        profile=_required(environment, "INPUT_PROFILE"),
        environment=environment,
    )


def start(environment: dict[str, str]) -> None:
    client = AgentStateCiClient.from_environment(environment)
    ci_run_id = client.start(
        _identity(environment),
        environment.get("INPUT_CI_RUN_ID", "") or None,
    )
    _write_output("ci_run_id", ci_run_id, environment)


def evidence(environment: dict[str, str]) -> None:
    client = AgentStateCiClient.from_environment(environment)
    client.evidence(
        _required(environment, "INPUT_CI_RUN_ID"),
        _required(environment, "INPUT_OBSERVED_SHA"),
    )
    _write_output("ci_run_id", _required(environment, "INPUT_CI_RUN_ID"), environment)


def finish(environment: dict[str, str]) -> None:
    client = AgentStateCiClient.from_environment(environment)
    ci_run_id = _required(environment, "INPUT_CI_RUN_ID")
    client.finish(
        ci_run_id,
        status=_required(environment, "INPUT_TERMINAL_STATUS"),
        error_summary=environment.get("INPUT_ERROR_SUMMARY", "") or None,
        diagnostic_status=environment.get("INPUT_DIAGNOSTIC_STATUS", "") or None,
        diagnostic_key=environment.get("INPUT_DIAGNOSTIC_KEY", "") or None,
    )
    _write_output("ci_run_id", ci_run_id, environment)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Shared Central CI Agent State lifecycle")
    result.add_argument("phase", choices=("start", "evidence", "finish"))
    return result


def main(argv: list[str] | None = None, environment: dict[str, str] | None = None) -> int:
    args = parser().parse_args(argv)
    selected = dict(os.environ if environment is None else environment)
    try:
        if args.phase == "start":
            start(selected)
        elif args.phase == "evidence":
            evidence(selected)
        else:
            finish(selected)
    except CiLifecycleError as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
