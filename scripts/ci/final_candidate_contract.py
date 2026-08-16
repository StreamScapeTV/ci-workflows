from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "final-candidate-lifecycle.json"
REPORT = ROOT / "docs" / "inventory" / "final-candidate-lifecycle.md"
INVENTORY = ROOT / "contracts" / "workflow-inventory.json"
WORKFLOW_ROOT = ROOT / ".github" / "workflows"

EXPECTED_PREFIX = "[skip push ci] "
EXPECTED_NATIVE_SKIP_MARKERS = {
    "[skip ci]",
    "[ci skip]",
    "[no ci]",
    "[skip actions]",
    "[actions skip]",
    "skip-checks:",
}
EXPECTED_CLASSES = {
    "final-pull-request-validation",
    "protected-integration-release-validation",
    "authorized-publication-deployment-device-live-evidence",
    "maintenance-control",
    "noncompliant-unprotected-feature-branch-product-validation",
    "retired-stale",
}


def load_contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def should_run_product_validation(*, event_name: str, protected: bool, subject: str) -> bool:
    if event_name == "pull_request":
        return True
    if event_name == "push":
        return protected
    return True


def has_native_skip_marker(subject: str) -> bool:
    lowered = subject.lower()
    return any(marker in lowered for marker in EXPECTED_NATIVE_SKIP_MARKERS)


def render(contract: dict) -> str:
    workflow_count = sum(len(repo["workflows"]) for repo in contract["repositories"])
    lines = [
        "# Final Candidate Lifecycle Inventory",
        "",
        f"- Audited: `{contract['audited_on']}`",
        f"- Repositories: `{len(contract['repositories'])}`",
        f"- Current workflow files: `{workflow_count}`",
        f"- Intermediate checkpoint prefix: `{contract['checkpoint_prefix']}`",
        "",
        "This report classifies the current live workflow-file set. A workflow can have multiple trigger classes when one file serves more than one authorized event family.",
        "",
        "## Prefix contract",
        "",
        "| Event family | Required behavior |",
        "|---|---|",
    ]
    report_order = (
        "ordinary_unprotected_feature_push",
        "pull_request",
        "protected_integration_release",
        "manual_publication_deployment_device_live",
    )
    for event_family in report_order:
        lines.append(
            f"| `{event_family}` | `{contract['prefix_contract'][event_family]}` |"
        )

    for repo in contract["repositories"]:
        lines.extend(
            [
                "",
                f"## {repo['repository']}",
                "",
                f"- Protected integration branch: `{repo['integration_branch']}`",
                f"- Workflow files: `{len(repo['workflows'])}`",
            ]
        )
        if repo.get("remediation_issue"):
            lines.append(f"- Repository remediation: `{repo['remediation_issue']}`")
        lines.extend(
            [
                "",
                "| Workflow path | Trigger class(es) | Finding | Remediation owner |",
                "|---|---|---|---|",
            ]
        )
        if not repo["workflows"]:
            lines.append("| — | — | none | — |")
        for workflow in repo["workflows"]:
            classes = "<br>".join(f"`{value}`" for value in workflow["trigger_classes"])
            finding = workflow.get("finding", "—")
            owner = workflow.get("remediation_issue", "—")
            lines.append(
                f"| `{workflow['path']}` | {classes} | {finding} | {owner} |"
            )

    lines.extend(
        [
            "",
            "## Native skip policy",
            "",
            "GitHub-native workflow-skip markers are forbidden for organization checkpoints because they can suppress the pull-request workflow itself. The exact machine-validated marker catalog lives in `contracts/final-candidate-lifecycle.json` and its fixtures.",
            "",
            "## Enforcement",
            "",
            "- Central self-check validates this contract, the exact repository/workflow count, allowed trigger classes, remediation ownership for noncompliance, the checkpoint prefix, and native-skip fixtures.",
            "- Consumer fixes remain separate repository issues/branches/PRs; this central contract records findings but does not mutate consumer repositories.",
            "",
        ]
    )
    return "\n".join(lines)


def validate(contract: dict) -> list[str]:
    errors: list[str] = []
    if contract.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if contract.get("organization") != "StreamScapeTV":
        errors.append("organization must be StreamScapeTV")
    if contract.get("checkpoint_prefix") != EXPECTED_PREFIX:
        errors.append("checkpoint prefix drift")
    if set(contract.get("native_skip_markers", [])) != EXPECTED_NATIVE_SKIP_MARKERS:
        errors.append("native skip marker fixture drift")
    if set(contract.get("allowed_trigger_classes", [])) != EXPECTED_CLASSES:
        errors.append("trigger class catalog drift")

    repositories = contract.get("repositories", [])
    names = [repo.get("repository") for repo in repositories]
    if len(repositories) != 13 or len(set(names)) != 13:
        errors.append("contract must contain exactly 13 unique repositories")

    total = 0
    for repo in repositories:
        name = repo.get("repository")
        branch = repo.get("integration_branch")
        workflows = repo.get("workflows")
        if not isinstance(name, str) or not name.startswith("StreamScapeTV/"):
            errors.append(f"invalid repository name: {name!r}")
            continue
        if not isinstance(branch, str) or not branch:
            errors.append(f"{name}: missing integration branch")
        if not isinstance(workflows, list):
            errors.append(f"{name}: workflows must be a list")
            continue
        seen: set[str] = set()
        for workflow in workflows:
            total += 1
            path = workflow.get("path")
            classes = workflow.get("trigger_classes")
            if not isinstance(path, str) or not path.startswith(".github/workflows/"):
                errors.append(f"{name}: invalid workflow path {path!r}")
                continue
            if path in seen:
                errors.append(f"{name}: duplicate workflow path {path}")
            seen.add(path)
            if not isinstance(classes, list) or not classes:
                errors.append(f"{name}:{path}: missing trigger classes")
                continue
            unknown = set(classes) - EXPECTED_CLASSES
            if unknown:
                errors.append(f"{name}:{path}: unknown trigger classes {sorted(unknown)}")
            if (
                "noncompliant-unprotected-feature-branch-product-validation" in classes
                and not workflow.get("remediation_issue")
            ):
                errors.append(f"{name}:{path}: noncompliance missing remediation owner")
            if workflow.get("finding") and not workflow.get("remediation_issue"):
                errors.append(f"{name}:{path}: finding missing remediation owner")

    if total != 98:
        errors.append(f"expected 98 current workflow files, found {total}")

    central = next(
        (repo for repo in repositories if repo.get("repository") == "StreamScapeTV/ci-workflows"),
        None,
    )
    if central is None:
        errors.append("missing ci-workflows repository")
    else:
        expected_paths = {workflow["path"] for workflow in central["workflows"]}
        actual_paths = {
            str(path.relative_to(ROOT)).replace("\\", "/")
            for path in WORKFLOW_ROOT.iterdir()
            if path.is_file() and path.suffix in {".yml", ".yaml"}
        }
        if expected_paths != actual_paths:
            errors.append(
                "ci-workflows workflow path drift: "
                f"missing={sorted(expected_paths - actual_paths)} "
                f"unexpected={sorted(actual_paths - expected_paths)}"
            )

    if INVENTORY.exists():
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        inventory_repositories = {
            item["repository"] for item in inventory.get("repositories", [])
        }
        missing = inventory_repositories - set(names)
        if missing:
            errors.append(f"legacy conformance inventory repositories missing: {sorted(missing)}")

    expected_report = render(contract)
    if not REPORT.exists() or REPORT.read_text(encoding="utf-8") != expected_report:
        errors.append("generated lifecycle report is stale")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "render"))
    args = parser.parse_args()
    contract = load_contract()
    if args.command == "render":
        REPORT.write_text(render(contract), encoding="utf-8")
        return 0
    errors = validate(contract)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("final-candidate lifecycle contract: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
