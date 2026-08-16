#!/usr/bin/env python3
"""Validate and render the checked-in organization workflow navigation inventory."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

SHA = re.compile(r"^[0-9a-f]{40}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COLUMNS = ("path", "name", "status", "disposition", "migration", "trust", "blob")
WORKFLOW_PREFIX = ".github/workflows/"
MIN_WORKFLOWS = 87


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc


def nonempty(value: Any, field: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{field} must be non-empty")
    return value


def paths(root: Path) -> dict[str, Path]:
    return {
        "inventory": root / "contracts/workflow-inventory.json",
        "ownership": root / "docs/architecture/ownership-boundaries.md",
        "report": root / "docs/inventory/workflows.md",
    }


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    location = paths(root)
    inventory = load_json(location["inventory"])
    require(inventory.get("schema_version") == 2, "unsupported inventory schema")
    require(inventory.get("organization") == "StreamScapeTV", "inventory organization mismatch")
    nonempty(inventory.get("captured_at"), "inventory.captured_at")
    require(tuple(inventory.get("workflow_columns", ())) == COLUMNS, "inventory column schema changed")
    tables: dict[str, Mapping[str, str]] = {}
    for field in ("dispositions", "trust_classes", "migration_classes"):
        table = inventory.get(field)
        require(
            isinstance(table, dict)
            and table
            and all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in table.items()),
            f"inventory.{field} must be a non-empty string map",
        )
        tables[field] = table

    rows = inventory.get("repositories")
    require(isinstance(rows, list) and rows, "inventory.repositories must be non-empty")
    require(
        [row.get("repository") for row in rows]
        == sorted((row.get("repository") for row in rows), key=lambda value: str(value).casefold()),
        "inventory repositories must be sorted",
    )
    seen_repositories: set[str] = set()
    counts = {"disposition": Counter(), "trust": Counter(), "migration": Counter()}
    workflow_total = 0
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"inventory.repositories[{index}] must be an object")
        repository = nonempty(row.get("repository"), f"inventory[{index}].repository")
        require(REPO.fullmatch(repository) is not None, f"invalid inventory repository: {repository}")
        require(repository not in seen_repositories, f"duplicate inventory repository: {repository}")
        seen_repositories.add(repository)
        nonempty(row.get("branch"), f"{repository}.branch")
        require(SHA.fullmatch(nonempty(row.get("commit"), f"{repository}.commit")) is not None, f"{repository}: invalid commit")
        nonempty(row.get("basis"), f"{repository}.basis")
        workflows = row.get("workflows")
        require(isinstance(workflows, list), f"{repository}.workflows must be an array")
        previous = ""
        for item_index, item in enumerate(workflows):
            label = f"{repository}.workflows[{item_index}]"
            require(isinstance(item, list) and len(item) == len(COLUMNS), f"{label}: invalid row shape")
            path, name, status, disposition, migration, trust, blob = item
            for value, field in zip(item[:6], COLUMNS[:6]):
                nonempty(value, f"{label}.{field}")
            require(path.startswith(WORKFLOW_PREFIX), f"{label}: path is outside workflows")
            require(path.endswith((".yml", ".yaml")), f"{label}: path is not workflow YAML")
            require(path > previous, f"{repository}: workflow rows must be uniquely sorted")
            previous = path
            require(disposition in tables["dispositions"], f"{label}: unknown disposition {disposition}")
            require(migration in tables["migration_classes"], f"{label}: unknown migration {migration}")
            require(trust in tables["trust_classes"], f"{label}: unknown trust {trust}")
            require(blob is None or (isinstance(blob, str) and SHA.fullmatch(blob)), f"{label}: invalid blob")
            require("unclassified" not in json.dumps(item).lower(), f"{label}: unresolved classification")
            counts["disposition"][disposition] += 1
            counts["migration"][migration] += 1
            counts["trust"][trust] += 1
            workflow_total += 1

    require(workflow_total >= MIN_WORKFLOWS, f"inventory shrank below {MIN_WORKFLOWS}: {workflow_total}")
    require(counts["disposition"]["retire"] > 0, "missing disposition: retire")
    for code in ("publish", "device", "flux"):
        require(counts["trust"][code] > 0, f"missing trust class: {code}")
    for code in ("flux-assets", "flux-reconcile"):
        require(counts["migration"][code] > 0, f"missing migration class: {code}")

    ownership = location["ownership"].read_text(encoding="utf-8")
    for phrase in (
        "central public reusable workflow",
        "temporary, repair, recovery, diagnostic, or superseded workflow to retire",
        "Agent State",
        "Flux",
        "organization-rules",
        "publication and live selection are separate evidence states",
    ):
        require(phrase in ownership, f"ownership document is missing: {phrase}")

    data = {"paths": location, "inventory": inventory, "workflow_total": workflow_total, "counts": counts}
    if location["report"].exists():
        require(location["report"].read_text(encoding="utf-8") == render(data), "workflow report is stale")
    return data


def cell(value: Any) -> str:
    return ("—" if value is None else str(value)).replace("|", "\\|").replace("\n", " ")


def render(data: Mapping[str, Any]) -> str:
    inventory = data["inventory"]
    repositories = inventory["repositories"]
    lines = [
        "# Organization workflow inventory",
        "",
        f"Capture date: `{inventory['captured_at']}`",
        "",
        (
            f"This navigation snapshot classifies **{data['workflow_total']} workflow files across "
            f"{len(repositories)} repositories**. It is not a consumer/product compatibility allowlist. "
            "Capture commits are evidence anchors; the live drift check compares workflow paths and available "
            "Git blob identities without checking out or executing consumer source."
        ),
        "",
        "## Summary",
        "",
        "| Repository | Captured source | Workflows | Active | Retire | Publication | Device | Flux-authorized |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for repository in repositories:
        workflows = repository["workflows"]
        values = (
            repository["repository"],
            f"`{repository['branch']}@{repository['commit']}`",
            len(workflows),
            sum(item[3] != "retire" for item in workflows),
            sum(item[3] == "retire" for item in workflows),
            sum(item[5] == "publish" for item in workflows),
            sum(item[5] == "device" for item in workflows),
            sum(item[5] == "flux" for item in workflows),
        )
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")

    lines += ["", "## Classification totals", "", "| Dimension | Code | Meaning | Count |", "|---|---|---|---:|"]
    for dimension, table, count_key in (
        ("Disposition", inventory["dispositions"], "disposition"),
        ("Trust", inventory["trust_classes"], "trust"),
        ("Migration", inventory["migration_classes"], "migration"),
    ):
        for code, meaning in table.items():
            lines.append(f"| {dimension} | `{cell(code)}` | {cell(meaning)} | {data['counts'][count_key].get(code, 0)} |")

    lines += ["", "## Repository workflow ledger", ""]
    for repository in repositories:
        lines += [
            f"### {repository['repository']}",
            "",
            f"- Capture: `{repository['branch']}@{repository['commit']}`",
            f"- Evidence basis: {repository['basis']}",
            "",
            "| Workflow | Name | Status | Disposition | Migration | Trust | Blob |",
            "|---|---|---|---|---|---|---|",
        ]
        for path, name, status, disposition, migration, trust, blob in repository["workflows"]:
            values = (
                f"`{cell(path)}`",
                cell(name),
                f"`{cell(status)}`",
                f"`{cell(disposition)}`",
                f"`{cell(migration)}`",
                f"`{cell(trust)}`",
                f"`{cell(blob)}`" if blob else "—",
            )
            lines.append("| " + " | ".join(values) + " |")
        lines.append("")

    lines += [
        "## Drift and update contract",
        "",
        "- `python3 scripts/ci/inventory_contract.py validate` validates this generic workflow-navigation snapshot and generated-report agreement.",
        "- `python3 scripts/ci/inventory_contract.py render` regenerates this report deterministically.",
        "- `python3 scripts/ci/inventory_live_check.py` compares the configured organization workflow trees using a read-only contents token.",
        "- Live comparison never checks out or executes consumer source and needs no product, Agent State mutation, registry, signing, SOPS, Kubernetes, or device credential.",
        "- This snapshot does not decide whether a repository or product may call a reusable workflow; ordinary compatibility is capability/trust/input based.",
        "",
        "Ownership decisions remain documented in `docs/architecture/ownership-boundaries.md`; navigation-only consumer/product metadata does not participate in public API validation.",
        "",
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path.cwd())
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    render_command = commands.add_parser("render")
    render_command.add_argument("--check", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        data = validate(args.root)
        if args.command == "validate":
            print(f"validated {data['workflow_total']} workflow inventory records")
        else:
            output = render(data)
            target = data["paths"]["report"]
            if args.check:
                require(target.exists() and target.read_text(encoding="utf-8") == output, "workflow report is stale")
                print("workflow inventory report is current")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(output, encoding="utf-8")
                print(f"rendered {target}")
    except ContractError as error:
        print(f"inventory contract error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
