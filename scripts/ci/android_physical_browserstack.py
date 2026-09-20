#!/usr/bin/env python3
"""Bounded BrowserStack physical-Android performance transport for Central CI.

The provider, device models, OS versions, wrapper path, binary version, timeouts,
result schema, and evidence bounds are fixed here. Caller-controlled provider
selection, device names, commands, environment maps, and timeouts are not
accepted.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

PROVIDER = "browserstack"
DEVICE_TUNNEL_VERSION = "1.29.0"
DEVICE_TUNNEL_URL = (
    "https://sdk-assets.browserstack.com/"
    f"binary-linux-x64-{DEVICE_TUNNEL_VERSION}.zip"
)
DEDICATED_DEVICES_URL = (
    "https://api-cloud.browserstack.com/app-automate/dedicated_devices"
)
WRAPPER = "scripts/ci/run-android-physical-performance.sh"
MAX_PROVIDER_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_MEASUREMENTS = 128
MAX_SCENARIOS = 64
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,159}\Z")
MEASUREMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}\Z")
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")
ARTIFACT_SHA = re.compile(r"[0-9a-f]{64}\Z")
DEVICE_SPECS = (
    {
        "deviceClass": "phone",
        "deviceModel": "Samsung Galaxy S24",
        "osVersion": "14.0",
    },
    {
        "deviceClass": "tablet",
        "deviceModel": "Samsung Galaxy Tab S9",
        "osVersion": "13.0",
    },
    {
        "deviceClass": "television",
        "deviceModel": "Nvidia Shield TV Pro 2019",
        "osVersion": "11.0",
    },
)
ADB_MODEL_PATTERNS = {
    "Samsung Galaxy S24": (
        re.compile(r"Samsung Galaxy S24", re.IGNORECASE),
        re.compile(r"Galaxy S24", re.IGNORECASE),
        re.compile(r"SM-S921[A-Z0-9-]*", re.IGNORECASE),
    ),
    "Samsung Galaxy Tab S9": (
        re.compile(r"Samsung Galaxy Tab S9", re.IGNORECASE),
        re.compile(r"Galaxy Tab S9", re.IGNORECASE),
        re.compile(r"SM-X71[068][A-Z0-9-]*", re.IGNORECASE),
    ),
    "Nvidia Shield TV Pro 2019": (
        re.compile(r"Nvidia Shield TV Pro 2019", re.IGNORECASE),
        re.compile(r"SHIELD Android TV", re.IGNORECASE),
        re.compile(r"P2897", re.IGNORECASE),
    ),
}
ALLOWED_UNITS = {
    "ms",
    "bytes",
    "bytes_delta",
    "count",
    "fraction",
    "percent",
    "fps",
}
ALLOWED_MEASUREMENT_STATUS = {"passed", "failed", "observed"}
ALLOWED_SCENARIO_STATUS = {"passed", "failed"}


class ExpectedFailure(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _write_private(log_path: Path, text: str) -> None:
    if not text:
        return
    with log_path.open("a", encoding="utf-8", errors="replace") as stream:
        stream.write(text)
        if not text.endswith("\n"):
            stream.write("\n")


def _run(
    args: list[str],
    *,
    log_path: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env=env,
    )
    _write_private(log_path, result.stdout)
    _write_private(log_path, result.stderr)
    return result


def _json_from_output(text: str) -> object:
    stripped = text.strip()
    if not stripped:
        raise ValueError("provider returned no JSON")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        starts = [value for value in (stripped.find("{"), stripped.find("[")) if value >= 0]
        if not starts:
            raise
        start = min(starts)
        for end in range(len(stripped), start, -1):
            try:
                return json.loads(stripped[start:end])
            except json.JSONDecodeError:
                continue
        raise


def _walk_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _field(node: dict, *names: str):
    desired = {_normalized_key(name) for name in names}
    for key, value in node.items():
        if _normalized_key(str(key)) in desired:
            return value
    return None


def _basic_auth(username: str, access_key: str) -> str:
    token = base64.b64encode(f"{username}:{access_key}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def fetch_dedicated_devices(username: str, access_key: str) -> list[dict]:
    request = urllib.request.Request(
        DEDICATED_DEVICES_URL,
        headers={
            "Authorization": _basic_auth(username, access_key),
            "Accept": "application/json",
            "User-Agent": "StreamScapeTV-ci-workflows-android-physical-v1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack dedicated-device discovery request failed",
        ) from exc
    if len(data) > MAX_PROVIDER_RESPONSE_BYTES:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack dedicated-device discovery response exceeded the bound",
        )
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack dedicated-device discovery returned invalid JSON",
        ) from exc
    if isinstance(value, dict) and isinstance(value.get("devices"), list):
        value = value["devices"]
    elif isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or len(value) > 10000:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack dedicated-device discovery returned an invalid device list",
        )
    if any(not isinstance(item, dict) for item in value):
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack dedicated-device discovery returned malformed device metadata",
        )
    return value


def select_device(devices: list[dict], spec: dict[str, str]) -> dict:
    matches = []
    for item in devices:
        if str(item.get("os", "")).casefold() != "android":
            continue
        if item.get("device") != spec["deviceModel"]:
            continue
        if str(item.get("os_version", "")) != spec["osVersion"]:
            continue
        if item.get("realMobile") is not True:
            continue
        matches.append(item)
    if not matches:
        raise ExpectedFailure(
            "device_class_unavailable",
            f"BrowserStack has no reviewed {spec['deviceClass']} model/OS in the dedicated fleet",
        )
    if len(matches) != 1:
        raise ExpectedFailure(
            "device_class_ambiguous",
            f"BrowserStack returned multiple reviewed {spec['deviceClass']} model/OS matches",
        )
    item = matches[0]
    state_value = str(item.get("state", "")).strip().casefold()
    if state_value != "available":
        raise ExpectedFailure(
            "device_busy_or_offline",
            f"BrowserStack reviewed {spec['deviceClass']} device is not available",
        )
    udid = item.get("udid")
    if not isinstance(udid, str) or not 4 <= len(udid) <= 256:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack dedicated-device metadata omitted a bounded device id",
        )
    return {
        "udid": udid,
        "deviceClass": spec["deviceClass"],
        "deviceModel": spec["deviceModel"],
        "osVersion": spec["osVersion"],
        "deviceIdentitySha256": hashlib.sha256(udid.encode("utf-8")).hexdigest(),
    }


def install_device_tunnel(root: Path, log_path: Path) -> Path:
    archive = root / "browserstack-device-tunnel.zip"
    request = urllib.request.Request(
        DEVICE_TUNNEL_URL,
        headers={"User-Agent": "StreamScapeTV-ci-workflows-android-physical-v1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            with archive.open("wb") as stream:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 256 * 1024 * 1024:
                        raise ExpectedFailure(
                            "provider_transport_failure",
                            "BrowserStack Device Tunnel archive exceeded the bound",
                        )
                    stream.write(chunk)
    except ExpectedFailure:
        raise
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack Device Tunnel download failed",
        ) from exc
    try:
        with zipfile.ZipFile(archive) as bundle:
            candidates = [
                info
                for info in bundle.infolist()
                if not info.is_dir() and Path(info.filename).name == "binary-linux-x64"
            ]
            if len(candidates) != 1:
                raise ExpectedFailure(
                    "provider_transport_failure",
                    "BrowserStack Device Tunnel archive has an unexpected binary layout",
                )
            info = candidates[0]
            if info.file_size <= 0 or info.file_size > 256 * 1024 * 1024:
                raise ExpectedFailure(
                    "provider_transport_failure",
                    "BrowserStack Device Tunnel binary size is outside the bound",
                )
            binary = root / "binary-linux-x64"
            with bundle.open(info) as source, binary.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    except ExpectedFailure:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack Device Tunnel archive could not be verified",
        ) from exc
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    probe = _run(
        [str(binary), "device-tunnel", "--help"],
        log_path=log_path,
        timeout=30,
    )
    if probe.returncode != 0:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack Device Tunnel binary failed its bounded startup probe",
        )
    return binary


def _write_config(root: Path, username: str, access_key: str) -> Path:
    config = root / "device-tunnel.yml"
    config.write_text(
        "browserstack_username: "
        + json.dumps(username)
        + "\nbrowserstack_accesskey: "
        + json.dumps(access_key)
        + "\nenvironment: production\nenable_ios: false\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    return config


def start_orchestrator(
    binary: Path,
    root: Path,
    username: str,
    access_key: str,
    log_path: Path,
):
    config = _write_config(root, username, access_key)
    process = subprocess.Popen(
        [str(binary), "device-tunnel", "start", "--config", str(config), "--json"],
        stdout=log_path.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
    )
    for _ in range(30):
        status = _run(
            [str(binary), "device-tunnel", "status", "--json"],
            log_path=log_path,
            timeout=15,
        )
        if status.returncode == 0:
            return process, config
        if process.poll() not in (None, 0):
            break
        time.sleep(2)
    raise ExpectedFailure(
        "provider_transport_failure",
        "BrowserStack Device Tunnel orchestrator did not become ready",
    )


def _connection_metadata_candidate(node: dict) -> dict[str, str] | None:
    session_id = _field(node, "session_id", "sessionId")
    build_id = _field(node, "build_id", "buildId")
    local_port = _field(node, "local_port", "localPort")
    if session_id is None or build_id is None or local_port is None:
        return None
    session_id = str(session_id)
    build_id = str(build_id)
    local_port = str(local_port)
    if not SAFE_ID.fullmatch(session_id) or not SAFE_ID.fullmatch(build_id):
        return None
    match = re.search(r"(?:localhost:)?([1-9][0-9]{1,4})\Z", local_port)
    if not match:
        return None
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        return None
    return {
        "providerSessionId": session_id,
        "providerBuildId": build_id,
        "localPort": str(port),
    }


def _extract_connection_metadata(payload: object, udid: str) -> dict[str, str]:
    exact: dict[tuple[str, str, str], dict[str, str]] = {}
    anonymous: dict[tuple[str, str, str], dict[str, str]] = {}
    conflicting = False

    def visit(value: object, inherited_device_id: str | None = None) -> None:
        nonlocal conflicting
        if isinstance(value, dict):
            raw_device_id = _field(value, "device_id", "deviceId", "udid")
            device_id = inherited_device_id
            if raw_device_id is not None and str(raw_device_id).strip():
                device_id = str(raw_device_id).strip()
            candidate = _connection_metadata_candidate(value)
            if candidate is not None:
                key = (
                    candidate["providerSessionId"],
                    candidate["providerBuildId"],
                    candidate["localPort"],
                )
                if device_id == udid:
                    exact[key] = candidate
                elif device_id is None:
                    anonymous[key] = candidate
                else:
                    conflicting = True
            for child in value.values():
                visit(child, device_id)
        elif isinstance(value, list):
            for child in value:
                visit(child, inherited_device_id)

    visit(payload)
    if len(exact) == 1:
        return next(iter(exact.values()))
    if len(exact) > 1:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack Device Tunnel returned ambiguous selected-device connection identity",
        )
    if conflicting:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack Device Tunnel connection identity belongs to a different device",
        )
    if len(anonymous) == 1:
        return next(iter(anonymous.values()))
    raise ExpectedFailure(
        "provider_transport_failure",
        "BrowserStack Device Tunnel returned incomplete or ambiguous connection identity",
    )


def connect_device(binary: Path, udid: str, log_path: Path) -> dict[str, str]:
    result = _run(
        [
            str(binary),
            "device-tunnel",
            "connect",
            "--device-id",
            udid,
            "--platform",
            "android",
            "--json",
        ],
        log_path=log_path,
        timeout=180,
    )
    if result.returncode != 0:
        raise ExpectedFailure(
            "provider_transport_failure",
            "BrowserStack Device Tunnel connection failed",
        )
    payload = _json_from_output(result.stdout)
    try:
        metadata = _extract_connection_metadata(payload, udid)
    except ExpectedFailure:
        for _ in range(30):
            tunnels = _run(
                [str(binary), "device-tunnel", "list-tunnels", "--json"],
                log_path=log_path,
                timeout=30,
            )
            if tunnels.returncode == 0:
                try:
                    metadata = _extract_connection_metadata(
                        _json_from_output(tunnels.stdout),
                        udid,
                    )
                    break
                except (ExpectedFailure, ValueError, json.JSONDecodeError):
                    pass
            time.sleep(2)
        else:
            raise
    return metadata


def _android_version_key(value: str) -> tuple[int, ...] | None:
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value) is None:
        return None
    parts = [int(part) for part in value.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _adb_model_matches(expected_model: str, observed_models: list[str]) -> bool:
    patterns = ADB_MODEL_PATTERNS.get(expected_model, ())
    return any(pattern.fullmatch(value) for pattern in patterns for value in observed_models)


def wait_for_adb(
    local_port: str,
    log_path: Path,
    expected_model: str,
    expected_os_version: str,
) -> str:
    adb = shutil.which("adb")
    if not adb:
        raise ExpectedFailure(
            "provider_transport_failure",
            "GitHub hosted Linux runner has no Android platform-tools adb",
        )
    serial = f"localhost:{local_port}"
    for _ in range(30):
        state = _run([adb, "-s", serial, "get-state"], log_path=log_path, timeout=15)
        if state.returncode == 0 and state.stdout.strip() == "device":
            qemu = _run(
                [adb, "-s", serial, "shell", "getprop", "ro.kernel.qemu"],
                log_path=log_path,
                timeout=15,
            )
            if qemu.returncode != 0 or qemu.stdout.strip() == "1":
                raise ExpectedFailure(
                    "provider_transport_failure",
                    "BrowserStack tunnel did not expose a real physical Android device",
                )
            observed_models = []
            for prop in ("ro.product.marketname", "ro.product.model"):
                model = _run(
                    [adb, "-s", serial, "shell", "getprop", prop],
                    log_path=log_path,
                    timeout=15,
                )
                if model.returncode == 0 and model.stdout.strip():
                    observed_models.append(model.stdout.strip())
            if not _adb_model_matches(expected_model, observed_models):
                raise ExpectedFailure(
                    "provider_transport_failure",
                    "BrowserStack adb endpoint model does not match the reviewed device class",
                )
            os_release = _run(
                [adb, "-s", serial, "shell", "getprop", "ro.build.version.release"],
                log_path=log_path,
                timeout=15,
            )
            expected_os = _android_version_key(expected_os_version)
            observed_os = _android_version_key(os_release.stdout.strip()) if os_release.returncode == 0 else None
            if expected_os is None or observed_os != expected_os:
                raise ExpectedFailure(
                    "provider_transport_failure",
                    "BrowserStack adb endpoint Android version does not match the reviewed device class",
                )
            return serial
        time.sleep(2)
    raise ExpectedFailure(
        "provider_transport_failure",
        "BrowserStack Device Tunnel never exposed a ready Android adb endpoint",
    )


def validate_source_and_wrapper(source_dir: Path, source_sha: str) -> Path:
    if not SOURCE_SHA.fullmatch(source_sha):
        raise ExpectedFailure("source_identity_invalid", "source SHA is invalid")
    head = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if head.returncode != 0 or head.stdout.strip() != source_sha:
        raise ExpectedFailure(
            "source_identity_invalid",
            "physical-performance source checkout does not match the observed SHA",
        )
    clean = subprocess.run(
        ["git", "-C", str(source_dir), "status", "--porcelain", "--untracked-files=all"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if clean.returncode != 0 or clean.stdout:
        raise ExpectedFailure(
            "source_identity_invalid",
            "physical-performance source checkout is not clean",
        )
    wrapper = source_dir / WRAPPER
    if wrapper.is_symlink() or not wrapper.is_file():
        raise ExpectedFailure(
            "product_wrapper_missing",
            "fixed Android physical-performance wrapper is unavailable",
        )
    tracked = subprocess.run(
        ["git", "-C", str(source_dir), "ls-files", "-s", "--", WRAPPER],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if tracked.returncode != 0 or not tracked.stdout.startswith("100755 "):
        raise ExpectedFailure(
            "product_wrapper_missing",
            "fixed Android physical-performance wrapper must be tracked and executable",
        )
    return wrapper


def _bounded_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ExpectedFailure("product_result_invalid", f"{label} is invalid")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_product_result(
    result_path: Path,
    evidence_root: Path,
    expected: dict[str, str],
) -> dict:
    if result_path.is_symlink() or not result_path.is_file():
        raise ExpectedFailure(
            "product_result_invalid",
            "Android physical-performance wrapper produced no regular result file",
        )
    if result_path.stat().st_size > MAX_RESULT_BYTES:
        raise ExpectedFailure(
            "product_result_invalid",
            "Android physical-performance result exceeds the bound",
        )
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpectedFailure(
            "product_result_invalid",
            "Android physical-performance result is not valid JSON",
        ) from exc
    expected_keys = {
        "schemaVersion",
        "sourceSha",
        "deviceClass",
        "deviceModel",
        "osVersion",
        "androidApiLevel",
        "provider",
        "providerBuildId",
        "providerSessionId",
        "buildMode",
        "artifactFile",
        "artifactSha256",
        "status",
        "measurements",
        "scenarios",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ExpectedFailure(
            "product_result_invalid",
            "Android physical-performance result schema is invalid",
        )
    if value["schemaVersion"] != 1:
        raise ExpectedFailure("product_result_invalid", "result schemaVersion is invalid")
    for key in (
        "sourceSha",
        "deviceClass",
        "deviceModel",
        "osVersion",
        "provider",
        "providerBuildId",
        "providerSessionId",
    ):
        if value[key] != expected[key]:
            raise ExpectedFailure(
                "product_result_invalid",
                f"Android physical-performance {key} does not match Central context",
            )
    api = value["androidApiLevel"]
    if isinstance(api, bool) or not isinstance(api, int) or not 24 <= api <= 100:
        raise ExpectedFailure("product_result_invalid", "androidApiLevel is invalid")
    if value["buildMode"] != "release":
        raise ExpectedFailure(
            "product_result_invalid",
            "Android physical-performance must measure a release artifact",
        )
    if value["status"] not in {"passed", "failed"}:
        raise ExpectedFailure("product_result_invalid", "result status is invalid")
    _bounded_id(value["providerBuildId"], "providerBuildId")
    _bounded_id(value["providerSessionId"], "providerSessionId")
    artifact_file = value["artifactFile"]
    if (
        not isinstance(artifact_file, str)
        or len(artifact_file) > 240
        or artifact_file.startswith("/")
        or ".." in Path(artifact_file).parts
    ):
        raise ExpectedFailure("product_result_invalid", "artifactFile is invalid")
    artifact = evidence_root / artifact_file
    if artifact.is_symlink() or not artifact.is_file():
        raise ExpectedFailure("product_result_invalid", "artifactFile is unavailable")
    resolved_root = evidence_root.resolve()
    resolved_artifact = artifact.resolve()
    if os.path.commonpath((str(resolved_root), str(resolved_artifact))) != str(resolved_root):
        raise ExpectedFailure("product_result_invalid", "artifactFile escapes evidence root")
    size = artifact.stat().st_size
    if not 1 <= size <= MAX_ARTIFACT_BYTES:
        raise ExpectedFailure("product_result_invalid", "artifact size is outside the bound")
    artifact_sha = value["artifactSha256"]
    if not isinstance(artifact_sha, str) or not ARTIFACT_SHA.fullmatch(artifact_sha):
        raise ExpectedFailure("product_result_invalid", "artifactSha256 is invalid")
    if _sha256(artifact) != artifact_sha:
        raise ExpectedFailure(
            "product_artifact_mismatch",
            "Android physical-performance release artifact SHA-256 mismatch",
        )
    measurements = value["measurements"]
    scenarios = value["scenarios"]
    if not isinstance(measurements, list) or not 1 <= len(measurements) <= MAX_MEASUREMENTS:
        raise ExpectedFailure("product_result_invalid", "measurements are outside the bound")
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= MAX_SCENARIOS:
        raise ExpectedFailure("product_result_invalid", "scenarios are outside the bound")
    normalized_measurements = []
    for item in measurements:
        if not isinstance(item, dict) or set(item) != {"id", "value", "unit", "status"}:
            raise ExpectedFailure("product_result_invalid", "measurement schema is invalid")
        identifier = item["id"]
        numeric = item["value"]
        unit = item["unit"]
        status_value = item["status"]
        if not isinstance(identifier, str) or not MEASUREMENT_ID.fullmatch(identifier):
            raise ExpectedFailure("product_result_invalid", "measurement id is invalid")
        if isinstance(numeric, bool) or not isinstance(numeric, (int, float)) or not math.isfinite(float(numeric)):
            raise ExpectedFailure("product_result_invalid", "measurement value is invalid")
        if unit not in ALLOWED_UNITS or status_value not in ALLOWED_MEASUREMENT_STATUS:
            raise ExpectedFailure("product_result_invalid", "measurement unit/status is invalid")
        normalized_measurements.append(
            {"id": identifier, "value": numeric, "unit": unit, "status": status_value}
        )
    normalized_scenarios = []
    for item in scenarios:
        if not isinstance(item, dict) or set(item) != {"id", "status"}:
            raise ExpectedFailure("product_result_invalid", "scenario schema is invalid")
        identifier = item["id"]
        status_value = item["status"]
        if not isinstance(identifier, str) or not MEASUREMENT_ID.fullmatch(identifier):
            raise ExpectedFailure("product_result_invalid", "scenario id is invalid")
        if status_value not in ALLOWED_SCENARIO_STATUS:
            raise ExpectedFailure("product_result_invalid", "scenario status is invalid")
        normalized_scenarios.append({"id": identifier, "status": status_value})
    failed = (
        value["status"] != "passed"
        or any(item["status"] == "failed" for item in normalized_measurements)
        or any(item["status"] == "failed" for item in normalized_scenarios)
    )
    return {
        "artifactSha256": artifact_sha,
        "artifactSize": size,
        "androidApiLevel": api,
        "measurements": normalized_measurements,
        "scenarios": normalized_scenarios,
        "measurementFailed": failed,
    }


def _append_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def _safe_provider_metadata(metadata: dict[str, str]) -> dict[str, str]:
    return {
        "providerBuildId": _bounded_id(metadata["providerBuildId"], "providerBuildId"),
        "providerSessionId": _bounded_id(metadata["providerSessionId"], "providerSessionId"),
    }


def _write_class_evidence(path: Path, payload: dict) -> None:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    if len(data.encode("utf-8")) > 512 * 1024:
        raise ExpectedFailure("product_result_invalid", "class evidence exceeds the bound")
    path.write_text(data, encoding="utf-8")
    path.chmod(0o600)


def run_cohort(
    *,
    source_dir: Path,
    source_sha: str,
    evidence_dir: Path,
    log_path: Path,
) -> tuple[bool, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.chmod(0o700)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    log_path.chmod(0o600)

    username = os.environ.get("BROWSERSTACK_USERNAME", "")
    access_key = os.environ.get("BROWSERSTACK_ACCESS_KEY", "")
    wrapper = None
    first_failure = ""
    class_results: list[dict] = []
    temp_root = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "central-browserstack-physical"
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, mode=0o700)

    try:
        try:
            wrapper = validate_source_and_wrapper(source_dir, source_sha)
        except ExpectedFailure as exc:
            first_failure = exc.code
            _write_private(log_path, exc.detail)
        if not first_failure and (not username or not access_key):
            first_failure = "provider_credentials_missing"
            _write_private(log_path, "BrowserStack username/access key are not configured.")
        devices: list[dict] = []
        binary: Path | None = None
        orchestrator = None
        config: Path | None = None
        if not first_failure:
            try:
                devices = fetch_dedicated_devices(username, access_key)
                binary = install_device_tunnel(temp_root, log_path)
                orchestrator, config = start_orchestrator(
                    binary,
                    temp_root,
                    username,
                    access_key,
                    log_path,
                )
            except ExpectedFailure as exc:
                first_failure = exc.code
                _write_private(log_path, exc.detail)
        try:
            if not first_failure and binary is not None and wrapper is not None:
                for spec in DEVICE_SPECS:
                    failure_code = ""
                    selected: dict | None = None
                    provider_meta: dict[str, str] | None = None
                    work = temp_root / f"work-{spec['deviceClass']}"
                    shutil.rmtree(work, ignore_errors=True)
                    work.mkdir(parents=True, mode=0o700)
                    result_path = work / "product-result.json"
                    product_evidence = work / "product-evidence"
                    product_evidence.mkdir(mode=0o700)
                    try:
                        selected = select_device(devices, spec)
                        print(f"::add-mask::{selected['udid']}")
                        provider_meta = connect_device(binary, selected["udid"], log_path)
                        serial = wait_for_adb(
                            provider_meta["localPort"],
                            log_path,
                            spec["deviceModel"],
                            spec["osVersion"],
                        )
                        api_result = _run(
                            ["adb", "-s", serial, "shell", "getprop", "ro.build.version.sdk"],
                            log_path=log_path,
                            timeout=15,
                        )
                        if (
                            api_result.returncode != 0
                            or re.fullmatch(r"[1-9][0-9]{0,2}", api_result.stdout.strip()) is None
                        ):
                            raise ExpectedFailure(
                                "provider_transport_failure",
                                "BrowserStack physical device Android API could not be verified",
                            )
                        api_level = api_result.stdout.strip()
                        expected_context = {
                            "sourceSha": source_sha,
                            "deviceClass": spec["deviceClass"],
                            "deviceModel": spec["deviceModel"],
                            "osVersion": spec["osVersion"],
                            "provider": PROVIDER,
                            **_safe_provider_metadata(provider_meta),
                        }
                        product_env = os.environ.copy()
                        product_env.pop("BROWSERSTACK_USERNAME", None)
                        product_env.pop("BROWSERSTACK_ACCESS_KEY", None)
                        product_env.update(
                            {
                                "ANDROID_SERIAL": serial,
                                "CI_ANDROID_PHYSICAL_PROFILE": "performance",
                                "CI_ANDROID_PHYSICAL_PROVIDER": PROVIDER,
                                "CI_ANDROID_PHYSICAL_DEVICE_CLASS": spec["deviceClass"],
                                "CI_ANDROID_PHYSICAL_DEVICE_MODEL": spec["deviceModel"],
                                "CI_ANDROID_PHYSICAL_OS_VERSION": spec["osVersion"],
                                "CI_ANDROID_PHYSICAL_ANDROID_API_LEVEL": api_level,
                                "CI_ANDROID_PHYSICAL_SOURCE_SHA": source_sha,
                                "CI_ANDROID_PHYSICAL_PROVIDER_BUILD_ID": provider_meta["providerBuildId"],
                                "CI_ANDROID_PHYSICAL_PROVIDER_SESSION_ID": provider_meta["providerSessionId"],
                                "CI_ANDROID_PHYSICAL_EVIDENCE_DIR": str(product_evidence),
                                "CI_ANDROID_PHYSICAL_RESULT_FILE": str(result_path),
                            }
                        )
                        product = _run(
                            [str(wrapper)],
                            log_path=log_path,
                            timeout=60 * 60,
                            env=product_env,
                        )
                        if product.returncode != 0:
                            raise ExpectedFailure(
                                "product_wrapper_failed",
                                f"Android physical-performance wrapper failed for {spec['deviceClass']}",
                            )
                        validated = validate_product_result(
                            result_path,
                            product_evidence,
                            expected_context,
                        )
                        if validated["measurementFailed"]:
                            raise ExpectedFailure(
                                "product_measurement_failed",
                                f"Android physical-performance measurements failed for {spec['deviceClass']}",
                            )
                        evidence = {
                            "schemaVersion": 1,
                            "provider": PROVIDER,
                            "sourceSha": source_sha,
                            "deviceClass": spec["deviceClass"],
                            "deviceModel": spec["deviceModel"],
                            "osVersion": spec["osVersion"],
                            "deviceIdentitySha256": selected["deviceIdentitySha256"],
                            "providerBuildId": provider_meta["providerBuildId"],
                            "providerSessionId": provider_meta["providerSessionId"],
                            "artifactSha256": validated["artifactSha256"],
                            "artifactSize": validated["artifactSize"],
                            "androidApiLevel": validated["androidApiLevel"],
                            "measurements": validated["measurements"],
                            "scenarios": validated["scenarios"],
                            "status": "passed",
                            "failureCode": "",
                        }
                    except ExpectedFailure as exc:
                        failure_code = exc.code
                        if not first_failure:
                            first_failure = failure_code
                        _write_private(log_path, exc.detail)
                        evidence = {
                            "schemaVersion": 1,
                            "provider": PROVIDER,
                            "sourceSha": source_sha,
                            "deviceClass": spec["deviceClass"],
                            "deviceModel": spec["deviceModel"],
                            "osVersion": spec["osVersion"],
                            "deviceIdentitySha256": (
                                selected["deviceIdentitySha256"] if selected else ""
                            ),
                            "providerBuildId": (
                                provider_meta["providerBuildId"] if provider_meta else ""
                            ),
                            "providerSessionId": (
                                provider_meta["providerSessionId"] if provider_meta else ""
                            ),
                            "artifactSha256": "",
                            "artifactSize": 0,
                            "androidApiLevel": 0,
                            "measurements": [],
                            "scenarios": [],
                            "status": "failed",
                            "failureCode": failure_code,
                        }
                    finally:
                        if binary is not None and selected is not None:
                            _run(
                                [
                                    str(binary),
                                    "device-tunnel",
                                    "stop",
                                    "--device-id",
                                    selected["udid"],
                                    "--json",
                                ],
                                log_path=log_path,
                                timeout=60,
                            )
                        shutil.rmtree(work, ignore_errors=True)
                    _write_class_evidence(
                        evidence_dir / f"{spec['deviceClass']}.json",
                        evidence,
                    )
                    class_results.append(evidence)
        finally:
            if binary is not None:
                _run(
                    [str(binary), "device-tunnel", "stop", "--json"],
                    log_path=log_path,
                    timeout=60,
                )
            if orchestrator is not None and orchestrator.poll() is None:
                orchestrator.terminate()
                try:
                    orchestrator.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    orchestrator.kill()
            if config is not None:
                config.unlink(missing_ok=True)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    if first_failure and not class_results:
        for spec in DEVICE_SPECS:
            evidence = {
                "schemaVersion": 1,
                "provider": PROVIDER,
                "sourceSha": source_sha,
                "deviceClass": spec["deviceClass"],
                "deviceModel": spec["deviceModel"],
                "osVersion": spec["osVersion"],
                "deviceIdentitySha256": "",
                "providerBuildId": "",
                "providerSessionId": "",
                "artifactSha256": "",
                "artifactSize": 0,
                "androidApiLevel": 0,
                "measurements": [],
                "scenarios": [],
                "status": "failed",
                "failureCode": first_failure,
            }
            _write_class_evidence(
                evidence_dir / f"{spec['deviceClass']}.json",
                evidence,
            )
            class_results.append(evidence)

    success = bool(class_results) and all(item["status"] == "passed" for item in class_results)
    summary = {
        "schemaVersion": 1,
        "provider": PROVIDER,
        "providerBinaryVersion": DEVICE_TUNNEL_VERSION,
        "sourceSha": source_sha,
        "status": "passed" if success else "failed",
        "failureCode": "" if success else (first_failure or "product_measurement_failed"),
        "devices": class_results,
    }
    _write_class_evidence(evidence_dir / "summary.json", summary)
    _append_output("success", "true" if success else "false")
    _append_output("failure_code", summary["failureCode"])
    _append_output("summary_path", str(evidence_dir / "summary.json"))
    return success, summary["failureCode"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--private-log", required=True)
    args = parser.parse_args()
    source_dir = Path(args.source_dir)
    evidence_dir = Path(args.evidence_dir)
    log_path = Path(args.private_log)
    try:
        run_cohort(
            source_dir=source_dir,
            source_sha=args.source_sha,
            evidence_dir=evidence_dir,
            log_path=log_path,
        )
    except Exception as exc:  # fail closed but preserve terminal evidence path
        evidence_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "schemaVersion": 1,
            "provider": PROVIDER,
            "providerBinaryVersion": DEVICE_TUNNEL_VERSION,
            "sourceSha": args.source_sha,
            "status": "failed",
            "failureCode": "provider_transport_failure",
            "devices": [],
        }
        _write_class_evidence(evidence_dir / "summary.json", summary)
        _write_private(log_path, f"BrowserStack physical transport failed closed: {type(exc).__name__}")
        _append_output("success", "false")
        _append_output("failure_code", "provider_transport_failure")
        _append_output("summary_path", str(evidence_dir / "summary.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
