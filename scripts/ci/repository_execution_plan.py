#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

OPERATIONS = ("build", "test", "full", "ui-test", "release")
OPERATING_SYSTEMS = ("linux", "macos")
MAX_PLAN_BYTES = 8192


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate execution-plan key: {key}")
        result[key] = value
    return result


def _load_plan(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise SystemExit("repository CI execution plan must be one regular non-symlink file")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_PLAN_BYTES:
        raise SystemExit("repository CI execution plan size is outside the reviewed bound")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit("repository CI execution plan must be one unambiguous UTF-8 JSON object") from exc
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "operations"}:
        raise SystemExit("repository CI execution plan top-level schema is invalid")
    if value.get("schemaVersion") != 1:
        raise SystemExit("repository CI execution plan schemaVersion must be 1")
    operations = value.get("operations")
    if not isinstance(operations, dict) or not operations:
        raise SystemExit("repository CI execution plan operations must be one non-empty object")
    if set(operations) - set(OPERATIONS):
        raise SystemExit("repository CI execution plan contains an unknown operation")
    for operation, operating_systems in operations.items():
        if not isinstance(operating_systems, list) or not 1 <= len(operating_systems) <= len(OPERATING_SYSTEMS):
            raise SystemExit(f"repository CI execution plan operation {operation} must select one or two reviewed operating systems")
        if any(not isinstance(item, str) or item not in OPERATING_SYSTEMS for item in operating_systems):
            raise SystemExit(f"repository CI execution plan operation {operation} contains an unknown operating system")
        if len(operating_systems) != len(set(operating_systems)):
            raise SystemExit(f"repository CI execution plan operation {operation} contains a duplicate operating system")
    return value


def select(plan_path: Path | None, operation: str, legacy_host_os: str) -> dict[str, object]:
    if operation not in OPERATIONS:
        raise SystemExit("repository CI execution-plan operation is unsupported")
    if legacy_host_os not in OPERATING_SYSTEMS:
        raise SystemExit("repository CI compatibility host_os must be linux or macos")
    if plan_path is None:
        return {
            "schemaVersion": 1,
            "source": "legacy-host-os-compatibility",
            "operation": operation,
            "operatingSystems": [legacy_host_os],
        }
    plan = _load_plan(plan_path)
    operations = plan["operations"]
    assert isinstance(operations, dict)
    if operation not in operations:
        raise SystemExit("repository CI execution plan does not declare the requested operation")
    selected = operations[operation]
    assert isinstance(selected, list)
    return {
        "schemaVersion": 1,
        "source": "tracked-repository-plan",
        "operation": operation,
        "operatingSystems": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and select the bounded repository-owned CI execution plan.")
    parser.add_argument("--operation", required=True, choices=OPERATIONS)
    parser.add_argument("--legacy-host-os", required=True, choices=OPERATING_SYSTEMS)
    parser.add_argument("--plan")
    args = parser.parse_args()
    plan_path = Path(args.plan) if args.plan else None
    print(json.dumps(select(plan_path, args.operation, args.legacy_host_os), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
