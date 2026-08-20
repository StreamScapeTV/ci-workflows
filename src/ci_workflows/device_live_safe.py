"""Secret-minimized generic live-device execution wrappers.

Production lock and owner-authorization receipts are Central authority. Product
scripts receive only a bounded host/runtime environment, the caller's validated
non-secret environment map, generic device metadata, and the runner-provided raw
identifier required to address the selected device.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

from .device_contract_common import require
from .device_live import _run_product_stage, load_selected_device, verify_production_lock
from .device_types import (
    DeviceFamily,
    DevicePlan,
    DeviceResult,
    SelectedDevice,
    canonical_json,
)

_ALLOWED_HOST_ENVIRONMENT = {
    "ANDROID_HOME",
    "ANDROID_SDK_ROOT",
    "DEVELOPER_DIR",
    "HOME",
    "JAVA_HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "RUNNER_TEMP",
    "RUNNER_TOOL_CACHE",
    "SHELL",
    "TMP",
    "TMPDIR",
    "USER",
}


def product_environment(
    *,
    environment: Mapping[str, str],
    plan: DevicePlan,
    selected: SelectedDevice,
) -> dict[str, str]:
    """Return the bounded product environment without Central authority material."""

    result = {
        key: value
        for key, value in environment.items()
        if key in _ALLOWED_HOST_ENVIRONMENT or key.startswith("LC_")
    }
    result.update(plan.profile.command_profile.environment)
    result.update(
        {
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "CIW_DEVICE_REQUEST_ID": plan.request.request_id,
            "CIW_DEVICE_SOURCE_SHA": plan.request.admitted_sha,
            "CIW_DEVICE_FAMILY": plan.request.family.value,
            "CIW_DEVICE_CAPABILITY": plan.request.capability,
            "CIW_DEVICE_HOST_CAPACITY": plan.request.host_capacity,
            "CIW_DEVICE_IDENTIFIER": selected._raw_identifier,
        }
    )
    if selected.family is DeviceFamily.ANDROID:
        result["ANDROID_SERIAL"] = selected._raw_identifier
    return result


def cleanup_live_device(
    *,
    contract_root: Path,
    plan: DevicePlan,
    source_root: Path,
    state_root: Path,
    selected_identity_hash: str,
    authorization_receipt: str,
    resource_lock_receipt: str,
    environment: Mapping[str, str],
) -> None:
    """Run the caller's one restoration/cleanup stage while the lock is valid."""

    selected = load_selected_device(
        state_root=state_root,
        plan=plan,
        expected_identity_hash=selected_identity_hash,
    )
    verify_production_lock(
        contract_root=contract_root,
        plan=plan,
        selected=selected,
        authorization_receipt=authorization_receipt,
        resource_lock_receipt=resource_lock_receipt,
        environment=environment,
    )
    _run_product_stage(
        source_root,
        plan.profile.command_profile.cleanup_script,
        args=(),
        environment=product_environment(
            environment=environment,
            plan=plan,
            selected=selected,
        ),
        timeout_seconds=min(900, max(60, plan.request.max_duration_minutes * 60)),
        failure_code="cleanup_failed",
    )


def execute_live_device(
    *,
    contract_root: Path,
    plan: DevicePlan,
    source_root: Path,
    state_root: Path,
    selected_identity_hash: str,
    authorization_receipt: str,
    resource_lock_receipt: str,
    environment: Mapping[str, str],
) -> DeviceResult:
    """Run prepare/test/evidence only; workflow restoration performs cleanup once."""

    require(
        plan.execution_authorized and not plan.profile.synthetic_only,
        "physical_authorization_required",
    )
    selected = load_selected_device(
        state_root=state_root,
        plan=plan,
        expected_identity_hash=selected_identity_hash,
    )
    verify_production_lock(
        contract_root=contract_root,
        plan=plan,
        selected=selected,
        authorization_receipt=authorization_receipt,
        resource_lock_receipt=resource_lock_receipt,
        environment=environment,
        minimum_remaining_seconds=max(
            1,
            min(300, plan.request.max_duration_minutes * 60),
        ),
    )
    runtime_environment = product_environment(
        environment=environment,
        plan=plan,
        selected=selected,
    )
    profile = plan.profile.command_profile
    failure_code = ""
    timeout_seconds = max(60, plan.request.max_duration_minutes * 60)
    stages = (
        (
            profile.prepare_script,
            (),
            min(timeout_seconds, 900),
            "prepare_failed",
        ),
        (
            profile.test_script,
            profile.fixed_arguments,
            timeout_seconds,
            "stage_failed",
        ),
        (
            profile.evidence_script,
            (),
            min(timeout_seconds, 900),
            "evidence_policy_failed",
        ),
    )
    for script, args, stage_timeout, code in stages:
        try:
            _run_product_stage(
                source_root,
                script,
                args=args,
                environment=runtime_environment,
                timeout_seconds=stage_timeout,
                failure_code=code,
            )
        except Exception as error:
            failure_code = getattr(error, "code", code)
            break

    result = "failure" if failure_code else "success"
    stable_basis = {
        "repository": plan.request.repository,
        "source_sha": plan.request.admitted_sha,
        "run_id": plan.request.run_id,
        "request_id": plan.request.request_id,
        "device_family": plan.request.family.value,
        "device_capability": plan.request.capability,
        "host_capacity": plan.request.host_capacity,
        "result": result,
        "cleanup_result": "deferred-to-restoration",
    }
    evidence_digest = hashlib.sha256(
        canonical_json(stable_basis).encode("utf-8")
    ).hexdigest()
    return DeviceResult(
        request_id=plan.request.request_id,
        evidence_id=evidence_digest,
        result=result,
        failure_code=failure_code,
        cleanup_result="deferred-to-restoration",
        artifact_exception_used=False,
        selected_device_hash=selected.identity_hash,
        evidence_packet={**stable_basis, "evidence_id": evidence_digest},
    )
