#!/usr/bin/env python3
"""Bounded owner-attached physical Android performance transport for Central CI."""

from __future__ import annotations

import argparse
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

PROVIDER = "owner-attached"
WRAPPER = "scripts/ci/run-android-physical-performance.sh"
MAX_RESULT_BYTES = 256 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_MEASUREMENTS = 128
MAX_SCENARIOS = 64
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,159}\Z")
MEASUREMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}\Z")
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")
ARTIFACT_SHA = re.compile(r"[0-9a-f]{64}\Z")
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


def _adb_path() -> str:
    adb = shutil.which("adb")
    if adb:
        return adb
    for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        root = os.environ.get(name, "")
        if root:
            candidate = Path(root) / "platform-tools" / "adb"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    raise ExpectedFailure(
        "owner_device_host_unconfigured",
        "registered physical-device runner has no usable adb; configure Android platform-tools on the trusted host",
    )


def _adb_prop(adb: str, serial: str, prop: str, log_path: Path) -> str:
    result = _run(
        [adb, "-s", serial, "shell", "getprop", prop],
        log_path=log_path,
        timeout=15,
    )
    value = result.stdout.strip().replace("\r", "")
    if result.returncode != 0:
        raise ExpectedFailure(
            "owner_device_unavailable",
            f"attached Android device could not report {prop}",
        )
    return value


def _parse_devices(output: str) -> tuple[list[str], list[tuple[str, str]]]:
    ready: list[str] = []
    nonready: list[tuple[str, str]] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices attached"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        serial, state = fields[0], fields[1]
        if state == "device":
            ready.append(serial)
        else:
            nonready.append((serial, state))
    return ready, nonready


def _device_class(adb: str, serial: str, log_path: Path) -> str:
    characteristics = _adb_prop(adb, serial, "ro.build.characteristics", log_path).lower()
    if "tv" in characteristics:
        return "television"

    size = _run([adb, "-s", serial, "shell", "wm", "size"], log_path=log_path, timeout=15)
    density = _run([adb, "-s", serial, "shell", "wm", "density"], log_path=log_path, timeout=15)
    if size.returncode == 0 and density.returncode == 0:
        size_match = re.findall(r"(\d+)x(\d+)", size.stdout)
        density_match = re.findall(r"(\d+)", density.stdout)
        if size_match and density_match:
            width, height = (int(value) for value in size_match[-1])
            dpi = int(density_match[-1])
            if dpi > 0:
                smallest_dp = min(width, height) * 160.0 / dpi
                if smallest_dp >= 600:
                    return "tablet"
    return "phone"


def discover_owner_device(log_path: Path) -> dict[str, object]:
    adb = _adb_path()
    listing = _run([adb, "devices", "-l"], log_path=log_path, timeout=20)
    if listing.returncode != 0:
        raise ExpectedFailure(
            "owner_device_host_unconfigured",
            "registered physical-device runner could not query adb devices",
        )
    ready, nonready = _parse_devices(listing.stdout)
    if not ready:
        detail = "owner physical Android device is not attached/authorized"
        if nonready:
            states = ",".join(sorted({state for _, state in nonready}))
            detail += f"; observed non-ready adb state(s): {states}"
        raise ExpectedFailure("owner_device_unavailable", detail)
    if len(ready) != 1:
        raise ExpectedFailure(
            "owner_device_ambiguous",
            "physical-device profile requires exactly one authorized Android device on the registered host",
        )

    serial = ready[0]
    if serial.startswith("emulator-"):
        raise ExpectedFailure(
            "owner_device_not_physical",
            "physical-performance profile rejects Android emulators",
        )
    qemu = _adb_prop(adb, serial, "ro.kernel.qemu", log_path)
    if qemu == "1":
        raise ExpectedFailure(
            "owner_device_not_physical",
            "physical-performance profile rejects emulated Android devices",
        )

    model = (
        _adb_prop(adb, serial, "ro.product.marketname", log_path)
        or _adb_prop(adb, serial, "ro.product.model", log_path)
    ).strip()
    os_version = _adb_prop(adb, serial, "ro.build.version.release", log_path).strip()
    api_text = _adb_prop(adb, serial, "ro.build.version.sdk", log_path).strip()
    if not model or not os_version or re.fullmatch(r"[1-9][0-9]{0,2}", api_text) is None:
        raise ExpectedFailure(
            "owner_device_identity_invalid",
            "attached physical Android device identity is incomplete",
        )

    device_class = _device_class(adb, serial, log_path)
    run_id = os.environ.get("GITHUB_RUN_ID", "manual")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    provider_build_id = f"github-{run_id}-{run_attempt}"
    provider_session_id = "device-" + hashlib.sha256(serial.encode()).hexdigest()[:32]
    return {
        "adb": adb,
        "serial": serial,
        "deviceClass": device_class,
        "deviceModel": model,
        "osVersion": os_version,
        "androidApiLevel": int(api_text),
        "providerBuildId": provider_build_id,
        "providerSessionId": provider_session_id,
    }


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
            "physical-performance source checkout does not match observed SHA",
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
        raise ExpectedFailure("product_wrapper_missing", "fixed product wrapper is unavailable")
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
            "fixed product wrapper must be tracked and executable",
        )
    if not (wrapper.stat().st_mode & stat.S_IXUSR):
        raise ExpectedFailure("product_wrapper_missing", "product wrapper is not executable")
    return wrapper


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ExpectedFailure("product_result_invalid", f"{label} is invalid")
    return value


def validate_product_result(
    result_path: Path,
    evidence_root: Path,
    expected: dict[str, object],
) -> dict[str, object]:
    if result_path.is_symlink() or not result_path.is_file():
        raise ExpectedFailure("product_result_invalid", "product wrapper produced no result file")
    if result_path.stat().st_size > MAX_RESULT_BYTES:
        raise ExpectedFailure("product_result_invalid", "product result exceeds the bound")
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpectedFailure("product_result_invalid", "product result is not valid JSON") from exc

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
        raise ExpectedFailure("product_result_invalid", "product result schema is invalid")
    if value["schemaVersion"] != 1 or value["buildMode"] != "release":
        raise ExpectedFailure("product_result_invalid", "product result mode/schema is invalid")
    for key in (
        "sourceSha",
        "deviceClass",
        "deviceModel",
        "osVersion",
        "androidApiLevel",
        "provider",
        "providerBuildId",
        "providerSessionId",
    ):
        if value[key] != expected[key]:
            raise ExpectedFailure("product_result_invalid", f"{key} does not match Central context")
    _bounded_id(value["providerBuildId"], "providerBuildId")
    _bounded_id(value["providerSessionId"], "providerSessionId")
    if value["status"] not in {"passed", "failed"}:
        raise ExpectedFailure("product_result_invalid", "product status is invalid")

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
    if os.path.commonpath((str(evidence_root.resolve()), str(artifact.resolve()))) != str(
        evidence_root.resolve()
    ):
        raise ExpectedFailure("product_result_invalid", "artifactFile escapes evidence root")
    size = artifact.stat().st_size
    if not 1 <= size <= MAX_ARTIFACT_BYTES:
        raise ExpectedFailure("product_result_invalid", "artifact size is outside the bound")
    artifact_sha = value["artifactSha256"]
    if not isinstance(artifact_sha, str) or not ARTIFACT_SHA.fullmatch(artifact_sha):
        raise ExpectedFailure("product_result_invalid", "artifactSha256 is invalid")
    if _sha256(artifact) != artifact_sha:
        raise ExpectedFailure("product_artifact_mismatch", "release artifact SHA-256 mismatch")

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
        if not isinstance(identifier, str) or not MEASUREMENT_ID.fullmatch(identifier):
            raise ExpectedFailure("product_result_invalid", "measurement id is invalid")
        if isinstance(numeric, bool) or not isinstance(numeric, (int, float)) or not math.isfinite(float(numeric)):
            raise ExpectedFailure("product_result_invalid", "measurement value is invalid")
        if item["unit"] not in ALLOWED_UNITS or item["status"] not in ALLOWED_MEASUREMENT_STATUS:
            raise ExpectedFailure("product_result_invalid", "measurement unit/status is invalid")
        normalized_measurements.append(item)

    normalized_scenarios = []
    for item in scenarios:
        if not isinstance(item, dict) or set(item) != {"id", "status"}:
            raise ExpectedFailure("product_result_invalid", "scenario schema is invalid")
        if not isinstance(item["id"], str) or not MEASUREMENT_ID.fullmatch(item["id"]):
            raise ExpectedFailure("product_result_invalid", "scenario id is invalid")
        if item["status"] not in ALLOWED_SCENARIO_STATUS:
            raise ExpectedFailure("product_result_invalid", "scenario status is invalid")
        normalized_scenarios.append(item)

    failed = (
        value["status"] != "passed"
        or any(item["status"] == "failed" for item in normalized_measurements)
        or any(item["status"] == "failed" for item in normalized_scenarios)
    )
    return {
        "artifactSha256": artifact_sha,
        "artifactSize": size,
        "measurements": normalized_measurements,
        "scenarios": normalized_scenarios,
        "measurementFailed": failed,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    if len(data.encode("utf-8")) > 512 * 1024:
        raise ExpectedFailure("product_result_invalid", "physical evidence exceeds bound")
    path.write_text(data, encoding="utf-8")
    path.chmod(0o600)


def _append_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def run_profile(
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

    failure_code = ""
    device_evidence: dict[str, object] | None = None
    temp_root = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "central-owner-attached-physical"
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, mode=0o700)

    try:
        try:
            wrapper = validate_source_and_wrapper(source_dir, source_sha)
            device = discover_owner_device(log_path)
            work = temp_root / "work"
            work.mkdir(mode=0o700)
            result_path = work / "product-result.json"
            product_evidence = work / "product-evidence"
            product_evidence.mkdir(mode=0o700)

            expected = {
                "sourceSha": source_sha,
                "deviceClass": device["deviceClass"],
                "deviceModel": device["deviceModel"],
                "osVersion": device["osVersion"],
                "androidApiLevel": device["androidApiLevel"],
                "provider": PROVIDER,
                "providerBuildId": device["providerBuildId"],
                "providerSessionId": device["providerSessionId"],
            }
            product_env = os.environ.copy()
            product_env.pop("CI_ANDROID_PHYSICAL_PROVIDER", None)
            product_env.update(
                {
                    "ANDROID_SERIAL": str(device["serial"]),
                    "CI_ANDROID_PHYSICAL_PROFILE": "performance",
                    "CI_ANDROID_PHYSICAL_DEVICE_CLASS": str(device["deviceClass"]),
                    "CI_ANDROID_PHYSICAL_DEVICE_MODEL": str(device["deviceModel"]),
                    "CI_ANDROID_PHYSICAL_OS_VERSION": str(device["osVersion"]),
                    "CI_ANDROID_PHYSICAL_ANDROID_API_LEVEL": str(device["androidApiLevel"]),
                    "CI_ANDROID_PHYSICAL_SOURCE_SHA": source_sha,
                    "CI_ANDROID_PHYSICAL_PROVIDER_BUILD_ID": str(device["providerBuildId"]),
                    "CI_ANDROID_PHYSICAL_PROVIDER_SESSION_ID": str(device["providerSessionId"]),
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
                    "Android physical-performance product wrapper failed",
                )
            validated = validate_product_result(result_path, product_evidence, expected)
            if validated["measurementFailed"]:
                raise ExpectedFailure(
                    "product_measurement_failed",
                    "Android physical-performance measurements reported failure",
                )
            device_evidence = {
                "schemaVersion": 1,
                "provider": PROVIDER,
                "sourceSha": source_sha,
                "deviceClass": device["deviceClass"],
                "deviceModel": device["deviceModel"],
                "osVersion": device["osVersion"],
                "androidApiLevel": device["androidApiLevel"],
                "providerBuildId": device["providerBuildId"],
                "providerSessionId": device["providerSessionId"],
                "artifactSha256": validated["artifactSha256"],
                "artifactSize": validated["artifactSize"],
                "measurements": validated["measurements"],
                "scenarios": validated["scenarios"],
                "status": "passed",
                "failureCode": "",
            }
        except ExpectedFailure as exc:
            failure_code = exc.code
            _write_private(log_path, exc.detail)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    if device_evidence is not None:
        _write_json(evidence_dir / f"{device_evidence['deviceClass']}.json", device_evidence)
    summary = {
        "schemaVersion": 1,
        "provider": PROVIDER,
        "sourceSha": source_sha,
        "status": "passed" if device_evidence is not None and not failure_code else "failed",
        "failureCode": failure_code,
        "devices": [device_evidence] if device_evidence is not None else [],
    }
    _write_json(evidence_dir / "summary.json", summary)
    success = summary["status"] == "passed"
    _append_output("success", "true" if success else "false")
    _append_output("failure_code", failure_code)
    _append_output("summary_path", str(evidence_dir / "summary.json"))
    return bool(success), failure_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--private-log", required=True)
    args = parser.parse_args()
    try:
        run_profile(
            source_dir=Path(args.source_dir),
            source_sha=args.source_sha,
            evidence_dir=Path(args.evidence_dir),
            log_path=Path(args.private_log),
        )
    except Exception as exc:
        evidence_dir = Path(args.evidence_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            evidence_dir / "summary.json",
            {
                "schemaVersion": 1,
                "provider": PROVIDER,
                "sourceSha": args.source_sha,
                "status": "failed",
                "failureCode": "owner_device_transport_failure",
                "devices": [],
            },
        )
        _write_private(
            Path(args.private_log),
            f"Owner-attached physical transport failed closed: {type(exc).__name__}",
        )
        _append_output("success", "false")
        _append_output("failure_code", "owner_device_transport_failure")
        _append_output("summary_path", str(evidence_dir / "summary.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
