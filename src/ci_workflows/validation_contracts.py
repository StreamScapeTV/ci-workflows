"""Public API, fixture, and dependency-lock compatibility validation."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .validation_helpers import (
    _actual_names,
    _contract_input_map,
    _events,
    _finding,
    _iter_jobs,
    _permission_mapping,
    _workflow_call_contract,
)
from .validation_model import (
    _REMOTE_WORKFLOW_RE,
    _SEMVER_RE,
    _SHA_RE,
    ActionLock,
    Finding,
    HarnessConfig,
    ParsedDocument,
    PublicContract,
    _read_json,
    _relative,
)


def _validate_public_compatibility(
    document: ParsedDocument,
    public_contract: PublicContract,
    config: HarnessConfig,
    findings: list[Finding],
    functions: Mapping[str, tuple[str, int]],
    docs_text: str,
) -> None:
    record = public_contract.records_by_file.get(document.relative_path)
    if record is None:
        _finding(
            findings,
            config,
            "missing-public-api-record",
            document.relative_path,
            "public reusable workflow is absent from contracts/public-workflows.json fragments",
        )
        return
    call = _workflow_call_contract(document)
    actual_inputs = call.get("inputs", {}) if isinstance(call, Mapping) else {}
    actual_secrets = call.get("secrets", {}) if isinstance(call, Mapping) else {}
    actual_outputs = call.get("outputs", {}) if isinstance(call, Mapping) else {}
    expected_inputs = _contract_input_map(record)
    expected_secrets = {str(value) for value in record.get("secrets", [])}
    expected_outputs = {str(value) for value in record.get("outputs", [])}
    migration_pending = record.get("status") == "migration-pending"
    if not migration_pending:
        if _actual_names(actual_inputs) != set(expected_inputs):
            _finding(
                findings,
                config,
                "workflow-input-drift",
                document.relative_path,
                f"workflow_call inputs differ from contract: actual={sorted(_actual_names(actual_inputs))}, expected={sorted(expected_inputs)}",
            )
        else:
            assert isinstance(actual_inputs, Mapping)
            for name, expected in expected_inputs.items():
                actual = actual_inputs.get(name, {})
                if not isinstance(actual, Mapping):
                    continue
                if bool(actual.get("required", False)) != bool(expected.get("required", False)):
                    _finding(
                        findings,
                        config,
                        "workflow-input-drift",
                        document.relative_path,
                        f"input {name!r} required flag differs from contract",
                    )
                if "default" in expected and actual.get("default") != expected.get("default"):
                    _finding(
                        findings,
                        config,
                        "workflow-input-drift",
                        document.relative_path,
                        f"input {name!r} default differs from contract",
                    )
    if _actual_names(actual_secrets) != expected_secrets:
        _finding(
            findings,
            config,
            "workflow-secret-drift",
            document.relative_path,
            f"workflow_call secrets differ from contract: actual={sorted(_actual_names(actual_secrets))}, expected={sorted(expected_secrets)}",
        )
    if _actual_names(actual_outputs) != expected_outputs:
        _finding(
            findings,
            config,
            "workflow-output-drift",
            document.relative_path,
            f"workflow_call outputs differ from contract: actual={sorted(_actual_names(actual_outputs))}, expected={sorted(expected_outputs)}",
        )
    profile = public_contract.permission_profiles.get(str(record.get("permission_profile", "")))
    if profile is None:
        _finding(
            findings,
            config,
            "unknown-permission-profile",
            document.relative_path,
            "contract permission profile is missing",
        )
    else:
        expected_permissions = profile.get("workflow_permissions", {})
        actual_permissions = _permission_mapping(document.data.get("permissions"))
        if dict(actual_permissions) != dict(expected_permissions):
            _finding(
                findings,
                config,
                "workflow-permission-drift",
                document.relative_path,
                f"workflow permissions differ from profile {record.get('permission_profile')}",
            )
    stable_name = str(record.get("stable_check_name", ""))
    names = [str(document.data.get("name", ""))]
    names.extend(str(job.get("name", "")) for _, job in _iter_jobs(document))
    if stable_name and stable_name not in names:
        _finding(
            findings,
            config,
            "stable-check-drift",
            document.relative_path,
            f"stable check name {stable_name!r} is not an exact workflow or job name",
        )
    components = [str(value) for value in record.get("implementation_components", [])]
    missing_components: list[str] = []
    for component in components:
        if component.endswith((".yml", ".yaml")):
            target = document.path.parents[2] / ".github/workflows" / component
            if not target.exists():
                missing_components.append(component)
        elif component.startswith("actions/") or component.startswith(".github/actions/"):
            target = document.path.parents[2] / component / "action.yml"
            if not target.exists() and not target.with_suffix(".yaml").exists():
                missing_components.append(component)
        elif component.startswith("ci_workflows."):
            if component not in functions:
                missing_components.append(component)
        elif component != "bootstrap-monolithic-workflow":
            missing_components.append(component)
    if missing_components:
        _finding(
            findings,
            config,
            "missing-implementation-component",
            document.relative_path,
            f"contract components do not exist: {sorted(missing_components)}",
        )
    if (
        str(record.get("api_name", "")) not in docs_text
        and document.relative_path not in docs_text
    ):
        _finding(
            findings,
            config,
            "public-api-doc-drift",
            document.relative_path,
            "checked-in documentation does not mention the public API name or workflow path",
        )


def _function_index(
    root: Path, python_sources: Sequence[Path]
) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for path in python_sources:
        relative = path.relative_to(root / "src").with_suffix("")
        module = ".".join(relative.parts)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
            ):
                result[f"{module}.{node.name}"] = (_relative(root, path), node.lineno)
    return result


def _validate_tool_lock(
    root: Path,
    lock: ActionLock,
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    requirement_path = root / "requirements/validation.lock"
    relative_path = "requirements/validation.lock"
    try:
        lines = {
            line.strip()
            for line in requirement_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    except OSError:
        _finding(
            findings,
            config,
            "missing-tool-lock",
            relative_path,
            "validation dependency lock is missing",
        )
        return
    for name, entry in lock.python_packages.items():
        expected = f"{name}=={entry['version']}"
        if expected not in lines:
            _finding(
                findings,
                config,
                "tool-lock-drift",
                relative_path,
                f"expected exact dependency line {expected!r}",
            )
        if not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
            _finding(
                findings,
                config,
                "tool-lock-drift",
                "contracts/action-tool-lock.json",
                f"{name} must include a verified SHA-256 source digest",
            )


def _validate_fixture_coverage(
    root: Path,
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    callers_root = root / "tests/fixtures/harness/callers"
    actual_callers = {
        path.name for path in callers_root.glob("*.y*ml") if path.is_file()
    }
    for missing in sorted(config.required_fixture_callers - actual_callers):
        _finding(
            findings,
            config,
            "missing-caller-fixture",
            "tests/fixtures/harness/callers",
            f"missing {missing}",
        )
    if config.required_event_fixtures:
        events_path = root / "tests/fixtures/harness/events.json"
        try:
            event_payload = _read_json(events_path)
        except ValueError as error:
            _finding(
                findings,
                config,
                "missing-event-fixture",
                _relative(root, events_path),
                str(error),
            )
            event_payload = {}
        event_fixtures = (
            event_payload.get("events", {})
            if isinstance(event_payload, Mapping)
            else {}
        )
        actual_events = (
            {str(name) for name in event_fixtures}
            if isinstance(event_fixtures, Mapping)
            else set()
        )
        for missing in sorted(config.required_event_fixtures - actual_events):
            _finding(
                findings,
                config,
                "missing-event-fixture",
                _relative(root, events_path),
                f"missing event fixture {missing!r}",
            )

    if config.required_service_scenarios:
        services_path = root / "tests/fixtures/harness/service-scenarios.json"
        try:
            services_payload = _read_json(services_path)
        except ValueError as error:
            _finding(
                findings,
                config,
                "missing-service-fixture",
                _relative(root, services_path),
                str(error),
            )
            services_payload = {}
        services = (
            services_payload.get("services", {})
            if isinstance(services_payload, Mapping)
            else {}
        )
        if not isinstance(services, Mapping):
            services = {}
        for service, required in config.required_service_scenarios.items():
            payload = services.get(service)
            if not isinstance(payload, Mapping):
                _finding(
                    findings,
                    config,
                    "missing-service-fixture",
                    _relative(root, services_path),
                    f"service fixture {service!r} is missing",
                )
                continue
            scenarios = payload.get("scenarios", [])
            if not isinstance(scenarios, list):
                _finding(
                    findings,
                    config,
                    "invalid-service-fixture",
                    _relative(root, services_path),
                    f"service {service!r} scenarios must be a list",
                )
                continue
            actual = {
                str(item.get("name"))
                for item in scenarios
                if isinstance(item, Mapping)
            }
            for missing in sorted(required - actual):
                _finding(
                    findings,
                    config,
                    "missing-service-scenario",
                    _relative(root, services_path),
                    f"service {service!r} is missing scenario {missing!r}",
                )


def _validate_caller_fixture(
    document: ParsedDocument,
    public_contract: PublicContract,
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    permissions = _permission_mapping(document.data.get("permissions"))
    for _, job in _iter_jobs(document):
        uses = job.get("uses")
        if not isinstance(uses, str):
            _finding(
                findings,
                config,
                "invalid-caller-fixture",
                document.relative_path,
                "fixture jobs must be thin reusable-workflow calls",
            )
            continue
        match = _REMOTE_WORKFLOW_RE.match(uses)
        if not match:
            _finding(
                findings,
                config,
                "invalid-caller-fixture",
                document.relative_path,
                f"unsupported called workflow {uses!r}",
            )
            continue
        if (
            match.group("owner") != "StreamScapeTV"
            or match.group("repo") != "ci-workflows"
        ):
            _finding(
                findings,
                config,
                "unapproved-reusable-workflow",
                document.relative_path,
                f"caller uses unapproved reusable workflow {uses!r}",
            )
            continue
        reference = match.group("ref")
        if (
            reference != "main"
            and not _SHA_RE.fullmatch(reference)
            and not _SEMVER_RE.fullmatch(reference)
        ):
            _finding(
                findings,
                config,
                "mutable-workflow-reference",
                document.relative_path,
                f"unsupported reusable workflow reference {reference!r}",
            )
        workflow_path = match.group("path")
        record = public_contract.records_by_file.get(workflow_path)
        if record is None:
            _finding(
                findings,
                config,
                "unknown-called-api",
                document.relative_path,
                f"no public API record for {workflow_path}",
            )
            continue
        if job.get("secrets") == "inherit":
            _finding(
                findings,
                config,
                "secrets-inherit",
                document.relative_path,
                "caller fixtures may not use secrets: inherit",
            )
        with_values = job.get("with", {})
        if not isinstance(with_values, Mapping):
            with_values = {}
        forbidden = set(str(key) for key in with_values) & set(
            public_contract.forbidden_caller_fields
        )
        if forbidden:
            _finding(
                findings,
                config,
                "forbidden-caller-input",
                document.relative_path,
                f"caller selects forbidden fields {sorted(forbidden)}",
            )
        allowed_inputs = set(_contract_input_map(record))
        unknown_inputs = set(str(key) for key in with_values) - allowed_inputs
        if unknown_inputs:
            _finding(
                findings,
                config,
                "unknown-caller-input",
                document.relative_path,
                f"caller supplies unknown inputs {sorted(unknown_inputs)}",
            )
        required_inputs = {
            name
            for name, value in _contract_input_map(record).items()
            if value.get("required")
        }
        missing_inputs = required_inputs - set(str(key) for key in with_values)
        if missing_inputs:
            _finding(
                findings,
                config,
                "missing-caller-input",
                document.relative_path,
                f"caller omits required inputs {sorted(missing_inputs)}",
            )
        profile = public_contract.permission_profiles.get(
            str(record.get("permission_profile", ""))
        )
        if profile:
            expected = profile.get("caller_permissions", {})
            if dict(permissions) != dict(expected):
                _finding(
                    findings,
                    config,
                    "caller-permission-drift",
                    document.relative_path,
                    f"caller permissions must exactly match profile {record.get('permission_profile')}",
                )
        events = _events(document)
        permitted = set(str(value) for value in record.get("permitted_events", []))
        aliases = {
            "tag-push": "push",
            "workflow_dispatch-verify-only": "workflow_dispatch",
            "pull_request-closed": "pull_request",
        }
        normalized_permitted = {aliases.get(value, value) for value in permitted}
        if not events <= normalized_permitted:
            _finding(
                findings,
                config,
                "caller-event-drift",
                document.relative_path,
                f"events {sorted(events)} exceed contract {sorted(normalized_permitted)}",
            )
