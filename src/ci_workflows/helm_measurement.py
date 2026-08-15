"""Bounded peak resource evidence for trusted Helm publication jobs."""
from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from . import runners
from .helm_contract import FULL_SHA, NAME, require
from .helm_types import HelmValidationError


STATE_NAME = "ciw-helm-publication-measurement"
RECORD_NAME = "record.json"
CONFIG_NAME = "config.json"
PID_NAME = "monitor.pid"
STOP_NAME = "stop"
DONE_NAME = "done"
ERROR_NAME = "error"
SAMPLE_SECONDS = 0.2


def _json(path: Path, code: str) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError(code) from error
    require(isinstance(data, Mapping), code)
    return data


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _allocated_bytes(root: Path) -> int:
    """Count allocated bytes below one root without following symlinks."""
    total = 0
    stack = [root]
    seen: set[tuple[int, int]] = set()
    while stack:
        candidate = stack.pop()
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        key = (metadata.st_dev, metadata.st_ino)
        if key in seen:
            continue
        seen.add(key)
        blocks = getattr(metadata, "st_blocks", 0)
        total += blocks * 512 if blocks else metadata.st_size
        if stat.S_ISDIR(metadata.st_mode):
            try:
                stack.extend(candidate.iterdir())
            except FileNotFoundError:
                continue
    return total


def _read_positive_integer(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _memory_bytes() -> int:
    candidates = (
        Path("/sys/fs/cgroup/memory.peak"),
        Path("/sys/fs/cgroup/memory.current"),
        Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes"),
        Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    )
    values = [value for path in candidates if (value := _read_positive_integer(path))]
    require(bool(values), "runner_measurement_unavailable")
    return max(values)


def _roots(environment: Mapping[str, str]) -> tuple[Path, Path, Path]:
    try:
        workspace = Path(environment["GITHUB_WORKSPACE"]).resolve(strict=True)
        runner_temp = Path(environment["RUNNER_TEMP"]).resolve(strict=True)
    except (KeyError, OSError) as error:
        raise HelmValidationError("runner_measurement_invalid") from error
    require(workspace.is_dir() and runner_temp.is_dir(), "runner_measurement_invalid")
    require(
        workspace != runner_temp
        and workspace not in runner_temp.parents
        and runner_temp not in workspace.parents,
        "runner_measurement_invalid",
    )
    return workspace, runner_temp, runner_temp / STATE_NAME


def _policy(root: Path) -> Mapping[str, Any]:
    publication = _json(
        root / "contracts/helm-publication.json",
        "runner_measurement_policy_invalid",
    )
    measurement = publication.get("runner_measurement")
    require(isinstance(measurement, Mapping), "runner_measurement_policy_invalid")
    required = [
        "peak_memory_bytes",
        "peak_local_storage_bytes",
        "source_sha",
        "workflow_api",
        "product_id",
    ]
    require(
        publication.get("runner_profile") == measurement.get("candidate_profile")
        and measurement.get("status") == "required-before-final-candidate"
        and measurement.get("required_evidence") == required
        and isinstance(measurement.get("headroom_percent"), int)
        and 0 <= measurement["headroom_percent"] <= 100,
        "runner_measurement_policy_invalid",
    )
    return measurement


def _finalize_evidence(
    root: Path,
    config: Mapping[str, Any],
    record: Mapping[str, Any],
) -> dict[str, str]:
    evidence: dict[str, Any] = {
        "peak_memory_bytes": int(record["peak_memory_bytes"]),
        "peak_local_storage_bytes": int(record["workspace_baseline_bytes"])
        + int(record["peak_runner_temp_bytes"]),
        "source_sha": str(config["source_sha"]),
        "workflow_api": "helm.publish",
        "product_id": str(config["product_id"]),
    }
    contract = runners.load_runner_contract(root)
    runners.validate_buildah_evidence(contract, evidence)
    measurement = _policy(root)
    selected = runners.select_buildah_tier(
        contract,
        peak_memory_bytes=evidence["peak_memory_bytes"],
        peak_local_storage_bytes=evidence["peak_local_storage_bytes"],
        headroom_percent=measurement["headroom_percent"],
    )
    require(selected == measurement["candidate_profile"], "runner_measurement_tier_mismatch")
    return {
        "result": "success",
        "peak_memory_bytes": str(evidence["peak_memory_bytes"]),
        "peak_local_storage_bytes": str(evidence["peak_local_storage_bytes"]),
        "runner_evidence_json": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        "selected_profile": selected,
    }


def start(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    product_id = environment.get("INPUT_PRODUCT_ID", "").strip()
    require(FULL_SHA.fullmatch(admitted_sha) is not None, "runner_measurement_invalid")
    require(NAME.fullmatch(product_id) is not None, "runner_measurement_invalid")
    workspace, runner_temp, state = _roots(environment)
    require(not state.exists() and not state.is_symlink(), "runner_measurement_state_exists")
    state.mkdir(mode=0o700)
    try:
        _write_json(
            state / CONFIG_NAME,
            {
                "workspace": str(workspace),
                "runner_temp": str(runner_temp),
                "source_sha": admitted_sha,
                "product_id": product_id,
            },
        )
        _write_json(
            state / RECORD_NAME,
            {
                "workspace_baseline_bytes": _allocated_bytes(workspace),
                "peak_runner_temp_bytes": _allocated_bytes(runner_temp),
                "peak_memory_bytes": _memory_bytes(),
            },
        )
        entrypoint = root / "scripts/ci/helm_measurement.py"
        require(entrypoint.is_file() and not entrypoint.is_symlink(), "runner_measurement_invalid")
        process = subprocess.Popen(
            [sys.executable, str(entrypoint), "monitor", "--state-dir", str(state)],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        (state / PID_NAME).write_text(f"{process.pid}\n", encoding="utf-8")
        return {"result": "started"}
    except Exception:
        shutil.rmtree(state, ignore_errors=True)
        raise


def monitor(state: Path) -> int:
    try:
        config = _json(state / CONFIG_NAME, "runner_measurement_invalid")
        runner_temp = Path(str(config["runner_temp"]))
        while not (state / STOP_NAME).exists():
            record = dict(_json(state / RECORD_NAME, "runner_measurement_invalid"))
            record["peak_runner_temp_bytes"] = max(
                int(record["peak_runner_temp_bytes"]), _allocated_bytes(runner_temp)
            )
            record["peak_memory_bytes"] = max(int(record["peak_memory_bytes"]), _memory_bytes())
            _write_json(state / RECORD_NAME, record)
            time.sleep(SAMPLE_SECONDS)
        record = dict(_json(state / RECORD_NAME, "runner_measurement_invalid"))
        record["peak_runner_temp_bytes"] = max(
            int(record["peak_runner_temp_bytes"]), _allocated_bytes(runner_temp)
        )
        record["peak_memory_bytes"] = max(int(record["peak_memory_bytes"]), _memory_bytes())
        _write_json(state / RECORD_NAME, record)
        (state / DONE_NAME).write_text("ok\n", encoding="utf-8")
        return 0
    except Exception:
        try:
            (state / ERROR_NAME).write_text("monitor_failed\n", encoding="utf-8")
        except OSError:
            pass
        return 2


def _read_pid(state: Path) -> int:
    try:
        raw = (state / PID_NAME).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise HelmValidationError("runner_measurement_invalid") from error
    require(raw.isdigit() and int(raw) > 1, "runner_measurement_invalid")
    return int(raw)


def _terminate(pid: int | None) -> None:
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as error:
        raise HelmValidationError("runner_measurement_cleanup_failed") from error
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)


def _cleanup_state(state: Path, pid: int | None) -> None:
    _terminate(pid)
    try:
        if state.is_symlink():
            state.unlink()
        elif state.exists():
            shutil.rmtree(state)
    except OSError as error:
        raise HelmValidationError("runner_measurement_cleanup_failed") from error
    require(not state.exists() and not state.is_symlink(), "runner_measurement_cleanup_failed")


def stop(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    product_id = environment.get("INPUT_PRODUCT_ID", "").strip()
    _, _, state = _roots(environment)
    require(state.is_dir() and not state.is_symlink(), "runner_measurement_missing")

    pid: int | None = None
    values: dict[str, str] | None = None
    failure: Exception | None = None
    try:
        pid = _read_pid(state)
        config = _json(state / CONFIG_NAME, "runner_measurement_invalid")
        require(
            config.get("source_sha") == admitted_sha and config.get("product_id") == product_id,
            "runner_measurement_mismatch",
        )
        (state / STOP_NAME).write_text("stop\n", encoding="utf-8")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (state / ERROR_NAME).exists():
                raise HelmValidationError("runner_measurement_monitor_failed")
            if (state / DONE_NAME).exists():
                break
            time.sleep(0.1)
        else:
            raise HelmValidationError("runner_measurement_monitor_timeout")
        values = _finalize_evidence(
            root,
            config,
            _json(state / RECORD_NAME, "runner_measurement_invalid"),
        )
    except Exception as error:
        failure = error

    _cleanup_state(state, pid)
    if failure is not None:
        raise failure
    require(values is not None, "runner_measurement_invalid")
    return values
