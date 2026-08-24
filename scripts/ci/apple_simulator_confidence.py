#!/usr/bin/env python3
"""Thin action adapter for the strict validation.apple simulator-confidence mode."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import apple as apple_validation  # noqa: E402
from ci_workflows import apple_execution  # noqa: E402
from ci_workflows.apple_contract_fragments import load_apple_contract  # noqa: E402
from ci_workflows.apple_simulator_confidence import (  # noqa: E402
    build_simulator_confidence_packet,
    confidence_outputs,
)
from ci_workflows.ciw_apple import (  # noqa: E402
    _diagnostic_runner,
    _resolved_state_root,
    _source_path,
    _source_trust,
)


def _write(values: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT", "")
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            for key, value in sorted(values.items()):
                handle.write(f"{key}={value}\n")
    print(json.dumps(values, sort_keys=True, separators=(",", ":")))


def _packet():
    contract = load_apple_contract(ROOT)
    return build_simulator_confidence_packet(
        os.environ.get("INPUT_VALIDATION_PLAN_JSON", ""),
        repository=os.environ.get("GITHUB_REPOSITORY", ""),
        admitted_sha=os.environ.get("INPUT_ADMITTED_SHA", ""),
        source_trust=_source_trust(os.environ),
        contract=contract,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("plan", "execute", "cleanup", "residue"), required=True)
    parser.add_argument("--source-root", default="source")
    args = parser.parse_args()
    try:
        packet = _packet()
        if args.phase == "plan":
            _write(packet.planning_outputs())
            return 0

        source = _source_path(ROOT, args.source_root, os.environ)
        state = _resolved_state_root(ROOT, os.environ)
        runner = _diagnostic_runner(source, state)
        if args.phase == "cleanup":
            apple_validation.cleanup_apple_state(source, state, packet.apple_plan)
            _write({"cleanup_result": "success", "failure_code": ""})
            return 0
        if args.phase == "residue":
            apple_validation.assert_zero_apple_residue(source, state, packet.apple_plan)
            _write({"cleanup_result": "success", "failure_code": ""})
            return 0

        result = apple_execution.execute_apple_plan(
            plan=packet.apple_plan,
            source_root=source,
            state_root=state,
            runner=runner,
            environment=os.environ,
        )
        _write(confidence_outputs(result.output_values(), packet))
        return 0
    except apple_validation.AppleValidationError as error:
        _write(
            {
                "result": "failure",
                "cleanup_result": "failure" if error.cleanup_failed else "not-run",
                "failure_code": error.code,
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
