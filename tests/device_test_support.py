from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
FIX = ROOT / "tests/fixtures/device-validation"


def synthetic_environment(family: str = "android") -> dict[str, str]:
    capabilities = {
        "android": "synthetic-android",
        "ios": "synthetic-ios",
        "tvos": "synthetic-tvos",
    }
    host_capacities = {
        "android": "mobile",
        "ios": "apple",
        "tvos": "apple",
    }
    return {
        "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_RUN_ID": "31234567890",
        "GITHUB_RUN_ATTEMPT": "1",
        "CIW_DEVICE_EVENT_REPOSITORY": "StreamScapeTV/ci-workflows",
        "CIW_DEVICE_EVENT_SHA": SHA,
        "CIW_DEVICE_HEAD_REPOSITORY": "StreamScapeTV/ci-workflows",
        "CIW_DEVICE_HEAD_FORK": "false",
        "CIW_DEVICE_SYNTHETIC_MODE": "true",
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_DEVICE_FAMILY": family,
        "INPUT_DEVICE_CAPABILITY": capabilities[family],
        "INPUT_HOST_CAPACITY": host_capacities[family],
        "INPUT_PREPARE_SCRIPT_PATH": "tests/fixtures/device-validation/scripts/prepare.sh",
        "INPUT_TEST_SCRIPT_PATH": "tests/fixtures/device-validation/scripts/test.sh",
        "INPUT_EVIDENCE_SCRIPT_PATH": "tests/fixtures/device-validation/scripts/evidence.sh",
        "INPUT_CLEANUP_SCRIPT_PATH": "tests/fixtures/device-validation/scripts/cleanup.sh",
        "INPUT_ARGUMENTS_JSON": "[]",
        "INPUT_ENVIRONMENT_JSON": "{}",
        "INPUT_MAX_DURATION_MINUTES": "15",
        "INPUT_EVIDENCE_EXCEPTION_ID": "",
        "INPUT_REQUEST_ID": "issue-14-contract-smoke",
    }


def real_environment(
    *,
    repository: str = "StreamScapeTV/streamscape-media",
    family: str = "ios",
    capability: str = "native-failover",
    host_capacity: str | None = None,
    prepare_script_path: str | None = None,
    test_script_path: str | None = None,
    evidence_script_path: str | None = None,
    cleanup_script_path: str | None = None,
    arguments: tuple[str, ...] = (),
    caller_environment: dict[str, str] | None = None,
    command_profile: str = "",
    script_path: str = "",
    alias: str = "",
    secret: bool = False,
) -> dict[str, str]:
    del command_profile, alias, secret
    selected_host = host_capacity or ("apple" if family in {"ios", "tvos"} else "mobile")
    selected_test = test_script_path or script_path or "scripts/ci/run-validation-scope.sh"
    selected_prepare = prepare_script_path or selected_test
    selected_evidence = evidence_script_path or selected_test
    selected_cleanup = cleanup_script_path or selected_test
    return {
        "GITHUB_REPOSITORY": repository,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ID": "31234567891",
        "GITHUB_RUN_ATTEMPT": "1",
        "CIW_DEVICE_EVENT_REPOSITORY": repository,
        "CIW_DEVICE_EVENT_SHA": SHA,
        "CIW_DEVICE_HEAD_REPOSITORY": repository,
        "CIW_DEVICE_HEAD_FORK": "false",
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_DEVICE_FAMILY": family,
        "INPUT_DEVICE_CAPABILITY": capability,
        "INPUT_HOST_CAPACITY": selected_host,
        "INPUT_PREPARE_SCRIPT_PATH": selected_prepare,
        "INPUT_TEST_SCRIPT_PATH": selected_test,
        "INPUT_EVIDENCE_SCRIPT_PATH": selected_evidence,
        "INPUT_CLEANUP_SCRIPT_PATH": selected_cleanup,
        "INPUT_ARGUMENTS_JSON": json.dumps(list(arguments), separators=(",", ":")),
        "INPUT_ENVIRONMENT_JSON": json.dumps(caller_environment or {}, sort_keys=True, separators=(",", ":")),
        "INPUT_MAX_DURATION_MINUTES": "60",
        "INPUT_EVIDENCE_EXCEPTION_ID": "",
        "INPUT_REQUEST_ID": "issue-14-physical-request",
    }
