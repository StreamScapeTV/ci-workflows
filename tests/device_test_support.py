from __future__ import annotations

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
        "INPUT_DEVICE_ALIAS": "synthetic-primary",
        "INPUT_COMMAND_PROFILE": "ciw-device-synthetic",
        "INPUT_SCRIPT_PATH": "tests/fixtures/device-validation/scripts/test.sh",
        "INPUT_MAX_DURATION_MINUTES": "15",
        "INPUT_EVIDENCE_EXCEPTION_ID": "",
        "INPUT_REQUEST_ID": "issue-14-contract-smoke",
    }

def real_environment(
    *,
    repository: str = "StreamScapeTV/streamscape-media",
    family: str = "ios",
    capability: str = "native-failover",
    command_profile: str = "streamscape-media-ios-device",
    script_path: str = "scripts/ci/run-ios-device-packet.sh",
    alias: str = "media-primary",
    secret: bool = False,
) -> dict[str, str]:
    environment = {
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
        "INPUT_DEVICE_ALIAS": alias,
        "INPUT_COMMAND_PROFILE": command_profile,
        "INPUT_SCRIPT_PATH": script_path,
        "INPUT_MAX_DURATION_MINUTES": "60",
        "INPUT_EVIDENCE_EXCEPTION_ID": "",
        "INPUT_REQUEST_ID": "issue-14-physical-request",
    }
    if secret:
        environment["CIW_DEVICE_LIVE_BACKEND_PRESENT"] = "true"
    return environment

