"""Stable typed command registry and dispatcher for shared CI workflow functions."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import runners
from .ciw_android import configure_android_validate, execute_android_validate
from .ciw_apple import configure_apple_validate, execute_apple_validate
from .ciw_docs import load_command_contract
from .ciw_flutter import configure_flutter_validate, execute_flutter_validate
from .ciw_gitops import configure_gitops_validate, execute_gitops_validate
from .ciw_node import configure_node_validate, execute_node_validate
from .ciw_oci import (
    configure_oci_publish,
    configure_oci_validate,
    execute_oci_publish,
    execute_oci_validate,
)
from .ciw_python import configure_python_validate, execute_python_validate
from .ciw_types import (
    CIWContext,
    CIWError,
    CIWResult,
    input_value,
    project_error,
    required_environment,
)
from .dependencies import checkout_private_dependency
from .evidence import build_evidence, parse_toolchain_json, write_evidence
from .foundation_types import FoundationError, bounded_int, canonical_json
from .policy import validate_cache_request, verify_repository_policy
from .release_tag_authority import (
    GitHubTagProvider,
    ReleaseInputs,
    authority_from_expected,
    event_from_environment,
    revalidate_release_authority,
    resolve_release_authority,
)
from .source_admission import revalidate_admission
from .source_checkout import exact_checkout
from .source_cli import resolve_from_environment
from .source_github import GitHubSourceProvider
from .source_types import AdmissionResult, SourceAdmissionError, TrustMode
from .tooling import install_locked_asset, verify_runtime_capability, verify_tool_set
from .workspace import (
    WorkspaceContext,
    cleanup_workspace,
    prepare_workspace,
    register_state_path,
    resolve_state_root,
)

Handler = Callable[[argparse.Namespace, CIWContext], CIWResult]
Configure = Callable[[argparse.ArgumentParser], None]


@dataclass(frozen=True)
class CommandSpec:
    domain: str
    operation: str
    handler: Handler
    configure: Configure

    @property
    def key(self) -> str:
        return f"{self.domain} {self.operation}"

    @property
    def qualified_handler(self) -> str:
        return f"{self.handler.__module__}.{self.handler.__name__}"


def _noop(_parser: argparse.ArgumentParser) -> None:
    return


def _add_source_revalidate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--admission-json")


def _add_source_checkout(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--admitted-sha", required=True)
    parser.add_argument("--path", default="source")
    parser.add_argument("--fetch-depth", type=int, default=1)


def _add_runner_generate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--check", action="store_true")


def _add_runner_resolve(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api", required=True)
    parser.add_argument("--source-trust", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--device-family")
    parser.add_argument("--caller-inputs-json")
    parser.add_argument("--lock-evidence-json")


def _add_runner_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("labels", nargs="+")


def _add_runner_tier(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--peak-memory-bytes", type=int, required=True)
    parser.add_argument("--peak-local-storage-bytes", type=int, required=True)
    parser.add_argument("--headroom-percent", type=int, default=20)


def _add_android_validate(parser: argparse.ArgumentParser) -> None:
    configure_android_validate(parser)


def _add_apple_validate(parser: argparse.ArgumentParser) -> None:
    configure_apple_validate(parser)


def _add_flutter_validate(parser: argparse.ArgumentParser) -> None:
    configure_flutter_validate(parser)


def _add_python_validate(parser: argparse.ArgumentParser) -> None:
    configure_python_validate(parser)


def _add_node_validate(parser: argparse.ArgumentParser) -> None:
    configure_node_validate(parser)


def _add_gitops_validate(parser: argparse.ArgumentParser) -> None:
    configure_gitops_validate(parser)


def _add_oci_publish(parser: argparse.ArgumentParser) -> None:
    configure_oci_publish(parser)


def _add_oci_validate(parser: argparse.ArgumentParser) -> None:
    configure_oci_validate(parser)


def _add_workspace_prepare(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile")
    parser.add_argument("--cache-mode")
    parser.add_argument("--source-sha")
    parser.add_argument("--lock-digest")
    parser.add_argument("--trust-mode")


def _add_tooling_verify(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tool-set")
    parser.add_argument("--capability-profile")


def _add_tooling_install(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--asset-id")


def _add_dependency_checkout(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository")
    parser.add_argument("--admitted-sha")
    parser.add_argument("--dependency-id")
    parser.add_argument("--expected-subpath")
    parser.add_argument("--fetch-depth", type=int)


def _add_policy_verify(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase")
    parser.add_argument("--artifacts-json")
    parser.add_argument("--artifact-exception-id")
    parser.add_argument("--trust-mode")


def _add_cache_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode")
    parser.add_argument("--repository")
    parser.add_argument("--source-sha")
    parser.add_argument("--lock-digest")
    parser.add_argument("--platform")
    parser.add_argument("--profile")
    parser.add_argument("--trust-mode")


def _add_evidence_render(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-sha")
    parser.add_argument("--workflow-release")
    parser.add_argument("--runner-profile")
    parser.add_argument("--toolchain-json")
    parser.add_argument("--command-profile")
    parser.add_argument("--result")
    parser.add_argument("--cleanup-state")
    parser.add_argument("--cleanup-removed-paths", type=int)


def _argument_or_input(
    args: argparse.Namespace,
    context: CIWContext,
    name: str,
    default: str = "",
) -> str:
    value = getattr(args, name, None)
    if value is not None:
        return str(value).strip()
    return input_value(context.environment, name, default)


def _required_argument_or_input(
    args: argparse.Namespace,
    context: CIWContext,
    name: str,
    *,
    domain: str,
    code: str,
) -> str:
    value = _argument_or_input(args, context, name)
    if not value:
        raise CIWError(domain, code)
    return value


def _require_output(context: CIWContext, *, domain: str) -> None:
    required_environment(
        context.environment,
        "GITHUB_OUTPUT",
        domain=domain,
        code="github_output_missing",
    )


def _source_summary(result: AdmissionResult) -> str:
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
    return "\n".join(lines) + "\n"


def handle_source_resolve(
    _args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    _require_output(context, domain="source")
    required_environment(
        context.environment,
        "GITHUB_STEP_SUMMARY",
        domain="source",
        code="github_summary_missing",
    )
    result = resolve_from_environment(context.root, environment=context.environment)
    return CIWResult(
        "source",
        "resolve",
        outputs=result.output_values(),
        summary=_source_summary(result),
    )


def _admission_from_json(raw: str) -> AdmissionResult:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SourceAdmissionError("invalid_admission_json") from error
    if not isinstance(payload, Mapping):
        raise SourceAdmissionError("invalid_admission_json")
    names = {field.name for field in dataclasses.fields(AdmissionResult)}
    if set(payload) != names:
        raise SourceAdmissionError("invalid_admission_json")
    values = dict(payload)
    try:
        values["trust_mode"] = TrustMode(values["trust_mode"])
        return AdmissionResult(**values)
    except (TypeError, ValueError) as error:
        raise SourceAdmissionError("invalid_admission_json") from error


def handle_source_revalidate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    raw = args.admission_json or input_value(
        context.environment,
        "admission_json",
    )
    if not raw:
        raise SourceAdmissionError("admission_json_required")
    result = _admission_from_json(raw)
    provider = GitHubSourceProvider(context.environment.get("GITHUB_TOKEN", ""))
    revalidate_admission(result, provider)
    return CIWResult(
        "source",
        "revalidate",
        outputs={
            "source_sha": result.source_sha,
            "evidence_id": result.evidence_id,
            "revalidated": "true",
        },
        stdout_text=canonical_json(
            {
                "evidence_id": result.evidence_id,
                "revalidated": True,
                "source_sha": result.source_sha,
            }
        ),
    )


def handle_source_exact_checkout(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    _require_output(context, domain="source")
    outputs = exact_checkout(
        repository=args.repository,
        admitted_sha=args.admitted_sha,
        path=args.path,
        fetch_depth=args.fetch_depth,
        token=context.environment.get("CHECKOUT_TOKEN", ""),
        workspace=Path(context.environment.get("GITHUB_WORKSPACE", ".")),
    )
    return CIWResult("source", "exact-checkout", outputs=dict(outputs))


def handle_runners_validate(
    _args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    contract = runners.load_runner_contract(context.root)
    report = runners.generate_compatibility_report(
        contract,
        runners.load_workflow_inventory(context.root),
    )
    return CIWResult(
        "runners",
        "validate",
        stdout_text=(
            f"validated {len(contract['profiles'])} runner profiles and "
            f"{report['workflow_count']} inventory mappings"
        ),
    )


def handle_runners_generate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    runners.load_runner_contract(context.root)
    runners.write_generated_outputs(context.root, check=args.check)
    return CIWResult(
        "runners",
        "generate",
        stdout_text=(
            "runner generated outputs are current"
            if args.check
            else "generated runner outputs"
        ),
    )


def _runner_object(value: str | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise runners.RunnerContractError(
            "invalid-cli-json",
            "object required",
        ) from error
    runners.require(
        isinstance(payload, dict),
        "invalid-cli-json",
        "object required",
    )
    return payload


def handle_runners_resolve(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    contract = runners.load_runner_contract(context.root)
    resolved = runners.resolve_runner_profile(
        contract,
        workflow_api=args.api,
        source_trust=args.source_trust,
        requested_profile=args.profile,
        caller_inputs=_runner_object(args.caller_inputs_json),
        device_family=args.device_family,
        lock_evidence=_runner_object(args.lock_evidence_json),
    )
    return CIWResult(
        "runners",
        "resolve",
        stdout_text=runners.canonical_json(resolved.as_dict()),
    )


def handle_runners_validate_selector(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    profile = runners.validate_direct_selector(
        runners.load_runner_contract(context.root),
        args.labels,
    )
    return CIWResult("runners", "validate-selector", stdout_text=profile)


def handle_runners_select_buildah_tier(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    profile = runners.select_buildah_tier(
        runners.load_runner_contract(context.root),
        peak_memory_bytes=args.peak_memory_bytes,
        peak_local_storage_bytes=args.peak_local_storage_bytes,
        headroom_percent=args.headroom_percent,
    )
    return CIWResult(
        "runners",
        "select-buildah-tier",
        stdout_text=profile,
    )


def handle_android_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return execute_android_validate(args, context)


def handle_apple_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return execute_apple_validate(args, context)


def handle_flutter_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return execute_flutter_validate(args, context)


def handle_python_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return execute_python_validate(args, context)


def handle_node_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return execute_node_validate(args, context)


def handle_gitops_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return execute_gitops_validate(args, context)


def handle_oci_publish(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return execute_oci_publish(args, context)


def handle_oci_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return execute_oci_validate(args, context)


def _foundation_environment(
    context: CIWContext,
    name: str,
    *,
    required: bool = True,
    default: str = "",
) -> str:
    value = context.environment.get(name, default)
    if required and not value:
        raise FoundationError("required_environment_missing")
    return value


def _state_root(context: CIWContext) -> Path:
    return resolve_state_root(
        runner_temp=Path(_foundation_environment(context, "RUNNER_TEMP")),
        state_id=_foundation_environment(context, "CI_WORKFLOW_STATE_ID"),
        declared_root=_foundation_environment(context, "CI_WORKFLOW_ROOT"),
        contract_root=context.root,
    )


def handle_workspace_prepare(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    workspace_context = WorkspaceContext(
        workspace=Path(_foundation_environment(context, "GITHUB_WORKSPACE")),
        runner_temp=Path(_foundation_environment(context, "RUNNER_TEMP")),
        repository=_foundation_environment(context, "GITHUB_REPOSITORY"),
        run_id=_foundation_environment(context, "GITHUB_RUN_ID"),
        run_attempt=bounded_int(
            _foundation_environment(
                context,
                "GITHUB_RUN_ATTEMPT",
                default="1",
            ),
            minimum=1,
            maximum=1_000_000,
            instruction="invalid_run_attempt",
        ),
        job=_foundation_environment(context, "GITHUB_JOB"),
        runner_os=_foundation_environment(context, "RUNNER_OS"),
    )
    state = prepare_workspace(
        workspace_context,
        profile=_argument_or_input(args, context, "profile", "minimal"),
        cache_mode=_argument_or_input(args, context, "cache_mode", "disabled"),
        source_sha=_argument_or_input(args, context, "source_sha") or None,
        lock_digest=_argument_or_input(args, context, "lock_digest") or None,
        trust_mode=_argument_or_input(args, context, "trust_mode") or None,
        contract_root=context.root,
    )
    return CIWResult(
        "workspace",
        "prepare",
        outputs=state.output_values(),
        environment=state.environment,
    )


def handle_workspace_cleanup(
    _args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    report = cleanup_workspace(
        _state_root(context),
        expected_state_id=context.environment.get("CI_WORKFLOW_STATE_ID") or None,
        contract_root=context.root,
    )
    return CIWResult(
        "workspace",
        "cleanup",
        outputs=report.output_values(),
    )


def handle_tooling_verify(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    evidence = verify_tool_set(
        _argument_or_input(args, context, "tool_set", "baseline"),
        contract_root=context.root,
    )
    capability = verify_runtime_capability(
        _argument_or_input(
            args,
            context,
            "capability_profile",
            "baseline",
        ),
        declared_os=context.environment.get("RUNNER_OS") or None,
        declared_architecture=context.environment.get("RUNNER_ARCH") or None,
        contract_root=context.root,
    )
    return CIWResult(
        "tooling",
        "verify",
        outputs={
            **evidence.output_values(),
            **capability.output_values(),
        },
    )


def handle_tooling_install_asset(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    asset_id = _required_argument_or_input(
        args,
        context,
        "asset_id",
        domain="tooling",
        code="locked_asset_id_required",
    )
    relative = f"tools/{asset_id}"
    destination = register_state_path(
        _state_root(context),
        name=f"tool-{asset_id}",
        relative=relative,
        kind="tool",
        contract_root=context.root,
        create=True,
    )
    installed = install_locked_asset(
        asset_id,
        destination_root=destination,
        contract_root=context.root,
    )
    outputs = installed.output_values()
    outputs["asset_relative_path"] = f"{relative}/{installed.filename}"
    return CIWResult(
        "tooling",
        "install-asset",
        outputs=outputs,
        environment={
            "CI_LOCKED_TOOL_PATH": str(destination / installed.filename)
        },
    )


def handle_dependencies_checkout_private(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    fetch_depth_raw: str | int
    if args.fetch_depth is not None:
        fetch_depth_raw = args.fetch_depth
    else:
        fetch_depth_raw = input_value(context.environment, "fetch_depth", "1")
    result = checkout_private_dependency(
        state_root=_state_root(context),
        repository=_required_argument_or_input(
            args,
            context,
            "repository",
            domain="dependencies",
            code="invalid_dependency_repository",
        ),
        admitted_sha=_required_argument_or_input(
            args,
            context,
            "admitted_sha",
            domain="dependencies",
            code="dependency_sha_must_be_full_sha",
        ),
        dependency_id=_required_argument_or_input(
            args,
            context,
            "dependency_id",
            domain="dependencies",
            code="invalid_dependency_id",
        ),
        expected_subpath=_argument_or_input(
            args,
            context,
            "expected_subpath",
            ".",
        ),
        fetch_depth=bounded_int(
            fetch_depth_raw,
            minimum=1,
            maximum=1000,
            instruction="invalid_dependency_fetch_depth",
        ),
        token=context.environment.get("PRIVATE_DEPENDENCY_TOKEN", ""),
        contract_root=context.root,
    )
    target = _state_root(context) / result.relative_path
    return CIWResult(
        "dependencies",
        "checkout-private",
        outputs=result.output_values(),
        environment={"CI_PRIVATE_DEPENDENCY_PATH": str(target)},
    )


def handle_policy_verify_repository(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    report = verify_repository_policy(
        Path(_foundation_environment(context, "GITHUB_WORKSPACE")),
        repository=_foundation_environment(context, "GITHUB_REPOSITORY"),
        phase=_argument_or_input(args, context, "phase", "after"),
        artifact_manifest_json=_argument_or_input(
            args,
            context,
            "artifacts_json",
            "[]",
        ),
        artifact_exception_id=_argument_or_input(
            args,
            context,
            "artifact_exception_id",
        )
        or None,
        trust_mode=_argument_or_input(args, context, "trust_mode") or None,
        contract_root=context.root,
    )
    return CIWResult(
        "policy",
        "verify-repository",
        outputs=report.output_values(),
    )


def handle_policy_validate_cache(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    decision = validate_cache_request(
        mode=_argument_or_input(args, context, "mode", "disabled"),
        repository=_argument_or_input(
            args,
            context,
            "repository",
            context.environment.get("GITHUB_REPOSITORY", ""),
        ),
        source_sha=_argument_or_input(args, context, "source_sha") or None,
        lock_digest=_argument_or_input(args, context, "lock_digest") or None,
        platform=_argument_or_input(
            args,
            context,
            "platform",
            context.environment.get("RUNNER_OS", "").lower(),
        ),
        profile=_argument_or_input(args, context, "profile", "minimal"),
        trust_mode=_argument_or_input(args, context, "trust_mode") or None,
        contract_root=context.root,
    )
    return CIWResult(
        "policy",
        "validate-cache",
        outputs={
            "mode": decision.mode,
            "restore": str(decision.restore).lower(),
            "save": str(decision.save).lower(),
            "key": decision.key or "",
        },
        stdout_text=canonical_json(
            {
                "key": decision.key,
                "mode": decision.mode,
                "restore": decision.restore,
                "save": decision.save,
            }
        ),
    )


def handle_evidence_render(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    toolchain = parse_toolchain_json(
        _argument_or_input(args, context, "toolchain_json", "{}")
    )
    cleanup_count: int | str
    if args.cleanup_removed_paths is not None:
        cleanup_count = args.cleanup_removed_paths
    else:
        cleanup_count = input_value(
            context.environment,
            "cleanup_removed_paths",
            "0",
        )
    result = build_evidence(
        source_sha=_required_argument_or_input(
            args,
            context,
            "source_sha",
            domain="evidence",
            code="exact_sha_required",
        ),
        workflow_release=_required_argument_or_input(
            args,
            context,
            "workflow_release",
            domain="evidence",
            code="invalid_workflow_release",
        ),
        runner_profile=_required_argument_or_input(
            args,
            context,
            "runner_profile",
            domain="evidence",
            code="invalid_runner_profile",
        ),
        toolchain=toolchain,
        command_profile=_required_argument_or_input(
            args,
            context,
            "command_profile",
            domain="evidence",
            code="invalid_command_profile",
        ),
        result=_required_argument_or_input(
            args,
            context,
            "result",
            domain="evidence",
            code="invalid_evidence_result",
        ),
        cleanup_state=_argument_or_input(
            args,
            context,
            "cleanup_state",
            "not-run",
        ),
        cleanup_removed_paths=bounded_int(
            cleanup_count,
            minimum=0,
            maximum=10000,
            instruction="invalid_cleanup_count",
        ),
        contract_root=context.root,
    )
    path = write_evidence(_state_root(context), result)
    return CIWResult(
        "evidence",
        "render",
        outputs=result.output_values(),
        environment={"CI_EVIDENCE_FILE": str(path)},
    )


def _release_provider(context: CIWContext) -> GitHubTagProvider:
    return GitHubTagProvider(
        api_url=required_environment(
            context.environment,
            "GITHUB_API_URL",
            domain="release-tag",
        ),
        token=required_environment(
            context.environment,
            "GITHUB_TOKEN",
            domain="release-tag",
        ),
    )


def _release_outputs(authority: Any) -> dict[str, str]:
    return {
        "release_mode": authority.release_mode,
        "release_version": authority.release_version,
        "release_source_sha": authority.release_source_sha,
        "tag_object_sha": authority.tag_object_sha,
        "tag_commit_sha": authority.tag_commit_sha,
    }


def handle_release_tag_resolve(
    _args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    _require_output(context, domain="release-tag")
    authority = resolve_release_authority(
        ReleaseInputs(
            release_mode=input_value(
                context.environment,
                "release_mode",
                "tag-push",
            ),
            release_version=input_value(
                context.environment,
                "release_version",
            ),
            release_source_sha=input_value(
                context.environment,
                "release_source_sha",
            ),
        ),
        event_from_environment(context.environment),
        _release_provider(context),
    )
    return CIWResult(
        "release-tag",
        "resolve",
        outputs=_release_outputs(authority),
        stdout_text=(
            "release tag authority accepted: "
            f"mode={authority.release_mode} "
            f"version={authority.release_version} "
            f"source={authority.release_source_sha}"
        ),
    )


def handle_release_tag_revalidate(
    _args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    _require_output(context, domain="release-tag")
    authority = authority_from_expected(
        release_mode=required_environment(
            context.environment,
            "INPUT_RELEASE_MODE",
            domain="release-tag",
        ),
        release_version=required_environment(
            context.environment,
            "INPUT_RELEASE_VERSION",
            domain="release-tag",
        ),
        release_source_sha=required_environment(
            context.environment,
            "INPUT_RELEASE_SOURCE_SHA",
            domain="release-tag",
        ),
        tag_object_sha=required_environment(
            context.environment,
            "INPUT_EXPECTED_TAG_OBJECT_SHA",
            domain="release-tag",
        ),
        tag_commit_sha=required_environment(
            context.environment,
            "INPUT_EXPECTED_TAG_COMMIT_SHA",
            domain="release-tag",
        ),
    )
    authority = revalidate_release_authority(
        authority,
        event_from_environment(context.environment),
        _release_provider(context),
    )
    return CIWResult(
        "release-tag",
        "revalidate",
        outputs=_release_outputs(authority),
        stdout_text=(
            "release tag authority accepted: "
            f"mode={authority.release_mode} "
            f"version={authority.release_version} "
            f"source={authority.release_source_sha}"
        ),
    )


def command_specs() -> tuple[CommandSpec, ...]:
    return (
        CommandSpec("source", "resolve", handle_source_resolve, _noop),
        CommandSpec(
            "source",
            "revalidate",
            handle_source_revalidate,
            _add_source_revalidate,
        ),
        CommandSpec(
            "source",
            "exact-checkout",
            handle_source_exact_checkout,
            _add_source_checkout,
        ),
        CommandSpec("runners", "validate", handle_runners_validate, _noop),
        CommandSpec(
            "runners",
            "generate",
            handle_runners_generate,
            _add_runner_generate,
        ),
        CommandSpec(
            "runners",
            "resolve",
            handle_runners_resolve,
            _add_runner_resolve,
        ),
        CommandSpec(
            "runners",
            "validate-selector",
            handle_runners_validate_selector,
            _add_runner_selector,
        ),
        CommandSpec(
            "runners",
            "select-buildah-tier",
            handle_runners_select_buildah_tier,
            _add_runner_tier,
        ),
        CommandSpec(
            "android",
            "validate",
            handle_android_validate,
            _add_android_validate,
        ),
        CommandSpec(
            "apple",
            "validate",
            handle_apple_validate,
            _add_apple_validate,
        ),
        CommandSpec(
            "flutter",
            "validate",
            handle_flutter_validate,
            _add_flutter_validate,
        ),
        CommandSpec(
            "python",
            "validate",
            handle_python_validate,
            _add_python_validate,
        ),
        CommandSpec(
            "node",
            "validate",
            handle_node_validate,
            _add_node_validate,
        ),
        CommandSpec(
            "gitops",
            "validate",
            handle_gitops_validate,
            _add_gitops_validate,
        ),
        CommandSpec(
            "oci",
            "publish",
            handle_oci_publish,
            _add_oci_publish,
        ),
        CommandSpec(
            "oci",
            "validate",
            handle_oci_validate,
            _add_oci_validate,
        ),
        CommandSpec(
            "workspace",
            "prepare",
            handle_workspace_prepare,
            _add_workspace_prepare,
        ),
        CommandSpec(
            "workspace",
            "cleanup",
            handle_workspace_cleanup,
            _noop,
        ),
        CommandSpec(
            "tooling",
            "verify",
            handle_tooling_verify,
            _add_tooling_verify,
        ),
        CommandSpec(
            "tooling",
            "install-asset",
            handle_tooling_install_asset,
            _add_tooling_install,
        ),
        CommandSpec(
            "dependencies",
            "checkout-private",
            handle_dependencies_checkout_private,
            _add_dependency_checkout,
        ),
        CommandSpec(
            "policy",
            "verify-repository",
            handle_policy_verify_repository,
            _add_policy_verify,
        ),
        CommandSpec(
            "policy",
            "validate-cache",
            handle_policy_validate_cache,
            _add_cache_validate,
        ),
        CommandSpec(
            "evidence",
            "render",
            handle_evidence_render,
            _add_evidence_render,
        ),
        CommandSpec(
            "release-tag",
            "resolve",
            handle_release_tag_resolve,
            _noop,
        ),
        CommandSpec(
            "release-tag",
            "revalidate",
            handle_release_tag_revalidate,
            _noop,
        ),
    )


def runtime_command_index() -> dict[str, CommandSpec]:
    result: dict[str, CommandSpec] = {}
    for spec in command_specs():
        if spec.key in result:
            raise CIWError("ciw", "ciw_runtime_command_duplicate")
        result[spec.key] = spec
    return result


def validate_runtime_contract(root: Path) -> None:
    contract = load_command_contract(root)
    contract_handlers = {
        f"{item['domain']} {item['operation']}": item["handler"]
        for item in contract["commands"]
    }
    runtime = runtime_command_index()
    if set(contract_handlers) != set(runtime):
        raise CIWError("ciw", "ciw_runtime_contract_drift")
    for key, spec in runtime.items():
        if contract_handlers[key] != spec.qualified_handler:
            raise CIWError("ciw", "ciw_runtime_handler_drift")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="ciw",
        description=__doc__,
    )
    result.add_argument("--root", type=Path, default=Path.cwd())
    domains = result.add_subparsers(dest="domain", required=True)
    grouped: dict[str, list[CommandSpec]] = {}
    for spec in command_specs():
        grouped.setdefault(spec.domain, []).append(spec)
    for domain in sorted(grouped):
        domain_parser = domains.add_parser(domain)
        operations = domain_parser.add_subparsers(
            dest="operation",
            required=True,
        )
        for spec in sorted(grouped[domain], key=lambda item: item.operation):
            operation_parser = operations.add_parser(spec.operation)
            spec.configure(operation_parser)
            operation_parser.set_defaults(_command_spec=spec)
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    args = parser().parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    context = CIWContext(
        root=args.root.resolve(),
        environment=dict(os.environ if environment is None else environment),
        stdout=output,
        stderr=errors,
    )
    spec: CommandSpec = args._command_spec
    try:
        validate_runtime_contract(context.root)
        result = spec.handler(args, context)
        if result.domain != spec.domain or result.operation != spec.operation:
            raise CIWError("ciw", "ciw_result_command_mismatch")
        result.emit(context)
    except BaseException as error:
        projected = project_error(error, domain=spec.domain)
        errors.write(
            f"ciw {spec.domain} {spec.operation} failed: "
            f"{projected.code}\n"
        )
        return projected.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
