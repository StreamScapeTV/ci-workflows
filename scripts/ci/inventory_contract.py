#!/usr/bin/env python3
"""Validate and render the checked-in organization workflow inventory."""
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
OCI_PRODUCERS = {
    "StreamScapeTV/agent-state",
    "StreamScapeTV/flux",
    "StreamScapeTV/iptv-backend",
}
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
        "consumers": root / "contracts/consumers.json",
        "inventory": root / "contracts/workflow-inventory.json",
        "products": root / "contracts/products.json",
        "ownership": root / "docs/architecture/ownership-boundaries.md",
        "report": root / "docs/inventory/workflows.md",
    }


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    location = paths(root)
    consumers = load_json(location["consumers"])
    inventory = load_json(location["inventory"])
    products = load_json(location["products"])

    require(consumers.get("schema_version") == 1, "unsupported consumers schema")
    require(consumers.get("organization") == "StreamScapeTV", "consumer organization mismatch")
    consumer_rows = consumers.get("repositories")
    require(isinstance(consumer_rows, list) and consumer_rows, "consumers.repositories must be non-empty")
    consumer_by_repo: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(consumer_rows):
        require(isinstance(row, dict), f"consumers.repositories[{index}] must be an object")
        repository = nonempty(row.get("repository"), f"consumer[{index}].repository")
        require(REPO.fullmatch(repository) is not None, f"invalid repository: {repository}")
        require(repository not in consumer_by_repo, f"duplicate consumer: {repository}")
        for field in (
            "integration_branch",
            "agent_state_project",
            "agent_state_mapping_status",
            "required_checks_status",
            "migration_status",
        ):
            nonempty(row.get(field), f"{repository}.{field}")
        for field in ("technologies", "products", "runner_capabilities", "required_checks"):
            value = row.get(field)
            require(
                isinstance(value, list) and all(isinstance(item, str) and item for item in value),
                f"{repository}.{field} must be a string array",
            )
        consumer_by_repo[repository] = row

    rules = consumer_by_repo.get("StreamScapeTV/organization-rules", {})
    require(
        rules.get("agent_state_project") == "not-established"
        and rules.get("agent_state_mapping_status") == "not-established-do-not-use-as-project-key",
        "organization-rules must not invent an Agent State project key",
    )

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
    inventory_by_repo: dict[str, dict[str, Any]] = {}
    counts = {
        "disposition": Counter(),
        "trust": Counter(),
        "migration": Counter(),
    }
    workflow_total = 0
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"inventory.repositories[{index}] must be an object")
        repository = nonempty(row.get("repository"), f"inventory[{index}].repository")
        require(REPO.fullmatch(repository) is not None, f"invalid inventory repository: {repository}")
        require(repository in consumer_by_repo, f"unknown inventory repository: {repository}")
        require(repository not in inventory_by_repo, f"duplicate inventory repository: {repository}")
        branch = nonempty(row.get("branch"), f"{repository}.branch")
        require(
            branch == consumer_by_repo[repository]["integration_branch"],
            f"{repository}: branch disagrees with consumers.json",
        )
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
        inventory_by_repo[repository] = row

    require(set(inventory_by_repo) == set(consumer_by_repo), "consumer and inventory repository sets differ")
    require(workflow_total >= MIN_WORKFLOWS, f"inventory shrank below {MIN_WORKFLOWS}: {workflow_total}")
    for code in ("retire",):
        require(counts["disposition"][code] > 0, f"missing disposition: {code}")
    for code in ("publish", "device", "flux"):
        require(counts["trust"][code] > 0, f"missing trust class: {code}")
    for code in ("flux-assets", "flux-reconcile"):
        require(counts["migration"][code] > 0, f"missing migration class: {code}")

    require(products.get("schema_version") == 1, "unsupported products schema")
    require(products.get("organization") == "StreamScapeTV", "products organization mismatch")
    principles = products.get("release_principles", {})
    require(principles.get("exact_source_required") is True, "product releases must require exact source")
    require(principles.get("latest_forbidden") is True, "latest must remain forbidden")
    require(principles.get("publication_separate_from_deployment") is True, "publication/deployment boundary missing")
    require(principles.get("routine_actions_artifacts") == "forbidden", "routine artifacts must be forbidden")
    require(
        principles.get("ci_workflows_repository_release") == "lightweight-git-tag-only",
        "ci-workflows release must be a lightweight tag only",
    )
    product_rows = products.get("products")
    require(isinstance(product_rows, list) and product_rows, "products.products must be non-empty")
    ids: set[str] = set()
    oci: set[str] = set()
    charts: set[str] = set()
    for index, product in enumerate(product_rows):
        require(isinstance(product, dict), f"products[{index}] must be an object")
        product_id = nonempty(product.get("id"), f"products[{index}].id")
        require(product_id not in ids, f"duplicate product id: {product_id}")
        ids.add(product_id)
        repository = nonempty(product.get("repository"), f"{product_id}.repository")
        require(repository in consumer_by_repo, f"{product_id}: unknown repository")
        kind = nonempty(product.get("kind"), f"{product_id}.kind")
        for field in ("status", "release_mode", "owner", "central_workflow_target"):
            nonempty(product.get(field), f"{product_id}.{field}")
        if kind in {"oci-image", "oci-runner-image-family"}:
            oci.add(repository)
        if kind in {"helm-oci-chart", "helm-oci-chart-assets"}:
            charts.add(repository)
    require(oci == OCI_PRODUCERS, f"OCI producer set changed: {sorted(oci)}")
    require(charts == OCI_PRODUCERS, f"Helm producer set changed: {sorted(charts)}")

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

    data = {
        "paths": location,
        "inventory": inventory,
        "products": products,
        "workflow_total": workflow_total,
        "counts": counts,
    }
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
            f"This contract classifies **{data['workflow_total']} live workflow files across "
            f"{len(repositories)} repositories**. Capture commits are evidence anchors; "
            "the live drift check compares workflow paths and available Git blob identities "
            "without checking out or executing consumer source."
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
        "- `python3 scripts/ci/inventory_contract.py validate` validates repository, workflow, product, authority, and generated-report agreement.",
        "- `python3 scripts/ci/inventory_contract.py render` regenerates this report deterministically.",
        "- `python3 scripts/ci/inventory_live_check.py` compares current organization workflow trees using a read-only contents token.",
        "- Live comparison never checks out or executes consumer source and needs no product, Agent State mutation, registry, signing, SOPS, Kubernetes, or device credential.",
        "- A workflow add, removal, rename, or changed recorded blob requires an inventory update in the same reviewed change.",
        "",
        "Ownership decisions are documented in `docs/architecture/ownership-boundaries.md`; product and explicit non-product decisions are recorded in `contracts/products.json`.",
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
            print(
                f"validated {len(data['inventory']['repositories'])} repositories, "
                f"{data['workflow_total']} workflows, and {len(data['products']['products'])} products"
            )
        else:
            output = render(data)
            report = data["paths"]["report"]
            if args.check:
                require(report.exists() and report.read_text(encoding="utf-8") == output, "workflow report is stale")
                print("workflow report is current")
            else:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(output, encoding="utf-8")
                print(f"rendered {report}")
    except ContractError as exc:
        print(f"inventory contract error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
