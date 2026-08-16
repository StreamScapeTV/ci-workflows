"""Secret-minimized live-device execution wrappers.

The production device-lock and owner-authorization receipts are Central authority.
They are required for verification immediately before mutation, but product-owned
scripts must not inherit those receipts, the lock backend root, checkout tokens,
or Central lock internals in their subprocess environment.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

from .device_contract_common import require
from .device_live import (
    _run_product_stage,
    load_selected_device,
    verify_production_lock,
)
from .device_retained_evidence import inspect_retained_evidence
from .device_types import (
    DeviceFamily,
    DevicePlan,
    DeviceResult,
    DeviceValidationError,
    SelectedDevice,
    canonical_json,
)

_DENIED_PRODUCT_ENVIRONMENT = {
    "CHECKOUT_TOKEN",
    "CIW_DEVICE_AUTHORIZATION_RECEIPT",
    "CIW_DEVICE_LOCK_ROOT",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "INPUT_RESOURCE_LOCK_RECEIPT",
}
_DENIED_PRODUCT_PREFIXES = ("CIW_LOCK_",)


def product_environment(
    *,
    environment: Mapping[str, str],
    plan: DevicePlan,
    selected: SelectedDevice,
) -> dict[str, str]:
    """Return the trusted product environment without Central authority material."""

    result = {
        key: value
        for key, value in environment.items()
        if key not in _DENIED_PRODUCT_ENVIRONMENT
        and not any(key.startswith(prefix) for prefix in _DENIED_PRODUCT_PREFIXES)
    }
    result.update(
        {
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "CIW_DEVICE_REQUEST_ID": plan.request.request_id,
            "CIW_DEVICE_SOURCE_SHA": plan.request.admitted_sha,
        }
    )
    if selected.family is DeviceFamily.ANDROID:
        result["ANDROID_SERIAL"] = selected._raw_identifier
    else:
        result["STREAMSCAPE_APPLE_DEVICE"] = selected._raw_identifier
        result["STREAMSCAPE_APPLE_HARDWARE_UDID"] = selected._raw_identifier
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
    """Restore product-owned device state while the exact fencing receipt is valid."""

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
    """Execute one reviewed product profile after exact receipt revalidation."""

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
    cleanup_result = "success"
    timeout_seconds = max(60, plan.request.max_duration_minutes * 60)
    prepared = False
    retained_evidence: list[dict[str, object]] = []

    try:
        _run_product_stage(
            source_root,
            profile.prepare_script,
            args=(),
            environment=runtime_environment,
            timeout_seconds=min(timeout_seconds, 900),
            failure_code="prepare_failed",
        )
        prepared = True
    except DeviceValidationError as error:
        failure_code = error.code
    else:
        try:
            _run_product_stage(
                source_root,
                profile.test_script,
                args=profile.fixed_arguments,
                environment=runtime_environment,
                timeout_seconds=timeout_seconds,
                failure_code="stage_failed",
            )
        except DeviceValidationError as error:
            failure_code = error.code

        # Evidence is a terminal product stage after every attempted test. A
        # failed test remains the primary failure, while an evidence failure can
        # fail an otherwise-successful test but never turn a failed test green.
        try:
            _run_product_stage(
                source_root,
                profile.evidence_script,
                args=(),
                environment=runtime_environment,
                timeout_seconds=min(timeout_seconds, 900),
                failure_code="evidence_policy_failed",
            )
        except DeviceValidationError as error:
            if not failure_code:
                failure_code = error.code
    finally:
        try:
            _run_product_stage(
                source_root,
                profile.cleanup_script,
                args=(),
                environment=runtime_environment,
                timeout_seconds=min(timeout_seconds, 900),
                failure_code="cleanup_failed",
            )
        except DeviceValidationError:
            cleanup_result = "failure"
            if not failure_code:
                failure_code = "cleanup_failed"

    # Product cleanup may retain only one contract-declared redacted handoff.
    # Central validates it before reusable-workflow source/workspace cleanup and
    # records metadata only; the checkout itself is still terminally deleted.
    if prepared and profile.retained_evidence_path is not None:
        try:
            require(
                profile.retained_evidence_media_type is not None,
                "evidence_policy_failed",
            )
            retained_evidence.append(
                inspect_retained_evidence(
                    contract_root=contract_root,
                    source_root=source_root,
                    relative_path=profile.retained_evidence_path,
                    media_type=profile.retained_evidence_media_type,
                )
            )
        except DeviceValidationError as error:
            if not failure_code:
                failure_code = error.code

    result = "failure" if failure_code else "success"
    stable_basis = {
        "repository": plan.request.repository,
        "source_sha": plan.request.admitted_sha,
        "run_id": plan.request.run_id,
        "request_id": plan.request.request_id,
        "device_family": plan.request.family.value,
        "validation_profile": plan.profile.profile_id,
        "result": result,
        "cleanup_result": cleanup_result,
        "retained_evidence": retained_evidence,
    }
    evidence_digest = hashlib.sha256(
        canonical_json(stable_basis).encode("utf-8")
    ).hexdigest()
    return DeviceResult(
        request_id=plan.request.request_id,
        evidence_id=evidence_digest,
        result=result,
        failure_code=failure_code,
        cleanup_result=cleanup_result,
        artifact_exception_used=False,
        selected_device_hash=selected.identity_hash,
        evidence_packet={**stable_basis, "evidence_id": evidence_digest},
    )