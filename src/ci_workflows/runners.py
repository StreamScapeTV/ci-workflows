"""Validate, resolve, and generate the central runner capability contract."""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

CONTRACT_PATH = Path("contracts/runner-profiles.json")
INVENTORY_PATH = Path("contracts/workflow-inventory.json")
MAPPINGS_PATH = Path("generated/runner-mappings.json")
COMPATIBILITY_DOC_PATH = Path("docs/inventory/runner-compatibility.md")
PROFILE_IDS = {
    "portable", "mobile", "buildah-tiny", "buildah-small", "buildah-medium",
    "buildah-high", "apple", "physical-device", "flux-control",
}
FINAL_LINUX_ARC_SELECTORS = {
    "portable": (("linux", "amd64", "general"),),
    "mobile": (
        ("linux", "amd64", "mobile"),
        ("linux", "amd64", "android"),
        ("linux", "amd64", "flutter"),
        ("linux", "amd64", "jdk-25"),
        ("linux", "amd64", "node-24"),
        ("linux", "amd64", "nodejs"),
    ),
    "buildah-tiny": (("linux", "amd64", "buildah", "tiny"),),
    "buildah-small": (("linux", "amd64", "buildah", "small"),),
    "buildah-medium": (("linux", "amd64", "buildah", "medium"),),
    "buildah-high": (("linux", "amd64", "buildah", "high"),),
    "flux-control": (("linux", "amd64", "flux-control"),),
}
APPLE_CAPABILITY_SELECTORS = (("macOS", "ARM64"),)
RETIRED_LINUX_SELECTOR_TOKENS = {
    "portable",
    "buildah-tiny",
    "buildah-small",
    "buildah-medium",
    "buildah-high",
}
INTERNAL_ARC_NAME = re.compile(
    r"(?:^|[-_])arc(?:[-_]|$)|(?:^|[-_])actions[-_]runner[-_]controller"
    r"(?:[-_]|$)|(?:^|[-_])gha[-_]runner[-_]scale[-_]set(?:[-_]|$)",
    re.IGNORECASE,
)
PROFILE_FIELDS = {
    "id", "public_name", "kind", "public_labels", "internal_selectors",
    "default_internal_selector", "os", "architecture", "lifecycle", "capacity_owner",
    "privilege", "trust", "tools", "resources", "concurrency_cap",
    "allowed_workflow_apis", "forbidden_uses", "evidence_fields",
}
PRIVILEGE_FIELDS = {
    "privileged_container", "kubernetes_token", "repository_scoped",
    "manual_capacity", "device_locked",
}
RESOURCE_FIELDS = {
    "memory_request", "memory_limit", "local_storage_limit", "workspace", "scratch",
    "managed_cache",
}
INVENTORY_COLUMNS = ["path", "name", "status", "disposition", "migration", "trust", "blob"]


class RunnerContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Resolution:
    profile: str
    execution_profile: str
    runs_on: tuple[str, ...]
    workflow_api: str
    source_trust: str
    resource_lock_required: bool
    evidence_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        labels = list(self.runs_on)
        return {
            "profile": self.profile,
            "execution_profile": self.execution_profile,
            "runs_on": labels,
            "runs_on_json": json.dumps(labels, separators=(",", ":")),
            "workflow_api": self.workflow_api,
            "source_trust": self.source_trust,
            "resource_lock_required": self.resource_lock_required,
            "evidence_fields": list(self.evidence_fields),
        }


def require(value: bool, code: str, message: str) -> None:
    if not value:
        raise RunnerContractError(code, message)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RunnerContractError("invalid-json", f"{path}: {exc}") from exc


def strings(value: Any, code: str, *, empty: bool = True) -> list[str]:
    require(isinstance(value, list), code, "expected list")
    require(all(isinstance(item, str) and item for item in value), code, "invalid string list")
    require(empty or bool(value), code, "list must not be empty")
    return list(value)


def selector(value: Any) -> tuple[str, ...]:
    labels = strings(value, "invalid-selector", empty=False)
    require(len(labels) == len(set(labels)), "invalid-selector", "duplicate labels")
    return tuple(labels)


def profile_index(contract: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {profile["id"]: profile for profile in contract["profiles"]}


def workflow_binding_index(contract: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {binding["api"]: binding for binding in contract["workflow_bindings"]}


def profile_alias_index(contract: Mapping[str, Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for profile in contract["profiles"]:
        for name in [profile["id"], profile["public_name"], *profile["public_labels"]]:
            require(name not in aliases or aliases[name] == profile["id"], "duplicate-profile-alias", name)
            aliases[name] = profile["id"]
    aliases["buildah"] = contract["direct_selection_policy"]["generic_buildah_maps_only_to"]
    return aliases


def approved_selector_index(contract: Mapping[str, Any]) -> dict[tuple[str, ...], str]:
    result: dict[tuple[str, ...], str] = {}
    for profile in contract["profiles"]:
        for raw in profile["internal_selectors"]:
            key = selector(raw)
            require(key not in result or result[key] == profile["id"], "duplicate-selector", str(list(key)))
            result[key] = profile["id"]
    return result


def validate_runner_contract(contract: Mapping[str, Any]) -> None:
    require(contract.get("schema_version") == 1, "invalid-contract", "schema_version")
    require(contract.get("contract_version") == "2.1.0", "invalid-contract", "contract_version")
    require(contract.get("organization") == "StreamScapeTV", "invalid-contract", "organization")
    mechanism = contract.get("scheduling_mechanism")
    require(isinstance(mechanism, dict), "invalid-contract", "scheduling_mechanism")
    require(mechanism.get("kind") == "trusted-planning-job", "invalid-contract", "planner kind")
    require(mechanism.get("planner_profile") == "portable", "invalid-contract", "planner profile")
    require(mechanism.get("composite_action_can_change_runs_on") is False, "invalid-contract", "composite action")
    require(mechanism.get("caller_supplied_selectors") is False, "invalid-contract", "caller selectors")
    require(mechanism.get("generated_mapping") == MAPPINGS_PATH.as_posix(), "invalid-contract", "mapping path")
    policy = contract.get("direct_selection_policy")
    require(isinstance(policy, dict), "invalid-contract", "direct selection policy")
    require(policy.get("bare_self_hosted_forbidden") is True, "invalid-contract", "self-hosted policy")
    require(policy.get("docker_and_dind_retired") is True, "invalid-contract", "Docker policy")
    require(policy.get("generic_buildah_maps_only_to") == "buildah-small", "invalid-contract", "Buildah alias")
    trust_values = set(strings(contract.get("source_trust_values"), "invalid-contract", empty=False))

    profiles = contract.get("profiles")
    require(isinstance(profiles, list), "invalid-contract", "profiles")
    require({profile.get("id") for profile in profiles if isinstance(profile, dict)} == PROFILE_IDS,
            "invalid-contract", "profile set")
    for profile in profiles:
        require(isinstance(profile, dict), "invalid-profile", "not an object")
        profile_id = profile["id"]
        require(PROFILE_FIELDS <= set(profile), "invalid-profile", f"{profile_id}: fields")
        require(profile["public_name"] == profile_id, "invalid-profile", f"{profile_id}: public name")
        require(profile["kind"] in {"runner", "guarded-overlay"}, "invalid-profile", f"{profile_id}: kind")
        labels = strings(profile["public_labels"], "invalid-profile")
        require(not ({label.lower() for label in labels} & {"docker", "dind", "self-hosted"}),
                "unsafe-public-label", profile_id)
        raw_selectors = profile["internal_selectors"]
        require(isinstance(raw_selectors, list), "invalid-profile", f"{profile_id}: selectors")
        approved = [selector(item) for item in raw_selectors]
        if profile_id in FINAL_LINUX_ARC_SELECTORS:
            expected_selectors = FINAL_LINUX_ARC_SELECTORS[profile_id]
            require(
                tuple(approved) == expected_selectors,
                "invalid-profile",
                f"{profile_id}: final Linux ARC selectors",
            )
            flattened = {
                label
                for approved_selector in approved
                for label in approved_selector
            }
            require(
                not (flattened & RETIRED_LINUX_SELECTOR_TOKENS),
                "retired-selector",
                profile_id,
            )
            require(
                not any(label.startswith("homelab-") for label in flattened),
                "retired-selector",
                profile_id,
            )
        if profile_id == "apple":
            require(
                tuple(approved) == APPLE_CAPABILITY_SELECTORS,
                "invalid-profile",
                "apple: current macOS ARM64 capability selector",
            )
        if profile["kind"] == "runner":
            require(bool(approved), "invalid-profile", f"{profile_id}: no selector")
            require(selector(profile["default_internal_selector"]) in approved,
                    "invalid-profile", f"{profile_id}: default selector")
        else:
            require(approved == [] and profile["default_internal_selector"] is None,
                    "invalid-profile", f"{profile_id}: overlay selector")
        privilege = profile["privilege"]
        require(isinstance(privilege, dict) and set(privilege) == PRIVILEGE_FIELDS,
                "invalid-profile", f"{profile_id}: privilege")
        require(all(isinstance(item, bool) for item in privilege.values()),
                "invalid-profile", f"{profile_id}: privilege values")
        trust = profile["trust"]
        require(isinstance(trust, dict), "invalid-profile", f"{profile_id}: trust")
        allowed_trust = set(strings(trust.get("allowed_source_trust"), "invalid-profile", empty=False))
        require(allowed_trust <= trust_values, "invalid-profile", f"{profile_id}: trust values")
        require(isinstance(trust.get("executes_caller_source"), bool),
                "invalid-profile", f"{profile_id}: caller source")
        tools = profile["tools"]
        require(isinstance(tools, list) and tools, "invalid-profile", f"{profile_id}: tools")
        for tool in tools:
            require(isinstance(tool, dict) and set(tool) == {"name", "version"},
                    "invalid-profile", f"{profile_id}: tool")
            require(not any(word in tool["name"].lower() for word in ("docker", "dind")),
                    "retired-docker", profile_id)
        require(isinstance(profile["resources"], dict) and set(profile["resources"]) == RESOURCE_FIELDS,
                "invalid-profile", f"{profile_id}: resources")
        cap = profile["concurrency_cap"]
        require(cap is None or isinstance(cap, int) and cap > 0,
                "invalid-profile", f"{profile_id}: concurrency")
        for key in ("allowed_workflow_apis", "forbidden_uses", "evidence_fields"):
            strings(profile[key], "invalid-profile", empty=False)

    approved_selector_index(contract)
    aliases = profile_alias_index(contract)
    require(aliases["buildah"] == "buildah-small", "invalid-contract", "generic Buildah")
    profile_map = profile_index(contract)
    physical = profile_map["physical-device"]
    require(physical.get("base_profile_by_family") == {"android": "mobile", "ios": "apple", "tvos": "apple"},
            "invalid-contract", "device family mapping")
    lock = physical.get("lock_contract")
    expected_lock = {"authorization_receipt", "resource_lock_receipt", "device_family",
                     "discovered_device_id", "tested_source_sha", "cleanup_evidence"}
    require(isinstance(lock, dict) and lock.get("required") is True and
            set(strings(lock.get("required_fields"), "invalid-contract", empty=False)) == expected_lock,
            "invalid-contract", "device lock contract")

    seen_apis: set[str] = set()
    for binding in contract.get("workflow_bindings", []):
        require(isinstance(binding, dict) and set(binding) == {"api", "strategy", "profiles"},
                "invalid-binding", "fields")
        api = binding["api"]
        require(api not in seen_apis, "duplicate-binding", api)
        seen_apis.add(api)
        for profile_id in strings(binding["profiles"], "invalid-binding", empty=False):
            require(profile_id in profile_map, "invalid-binding", profile_id)
            require(api in profile_map[profile_id]["allowed_workflow_apis"],
                    "invalid-binding", f"{api}:{profile_id}")
    seen_migrations: set[str] = set()
    for rule in contract.get("compatibility_rules", []):
        migration = rule.get("migration") if isinstance(rule, dict) else None
        require(isinstance(migration, str) and migration not in seen_migrations,
                "invalid-compatibility-rule", str(migration))
        seen_migrations.add(migration)
        mapped = strings(rule.get("profiles"), "invalid-compatibility-rule")
        require(set(mapped) <= PROFILE_IDS, "invalid-compatibility-rule", migration)
        require(bool(mapped) or isinstance(rule.get("exception"), str),
                "invalid-compatibility-rule", migration)
    escalation = contract.get("buildah_escalation")
    require(isinstance(escalation, dict) and escalation.get("order") ==
            ["buildah-tiny", "buildah-small", "buildah-medium", "buildah-high"],
            "invalid-contract", "Buildah order")
    strings(escalation.get("required_evidence"), "invalid-contract", empty=False)
    forbidden = set(strings(contract.get("caller_forbidden_fields"), "invalid-contract", empty=False))
    require({"runner", "runs-on", "runs_on", "runner_labels", "labels"} <= forbidden,
            "invalid-contract", "caller selector fields")


def load_runner_contract(root: Path) -> dict[str, Any]:
    data = read_json(root / CONTRACT_PATH)
    require(isinstance(data, dict), "invalid-contract", "root object")
    validate_runner_contract(data)
    return data


def validate_caller_inputs(contract: Mapping[str, Any], inputs: Mapping[str, Any] | None) -> None:
    if inputs:
        supplied = sorted(set(inputs) & set(contract["caller_forbidden_fields"]))
        require(not supplied, "caller-supplied-selector", ", ".join(supplied))


def validate_source(profile: Mapping[str, Any], source_trust: str) -> None:
    require(source_trust in profile["trust"]["allowed_source_trust"],
            "source-trust-not-allowed", f"{profile['id']}:{source_trust}")


def validate_device_lock(profile: Mapping[str, Any], family: str | None,
                         evidence: Mapping[str, Any] | None) -> str:
    bases = profile["base_profile_by_family"]
    require(family in bases, "invalid-device-family", str(family))
    evidence = evidence or {}
    missing = [name for name in profile["lock_contract"]["required_fields"]
               if not isinstance(evidence.get(name), str) or not evidence[name].strip()]
    require(not missing, "device-lock-required", ", ".join(missing))
    require(evidence["device_family"] == family, "device-lock-mismatch", "device_family")
    require(re.fullmatch(r"[0-9a-f]{40}", evidence["tested_source_sha"]) is not None,
            "device-lock-mismatch", "tested_source_sha")
    return bases[family]


def resolve_runner_profile(contract: Mapping[str, Any], *, workflow_api: str,
                           source_trust: str, requested_profile: str | None = None,
                           caller_inputs: Mapping[str, Any] | None = None,
                           device_family: str | None = None,
                           lock_evidence: Mapping[str, Any] | None = None) -> Resolution:
    validate_caller_inputs(contract, caller_inputs)
    bindings = workflow_binding_index(contract)
    require(workflow_api in bindings, "unknown-workflow-api", workflow_api)
    binding = bindings[workflow_api]
    allowed = list(binding["profiles"])
    if requested_profile is None:
        require(len(allowed) == 1, "profile-required", workflow_api)
        profile_id = allowed[0]
    else:
        aliases = profile_alias_index(contract)
        require(requested_profile in aliases, "unknown-profile", requested_profile)
        profile_id = aliases[requested_profile]
    require(profile_id in allowed, "profile-not-allowed", f"{profile_id}:{workflow_api}")
    profiles = profile_index(contract)
    profile = profiles[profile_id]
    validate_source(profile, source_trust)
    if profile["kind"] == "guarded-overlay":
        execution_id = validate_device_lock(profile, device_family, lock_evidence)
        execution = profiles[execution_id]
        validate_source(execution, "trusted-exact")
        return Resolution(profile_id, execution_id, selector(execution["default_internal_selector"]),
                          workflow_api, source_trust, True, tuple(profile["evidence_fields"]))
    return Resolution(profile_id, profile_id, selector(profile["default_internal_selector"]),
                      workflow_api, source_trust, bool(profile["privilege"]["device_locked"]),
                      tuple(profile["evidence_fields"]))


def validate_direct_selector(contract: Mapping[str, Any], labels: Sequence[str] | str) -> str:
    selected = (labels,) if isinstance(labels, str) else tuple(labels)
    require(bool(selected) and all(isinstance(item, str) and item for item in selected),
            "invalid-selector", "labels")
    require(len(selected) == len(set(selected)), "invalid-selector", "duplicates")
    lowered = {item.lower() for item in selected}
    if "self-hosted" in lowered:
        if len(selected) == 1:
            raise RunnerContractError("bare-self-hosted", "bare self-hosted is forbidden")
        raise RunnerContractError(
            "unsupported-self-hosted-combination",
            ", ".join(selected),
        )
    if selected == ("buildah",):
        raise RunnerContractError(
            "ambiguous-buildah",
            "bare buildah must include exactly one size",
        )
    retired = sorted(
        item
        for item in selected
        if item.lower() in RETIRED_LINUX_SELECTOR_TOKENS
        or item.lower().startswith("homelab-")
        or INTERNAL_ARC_NAME.search(item)
    )
    if retired:
        raise RunnerContractError("retired-selector", ", ".join(retired))
    if any("docker" in item or "dind" in item for item in lowered):
        raise RunnerContractError("retired-docker", "Docker/DinD capacity is retired")
    approved = approved_selector_index(contract)
    if selected in approved:
        return approved[selected]
    labels_to_profiles = profile_alias_index(contract)
    for key, profile_id in approved.items():
        if len(key) == 1:
            labels_to_profiles.setdefault(key[0], profile_id)
    unknown = [item for item in selected if item not in labels_to_profiles and item != "self-hosted"]
    require(not unknown, "unknown-selector", ", ".join(unknown))
    mapped = {labels_to_profiles[item] for item in selected}
    require(len(mapped) <= 1, "contradictory-labels", ", ".join(sorted(mapped)))
    raise RunnerContractError("unsupported-selector-combination", ", ".join(selected))


def parse_bytes(value: str) -> int:
    match = re.fullmatch(r"([0-9]+)(Mi|Gi)", value)
    require(match is not None, "invalid-resource-limit", value)
    return int(match.group(1)) * (1024 ** (2 if match.group(2) == "Mi" else 3))


def select_buildah_tier(contract: Mapping[str, Any], *, peak_memory_bytes: int,
                        peak_local_storage_bytes: int, headroom_percent: int = 20) -> str:
    require(peak_memory_bytes > 0 and peak_local_storage_bytes > 0,
            "invalid-measurement", "peaks must be positive")
    require(0 <= headroom_percent <= 100, "invalid-measurement", "headroom")
    factor = 1 + headroom_percent / 100
    memory = math.ceil(peak_memory_bytes * factor)
    storage = math.ceil(peak_local_storage_bytes * factor)
    profiles = profile_index(contract)
    for profile_id in contract["buildah_escalation"]["order"]:
        resources = profiles[profile_id]["resources"]
        if memory <= parse_bytes(resources["memory_limit"]) and storage <= parse_bytes(resources["local_storage_limit"]):
            return profile_id
    raise RunnerContractError("buildah-capacity-exceeded", f"memory={memory} storage={storage}")


def validate_buildah_evidence(contract: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    missing = [name for name in contract["buildah_escalation"]["required_evidence"] if name not in evidence]
    require(not missing, "missing-buildah-evidence", ", ".join(missing))
    require(isinstance(evidence["peak_memory_bytes"], int) and evidence["peak_memory_bytes"] > 0,
            "invalid-buildah-evidence", "peak_memory_bytes")
    require(isinstance(evidence["peak_local_storage_bytes"], int) and evidence["peak_local_storage_bytes"] > 0,
            "invalid-buildah-evidence", "peak_local_storage_bytes")
    require(re.fullmatch(r"[0-9a-f]{40}", str(evidence["source_sha"])) is not None,
            "invalid-buildah-evidence", "source_sha")
    require(all(isinstance(evidence[name], str) and evidence[name]
                for name in ("workflow_api", "product_id")),
            "invalid-buildah-evidence", "workflow_api/product_id")


def generate_runner_mappings(contract: Mapping[str, Any]) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for profile in sorted(contract["profiles"], key=lambda item: item["id"]):
        entry = {
            "kind": profile["kind"], "public_labels": profile["public_labels"],
            "lifecycle": profile["lifecycle"], "capacity_owner": profile["capacity_owner"],
            "privilege": profile["privilege"],
            "allowed_source_trust": profile["trust"]["allowed_source_trust"],
            "allowed_workflow_apis": profile["allowed_workflow_apis"],
            "evidence_fields": profile["evidence_fields"],
        }
        if profile["kind"] == "runner":
            entry.update(runs_on=profile["default_internal_selector"],
                         approved_selectors=profile["internal_selectors"])
        else:
            entry.update(runs_on=None, base_profile_by_family=profile["base_profile_by_family"],
                         lock_contract=profile["lock_contract"])
        profiles[profile["id"]] = entry
    bindings = {item["api"]: {"strategy": item["strategy"], "profiles": item["profiles"]}
                for item in sorted(contract["workflow_bindings"], key=lambda item: item["api"])}
    return {
        "schema_version": 1, "generated_from": CONTRACT_PATH.as_posix(),
        "contract_version": contract["contract_version"],
        "scheduling_mechanism": contract["scheduling_mechanism"],
        "aliases": dict(sorted(profile_alias_index(contract).items())),
        "profiles": profiles, "workflow_bindings": bindings,
    }


def load_workflow_inventory(root: Path) -> Mapping[str, Any]:
    inventory = read_json(root / INVENTORY_PATH)
    require(isinstance(inventory, dict) and inventory.get("workflow_columns") == INVENTORY_COLUMNS,
            "invalid-inventory", "columns")
    require(isinstance(inventory.get("repositories"), list), "invalid-inventory", "repositories")
    return inventory


def generate_compatibility_report(contract: Mapping[str, Any], inventory: Mapping[str, Any]) -> dict[str, Any]:
    rules = {rule["migration"]: rule for rule in contract["compatibility_rules"]}
    entries: list[dict[str, Any]] = []
    for repository in inventory["repositories"]:
        require(isinstance(repository, dict) and isinstance(repository.get("workflows"), list),
                "invalid-inventory", "repository")
        for row in repository["workflows"]:
            require(isinstance(row, list) and len(row) == len(INVENTORY_COLUMNS),
                    "invalid-inventory", repository["repository"])
            item = dict(zip(INVENTORY_COLUMNS, row, strict=True))
            require(item["migration"] in rules, "unmapped-workflow",
                    f"{repository['repository']}:{item['path']} migration={item['migration']}")
            rule = rules[item["migration"]]
            entries.append({
                "repository": repository["repository"], "path": item["path"],
                "name": item["name"], "status": item["status"],
                "migration": item["migration"], "trust": item["trust"],
                "profiles": list(rule["profiles"]), "exception": rule.get("exception"),
            })
    entries.sort(key=lambda item: (item["repository"].lower(), item["path"]))
    return {
        "schema_version": 1,
        "generated_from": [CONTRACT_PATH.as_posix(), INVENTORY_PATH.as_posix()],
        "contract_version": contract["contract_version"],
        "captured_at": inventory.get("captured_at"),
        "workflow_count": len(entries), "repository_count": len(inventory["repositories"]),
        "entries": entries,
    }


def render_compatibility_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Runner compatibility report", "",
        f"Generated from `{CONTRACT_PATH}` and `{INVENTORY_PATH}`.", "",
        f"Every one of the **{report['workflow_count']}** inventoried workflow/job families across "
        f"**{report['repository_count']}** repositories has a semantic profile mapping or an explicit exception.",
        "", "| Repository | Workflow | Migration | Approved profile(s) or exception |",
        "|---|---|---|---|",
    ]
    for item in report["entries"]:
        decision = ", ".join(f"`{name}`" for name in item["profiles"])
        if not decision:
            decision = f"Exception: `{item['exception']}`"
        lines.append(f"| `{item['repository']}` | `{item['path']}` | `{item['migration']}` | {decision} |")
    lines += ["", "## Interpretation", "",
              "This report classifies current inventory; it does not authorize consumer edits or direct infrastructure selectors. Reusable-workflow callers supply bounded intent and the central planner resolves the current internal selector. `retire` entries remain owner cleanup; `other` entries require a linked adoption decision.", ""]
    return "\n".join(lines)


def canonical_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generated_outputs(root: Path) -> dict[Path, str]:
    contract = load_runner_contract(root)
    report = generate_compatibility_report(contract, load_workflow_inventory(root))
    return {
        root / MAPPINGS_PATH: canonical_json(generate_runner_mappings(contract)),
        root / COMPATIBILITY_DOC_PATH: render_compatibility_markdown(report),
    }


def write_generated_outputs(root: Path, *, check: bool) -> None:
    for path, expected in generated_outputs(root).items():
        if check:
            actual = path.read_text(encoding="utf-8") if path.exists() else None
            require(actual == expected, "generated-drift", path.relative_to(root).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")


def optional_object(value: str | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    data = json.loads(value)
    require(isinstance(data, dict), "invalid-cli-json", "object required")
    return data


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path.cwd())
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    generate = sub.add_parser("generate")
    generate.add_argument("--check", action="store_true")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--api", required=True)
    resolve.add_argument("--source-trust", required=True)
    resolve.add_argument("--profile")
    resolve.add_argument("--device-family")
    resolve.add_argument("--caller-inputs-json")
    resolve.add_argument("--lock-evidence-json")
    direct = sub.add_parser("validate-selector")
    direct.add_argument("labels", nargs="+")
    tier = sub.add_parser("select-buildah-tier")
    tier.add_argument("--peak-memory-bytes", type=int, required=True)
    tier.add_argument("--peak-local-storage-bytes", type=int, required=True)
    tier.add_argument("--headroom-percent", type=int, default=20)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.root.resolve()
    contract = load_runner_contract(root)
    if args.command == "validate":
        report = generate_compatibility_report(contract, load_workflow_inventory(root))
        print(f"validated {len(contract['profiles'])} runner profiles and {report['workflow_count']} inventory mappings")
    elif args.command == "generate":
        write_generated_outputs(root, check=args.check)
        print("runner generated outputs are current" if args.check else "generated runner outputs")
    elif args.command == "resolve":
        resolved = resolve_runner_profile(
            contract, workflow_api=args.api, source_trust=args.source_trust,
            requested_profile=args.profile, caller_inputs=optional_object(args.caller_inputs_json),
            device_family=args.device_family, lock_evidence=optional_object(args.lock_evidence_json),
        )
        print(canonical_json(resolved.as_dict()), end="")
    elif args.command == "validate-selector":
        print(validate_direct_selector(contract, args.labels))
    else:
        print(select_buildah_tier(contract, peak_memory_bytes=args.peak_memory_bytes,
                                  peak_local_storage_bytes=args.peak_local_storage_bytes,
                                  headroom_percent=args.headroom_percent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
