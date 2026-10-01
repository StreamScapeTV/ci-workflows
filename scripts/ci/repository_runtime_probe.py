#!/usr/bin/env python3
"""Record bounded Central-owned runtime diagnostics for macOS repository UI execution.

The probe intentionally never records process argv or environment values. It captures only
fixed host/runtime metadata that can distinguish simulator, app-process, and VM-service
launch boundaries while repository-owned test output is otherwise silent.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

SCHEMA_VERSION = 1
INTERVAL_SECONDS = 60
COMMAND_TIMEOUT_SECONDS = 5
MAX_SAMPLES = 512
MAX_LINE_BYTES = 8 * 1024
MAX_PROBE_BYTES = 4 * 1024 * 1024
MAX_PROCESS_ROWS = 32
MAX_APP_IDS = 16
MAX_LISTENERS = 16
MAX_ERRORS = 8
MAX_SUMMARY_SAMPLES = 6
MAX_VM_SERVICE_LOG_PROBES = 4

_TOOL_NAMES = {
    "dart",
    "flutter",
    "xcodebuild",
    "simctl",
    "ios-deploy",
}
_BUNDLE_KEY = re.compile(
    r'^\s*(?:"([A-Za-z0-9_.-]+)"|([A-Za-z0-9_.-]+))\s*=\s*\{\s*$',
)
_BUNDLE_EXECUTABLE = re.compile(
    r'^\s*CFBundleExecutable\s*=\s*(?:"([^"]+)"|([^;]+))\s*;\s*$'
)
_VM_SERVICE_PUBLICATION = re.compile(
    r'(?:The )?(?:Dart VM service is listening on|Observatory listening on)\s+'
    r'https?://(?:127\.0\.0\.1|localhost|\[::1\]):([0-9]{1,5})(?:/|$)',
    re.IGNORECASE,
)
_ENDPOINT_PORT = re.compile(r':([0-9]{1,5})$')


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


class CommandRunner(Protocol):
    def __call__(self, argv: Sequence[str]) -> CommandResult: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_runner(argv: Sequence[str]) -> CommandResult:
    try:
        result = subprocess.run(
            list(argv),
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(127, "", type(exc).__name__)
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _safe_text(value: object, limit: int = 128) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text[:limit]


def _process_rows(raw: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in raw.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) != 5:
            continue
        pid_text, ppid_text, state, elapsed, command = parts
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
        except ValueError:
            continue
        basename = os.path.basename(command.rstrip("/")) or command
        simulator_app = (
            "/CoreSimulator/Devices/" in command
            and "/data/Containers/Bundle/Application/" in command
        )
        if basename not in _TOOL_NAMES and not simulator_app:
            continue
        rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "state": _safe_text(state, 16),
                "elapsed": _safe_text(elapsed, 32),
                "comm": _safe_text(basename, 128),
                "simulator_app": simulator_app,
            }
        )
        if len(rows) >= MAX_PROCESS_ROWS:
            break
    return rows


def _booted_simulators(raw: str) -> list[dict[str, str]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    devices = value.get("devices") if isinstance(value, dict) else None
    if not isinstance(devices, dict):
        return []
    output: list[dict[str, str]] = []
    for runtime_devices in devices.values():
        if not isinstance(runtime_devices, list):
            continue
        for device in runtime_devices:
            if not isinstance(device, dict) or device.get("state") != "Booted":
                continue
            name = device.get("name")
            udid = device.get("udid")
            if isinstance(name, str) and isinstance(udid, str):
                output.append({"name": _safe_text(name), "udid": _safe_text(udid, 64)})
    return output[:8]


def _third_party_bundle_info(raw: str) -> dict[str, str]:
    candidates: dict[str, str] = {}
    current_bundle: str | None = None
    current_indent = -1
    current_executable = ""
    for line in raw.splitlines():
        bundle_match = _BUNDLE_KEY.match(line)
        if current_bundle is None and bundle_match:
            value = bundle_match.group(1) or bundle_match.group(2)
            if "." in value and not value.startswith("com.apple."):
                current_bundle = _safe_text(value, 160)
                current_indent = len(line) - len(line.lstrip())
                current_executable = ""
            continue
        if current_bundle is None:
            continue
        executable_match = _BUNDLE_EXECUTABLE.match(line)
        if executable_match:
            current_executable = _safe_text(
                (executable_match.group(1) or executable_match.group(2)).strip(),
                128,
            )
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "};" and indent == current_indent:
            candidates[current_bundle] = current_executable
            if len(candidates) >= MAX_APP_IDS:
                break
            current_bundle = None
            current_indent = -1
            current_executable = ""
    return candidates


def _simulator_app_rows(raw: str, executable_names: set[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not executable_names:
        return rows
    for line in raw.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) != 5:
            continue
        pid_text, ppid_text, state, elapsed, command = parts
        basename = os.path.basename(command.rstrip("/")) or command
        if basename not in executable_names:
            continue
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "state": _safe_text(state, 16),
                "elapsed": _safe_text(elapsed, 32),
                "comm": _safe_text(basename, 128),
                "simulator_app": True,
            }
        )
        if len(rows) >= MAX_PROCESS_ROWS:
            break
    return rows


def _vm_service_log_command(udid: str, pid: int) -> tuple[str, ...]:
    predicate = (
        f"eventType = logEvent AND processIdentifier == {pid} AND "
        '(eventMessage CONTAINS[c] "Dart VM Service is listening on" OR '
        'eventMessage CONTAINS[c] "Observatory listening on")'
    )
    return (
        "xcrun",
        "simctl",
        "spawn",
        udid,
        "log",
        "show",
        "--last",
        "2m",
        "--style",
        "compact",
        "--predicate",
        predicate,
    )


def _vm_service_publication_ports(raw: str) -> list[int]:
    ports: set[int] = set()
    for match in _VM_SERVICE_PUBLICATION.finditer(raw):
        port = int(match.group(1))
        if 1 <= port <= 65535:
            ports.add(port)
        if len(ports) >= MAX_LISTENERS:
            break
    return sorted(ports)


def _listener_ports(rows: list[dict[str, object]]) -> set[int]:
    ports: set[int] = set()
    for row in rows:
        endpoint = str(row.get("endpoint", ""))
        match = _ENDPOINT_PORT.search(endpoint)
        if match is None:
            continue
        port = int(match.group(1))
        if 1 <= port <= 65535:
            ports.add(port)
    return ports


def _listener_rows(raw: str, relevant_pids: set[int]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        if pid not in relevant_pids:
            continue
        endpoint = parts[-2] if parts[-1] == "(LISTEN)" and len(parts) >= 10 else parts[-1]
        rows.append(
            {
                "pid": pid,
                "comm": _safe_text(parts[0], 64),
                "endpoint": _safe_text(endpoint, 160),
            }
        )
        if len(rows) >= MAX_LISTENERS:
            break
    return rows


def collect_sample(
    *,
    sequence: int,
    runner: CommandRunner = _default_runner,
    observed_at: str | None = None,
) -> dict[str, object]:
    errors: list[str] = []

    ps_result = runner(("ps", "-axo", "pid=,ppid=,state=,etime=,comm="))
    if ps_result.returncode == 0:
        processes = _process_rows(ps_result.stdout)
    else:
        processes = []
        errors.append("ps_unavailable")

    sim_result = runner(("xcrun", "simctl", "list", "devices", "booted", "-j"))
    if sim_result.returncode == 0:
        simulators = _booted_simulators(sim_result.stdout)
    else:
        simulators = []
        errors.append("simctl_devices_unavailable")

    app_inventory_observed = False
    third_party_apps: dict[str, list[str]] = {}
    simulator_processes: list[dict[str, object]] = []
    simulator_processes_by_udid: dict[str, list[dict[str, object]]] = {}
    for simulator in simulators:
        udid = simulator["udid"]
        result = runner(("xcrun", "simctl", "listapps", udid))
        if result.returncode != 0:
            errors.append("simctl_listapps_unavailable")
            continue
        app_inventory_observed = True
        bundle_info = _third_party_bundle_info(result.stdout)
        third_party_apps[udid] = list(bundle_info)
        executable_names = {value for value in bundle_info.values() if value}
        process_result = runner(
            ("xcrun", "simctl", "spawn", udid, "ps", "-axo", "pid=,ppid=,state=,etime=,comm=")
        )
        if process_result.returncode == 0:
            app_rows = _simulator_app_rows(process_result.stdout, executable_names)
            simulator_processes.extend(app_rows)
            simulator_processes_by_udid[udid] = app_rows
        else:
            errors.append("simulator_process_inventory_unavailable")

    relevant_pids = {int(row["pid"]) for row in [*processes, *simulator_processes]}
    lsof_result = runner(("lsof", "-nP", "-iTCP", "-sTCP:LISTEN"))
    if lsof_result.returncode == 0:
        listeners = _listener_rows(lsof_result.stdout, relevant_pids)
        listener_inventory_observed = True
    else:
        listeners = []
        listener_inventory_observed = False
        errors.append("listener_inventory_unavailable")

    tool_processes = sorted(
        {
            str(row["comm"])
            for row in processes
            if not bool(row.get("simulator_app"))
        }
    )

    vm_service_log_inventory_observed = False
    vm_service_publication_ports: set[int] = set()
    if {"flutter", "dart"}.intersection(tool_processes):
        probes = 0
        for simulator in simulators:
            udid = simulator["udid"]
            for row in simulator_processes_by_udid.get(udid, []):
                if probes >= MAX_VM_SERVICE_LOG_PROBES:
                    break
                probes += 1
                log_result = runner(_vm_service_log_command(udid, int(row["pid"])))
                if log_result.returncode == 0:
                    vm_service_log_inventory_observed = True
                    vm_service_publication_ports.update(
                        _vm_service_publication_ports(log_result.stdout)
                    )
                else:
                    errors.append("vm_service_log_unavailable")
            if probes >= MAX_VM_SERVICE_LOG_PROBES:
                break

    vm_service_ports = sorted(vm_service_publication_ports)[:MAX_LISTENERS]
    vm_service_listener_match = bool(
        set(vm_service_ports).intersection(_listener_ports(listeners))
    )

    simulator_app_processes = sorted(
        {
            str(row["comm"])
            for row in [*processes, *simulator_processes]
            if bool(row.get("simulator_app"))
        }
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "sequence": sequence,
        "observed_at": observed_at or _now(),
        "host_os": "macos",
        "simulators": simulators,
        "app_inventory_observed": app_inventory_observed,
        "third_party_apps": third_party_apps,
        "tool_processes": tool_processes,
        "simulator_app_processes": simulator_app_processes,
        "listener_inventory_observed": listener_inventory_observed,
        "listeners": listeners,
        "vm_service_log_inventory_observed": vm_service_log_inventory_observed,
        "vm_service_publication_ports": vm_service_ports,
        "vm_service_listener_match": vm_service_listener_match,
        "errors": sorted(set(errors))[:MAX_ERRORS],
    }


def _encode_sample(sample: dict[str, object]) -> bytes:
    raw = (
        "CENTRAL runtime-probe "
        + json.dumps(sample, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(raw) > MAX_LINE_BYTES:
        raise ProbeError("runtime_probe_sample_too_large")
    return raw


def _validate_output(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ProbeError("invalid_runtime_probe_path")


def append_sample(path: Path, sample: dict[str, object]) -> None:
    _validate_output(path)
    raw = _encode_sample(sample)
    if path.stat().st_size + len(raw) > MAX_PROBE_BYTES:
        raise ProbeError("runtime_probe_size_bound")
    with path.open("ab", buffering=0) as handle:
        handle.write(raw)


def run_loop(
    *,
    output_path: Path,
    runner: CommandRunner = _default_runner,
    interval_seconds: float = INTERVAL_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    stop_requested: Callable[[], bool] | None = None,
) -> int:
    if interval_seconds <= 0:
        raise ProbeError("invalid_probe_interval")
    _validate_output(output_path)
    requested = stop_requested or (lambda: False)
    sequence = 1
    while sequence <= MAX_SAMPLES and not requested():
        append_sample(output_path, collect_sample(sequence=sequence, runner=runner))
        sequence += 1
        if requested():
            break
        sleep_fn(interval_seconds)
    return sequence - 1


def _load_samples(path: Path) -> list[dict[str, object]]:
    _validate_output(path)
    if path.stat().st_size > MAX_PROBE_BYTES:
        raise ProbeError("runtime_probe_size_bound")
    samples: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("CENTRAL runtime-probe "):
            continue
        try:
            value = json.loads(line[len("CENTRAL runtime-probe ") :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("schema_version") == SCHEMA_VERSION:
            samples.append(value)
    return samples


def summary_lines(path: Path) -> list[str]:
    samples = _load_samples(path)
    if not samples:
        return ["CENTRAL runtime-probe summary samples=0"]

    simulator_seen = any(bool(sample.get("simulators")) for sample in samples)
    inventory_seen = any(sample.get("app_inventory_observed") is True for sample in samples)
    third_party_seen = any(
        any(bool(ids) for ids in (sample.get("third_party_apps") or {}).values())
        for sample in samples
        if isinstance(sample.get("third_party_apps"), dict)
    )
    app_process_seen = any(bool(sample.get("simulator_app_processes")) for sample in samples)
    listener_inventory_seen = any(
        sample.get("listener_inventory_observed") is True for sample in samples
    )
    listener_seen = any(bool(sample.get("listeners")) for sample in samples)
    xcodebuild_seen = any(
        "xcodebuild" in (sample.get("tool_processes") or []) for sample in samples
    )
    flutter_seen = any(
        "flutter" in (sample.get("tool_processes") or []) for sample in samples
    )
    vm_service_log_inventory_seen = any(
        sample.get("vm_service_log_inventory_observed") is True for sample in samples
    )
    vm_service_publication_seen = any(
        bool(sample.get("vm_service_publication_ports")) for sample in samples
    )
    vm_service_listener_match_seen = any(
        sample.get("vm_service_listener_match") is True for sample in samples
    )

    lines = [
        "CENTRAL runtime-probe summary "
        f"samples={len(samples)} simulator_booted={int(simulator_seen)} "
        f"app_inventory_observed={int(inventory_seen)} third_party_app_seen={int(third_party_seen)} "
        f"simulator_app_process_seen={int(app_process_seen)} "
        f"listener_inventory_observed={int(listener_inventory_seen)} "
        f"candidate_listener_seen={int(listener_seen)} "
        f"xcodebuild_seen={int(xcodebuild_seen)} flutter_seen={int(flutter_seen)} "
        f"vm_service_log_inventory_observed={int(vm_service_log_inventory_seen)} "
        f"vm_service_publication_seen={int(vm_service_publication_seen)} "
        f"vm_service_listener_match={int(vm_service_listener_match_seen)}"
    ]
    for sample in samples[-MAX_SUMMARY_SAMPLES:]:
        compact = json.dumps(sample, sort_keys=True, separators=(",", ":"))
        raw = f"CENTRAL runtime-probe final-sample {compact}"
        if len(raw.encode("utf-8")) > MAX_LINE_BYTES:
            raise ProbeError("runtime_probe_summary_too_large")
        lines.append(raw)
    return lines


def append_summary(probe_path: Path, log_path: Path) -> None:
    _validate_output(probe_path)
    if log_path.is_symlink() or not log_path.is_file():
        raise ProbeError("invalid_log_path")
    payload = ("\n".join(summary_lines(probe_path)) + "\n").encode("utf-8")
    if len(payload) > 64 * 1024:
        raise ProbeError("runtime_probe_summary_too_large")
    with log_path.open("ab", buffering=0) as handle:
        handle.write(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("--output-path", required=True)
    run.add_argument("--interval-seconds", type=float, default=INTERVAL_SECONDS)

    summary = sub.add_parser("summary")
    summary.add_argument("--probe-path", required=True)
    summary.add_argument("--log-path", required=True)

    args = parser.parse_args()
    try:
        if args.command == "summary":
            append_summary(Path(args.probe_path), Path(args.log_path))
            return 0

        stop_event = threading.Event()

        def request_stop(_signum: int, _frame: object) -> None:
            stop_event.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        run_loop(
            output_path=Path(args.output_path),
            interval_seconds=args.interval_seconds,
            stop_requested=stop_event.is_set,
            sleep_fn=stop_event.wait,
        )
        return 0
    except (ProbeError, OSError) as exc:
        print(f"repository runtime probe failed: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
