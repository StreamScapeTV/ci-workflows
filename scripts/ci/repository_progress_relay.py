#!/usr/bin/env python3
"""Relay bounded Repository CI progress into the canonical Agent State CI row.

Repository scripts may write arbitrary private diagnostic lines to CI_PROGRESS_FILE.
Only lines prefixed with ``CI_PROGRESS_V1 `` participate in the safe current
projection. Product code never receives Agent State credentials; Central owns the
service-only relay.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from typing import Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request

PREFIX = b"CI_PROGRESS_V1 "
FIXED_WRAPPERS = (
    "source-admission",
    "private-capabilities",
    "repository-entrypoint",
    "evidence",
    "cleanup-settlement",
)
PRODUCT_ID_RE = re.compile(r"[a-z][a-z0-9._-]{0,47}\Z")
STATUSES = {"pending", "running", "succeeded", "failed", "skipped"}
TERMINAL_STATUSES = {"succeeded", "failed", "skipped"}
TIMELINE_TERMINAL_MAP = {
    "complete": "succeeded",
    "failed": "failed",
    "cancelled": "failed",
    "skipped": "skipped",
}
MAX_PRODUCT_STEPS = 32
MAX_PROGRESS_FILE_BYTES = 16 * 1024 * 1024
MAX_STRUCTURED_LINE_BYTES = 4096
MAX_STRUCTURED_EVENTS = 512
MAX_TIMELINE_BYTES = 16 * 1024
MAX_RELAY_STATE_BYTES = 32 * 1024
MAX_CREDENTIALS_BYTES = 16 * 1024
HTTP_TIMEOUT_SECONDS = 25
MAX_REPORT_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (2, 4, 8)
TRANSIENT_HTTP = {408, 429}
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_HEARTBEAT_SECONDS = 60.0


class ProgressError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class ReportError(RuntimeError):
    def __init__(self, category: str, *, transient: bool = False) -> None:
        super().__init__(category)
        self.category = category
        self.transient = transient


@dataclass(frozen=True)
class ProductProgress:
    steps: tuple[str, ...]
    statuses: tuple[str, ...]

    def status_map(self) -> dict[str, str]:
        return dict(zip(self.steps, self.statuses, strict=True))


@dataclass(frozen=True)
class TimelineProgress:
    completed: Mapping[str, str]
    active: str | None


@dataclass
class RelayState:
    revision: int = 0
    last_shape: dict[str, object] | None = None
    last_activity_mtime_ns: int = 0
    last_report_monotonic: float = 0.0


def _regular_file(path: Path, *, maximum: int, category: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ProgressError(f"invalid_{category}_path")
    if path.stat().st_size > maximum:
        raise ProgressError(f"{category}_too_large")


def _transition_allowed(old: str, new: str) -> bool:
    return old == new or (
        old == "pending" and new in {"running", "succeeded", "failed", "skipped"}
    ) or (old == "running" and new in {"succeeded", "failed", "skipped"})


def parse_product_progress(path: Path) -> ProductProgress | None:
    """Parse only the structured V1 subset; free-form/private lines are ignored."""
    _regular_file(path, maximum=MAX_PROGRESS_FILE_BYTES, category="progress")
    plan: tuple[str, ...] | None = None
    statuses: dict[str, str] = {}
    structured_events = 0

    with path.open("rb") as handle:
        for raw_line in handle:
            if not raw_line.startswith(PREFIX):
                continue
            structured_events += 1
            if structured_events > MAX_STRUCTURED_EVENTS:
                raise ProgressError("too_many_structured_events")
            raw = raw_line.rstrip(b"\r\n")
            if len(raw) > MAX_STRUCTURED_LINE_BYTES:
                raise ProgressError("structured_line_too_large")
            try:
                value = json.loads(raw[len(PREFIX) :].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ProgressError("invalid_structured_json") from None
            if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
                raise ProgressError("invalid_structured_event")

            kind = value["kind"]
            if kind == "plan":
                if set(value) != {"kind", "steps"} or not isinstance(value["steps"], list):
                    raise ProgressError("invalid_plan")
                raw_steps = value["steps"]
                if not 1 <= len(raw_steps) <= MAX_PRODUCT_STEPS:
                    raise ProgressError("invalid_plan_size")
                if any(not isinstance(step, str) or PRODUCT_ID_RE.fullmatch(step) is None for step in raw_steps):
                    raise ProgressError("invalid_step_id")
                if len(raw_steps) != len(set(raw_steps)):
                    raise ProgressError("duplicate_step_id")
                if any(step in FIXED_WRAPPERS for step in raw_steps):
                    raise ProgressError("reserved_step_id")
                candidate = tuple(raw_steps)
                if plan is None:
                    plan = candidate
                    statuses = {step: "pending" for step in candidate}
                elif plan != candidate:
                    raise ProgressError("plan_conflict")
                continue

            if kind != "step" or set(value) != {"kind", "step", "status"}:
                raise ProgressError("unsupported_structured_event")
            if plan is None:
                raise ProgressError("step_before_plan")
            step = value.get("step")
            status = value.get("status")
            if not isinstance(step, str) or step not in statuses:
                raise ProgressError("unknown_step")
            if not isinstance(status, str) or status not in STATUSES:
                raise ProgressError("invalid_step_status")

            old = statuses[step]
            if not _transition_allowed(old, status):
                raise ProgressError("transition_conflict")
            if old == status:
                continue

            index = plan.index(step)
            earlier = [statuses[item] for item in plan[:index]]
            later = [statuses[item] for item in plan[index + 1 :]]
            if any(item not in TERMINAL_STATUSES for item in earlier):
                raise ProgressError("step_out_of_order")
            if any(item != "pending" for item in later):
                raise ProgressError("step_out_of_order")
            if status == "running" and any(item == "running" for item in statuses.values()):
                raise ProgressError("multiple_running_steps")
            statuses[step] = status

    if plan is None:
        return None
    return ProductProgress(plan, tuple(statuses[step] for step in plan))



def preserve_monotonic_product(
    previous: ProductProgress | None,
    candidate: ProductProgress | None,
) -> ProductProgress | None:
    if previous is None:
        return candidate
    if candidate is None:
        return previous
    if previous.steps != candidate.steps:
        raise ProgressError("plan_snapshot_conflict")
    for old, new in zip(previous.statuses, candidate.statuses, strict=True):
        if not _transition_allowed(old, new):
            raise ProgressError("transition_snapshot_conflict")
    return candidate

def load_timeline(path: Path) -> TimelineProgress:
    _regular_file(path, maximum=MAX_TIMELINE_BYTES, category="timeline_state")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProgressError("invalid_timeline_json") from None
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ProgressError("invalid_timeline_schema")
    completed_raw = state.get("completed", {})
    if not isinstance(completed_raw, dict):
        raise ProgressError("invalid_timeline_completed")
    completed: dict[str, str] = {}
    for name, status in completed_raw.items():
        if name not in FIXED_WRAPPERS or status not in TIMELINE_TERMINAL_MAP:
            raise ProgressError("invalid_timeline_completed")
        completed[name] = status

    # Compatibility with a timeline state produced before completed-status
    # projection existed. Current runs always write the explicit map.
    last_completed = state.get("last_completed_ordinal", 0)
    if not isinstance(last_completed, int) or isinstance(last_completed, bool) or not 0 <= last_completed <= len(FIXED_WRAPPERS):
        raise ProgressError("invalid_timeline_ordinal")
    for index in range(last_completed):
        completed.setdefault(FIXED_WRAPPERS[index], "complete")

    active_raw = state.get("active")
    active: str | None = None
    if active_raw is not None:
        if not isinstance(active_raw, dict) or not isinstance(active_raw.get("name"), str):
            raise ProgressError("invalid_timeline_active")
        active = active_raw["name"]
        if active not in FIXED_WRAPPERS:
            raise ProgressError("invalid_timeline_active")
    return TimelineProgress(completed, active)


def _wrapper_statuses(timeline: TimelineProgress) -> dict[str, str]:
    result = {name: "pending" for name in FIXED_WRAPPERS}
    for name, status in timeline.completed.items():
        result[name] = TIMELINE_TERMINAL_MAP[status]
    if timeline.active is not None:
        result[timeline.active] = "running"
    return result


def _settle_product(product: ProductProgress, repository_status: str) -> ProductProgress:
    statuses = list(product.statuses)
    open_indexes = [index for index, status in enumerate(statuses) if status not in TERMINAL_STATUSES]
    if not open_indexes:
        return product
    if repository_status in {"succeeded", "skipped"}:
        for index in open_indexes:
            statuses[index] = "skipped"
    elif any(status == "failed" for status in statuses):
        # Preserve the first explicit repository-owned failure as the narrow
        # diagnostic signal. Later work never becomes a second synthetic
        # failure merely because the fixed entrypoint exits nonzero.
        for index in open_indexes:
            statuses[index] = "skipped"
    else:
        first = open_indexes[0]
        statuses[first] = "failed"
        for index in open_indexes[1:]:
            statuses[index] = "skipped"
    return ProductProgress(product.steps, tuple(statuses))


def compose_projection(timeline: TimelineProgress, product: ProductProgress | None) -> dict[str, object]:
    wrappers = _wrapper_statuses(timeline)
    product_statuses: ProductProgress | None = None

    repository_reached = (
        timeline.active == "repository-entrypoint"
        or "repository-entrypoint" in timeline.completed
        or any(name in timeline.completed or timeline.active == name for name in ("evidence", "cleanup-settlement"))
    )
    if product is not None and repository_reached:
        # Once the product declares a plan, repository-entrypoint represents the
        # fixed admission/launch gate; detailed repository-owned work follows it.
        wrappers["repository-entrypoint"] = "succeeded"
        product_statuses = product
        if "repository-entrypoint" in timeline.completed:
            terminal = TIMELINE_TERMINAL_MAP[timeline.completed["repository-entrypoint"]]
            product_statuses = _settle_product(product_statuses, terminal)

    steps: list[dict[str, str]] = [
        {"id": "source-admission", "status": wrappers["source-admission"]},
        {"id": "private-capabilities", "status": wrappers["private-capabilities"]},
        {"id": "repository-entrypoint", "status": wrappers["repository-entrypoint"]},
    ]
    if product_statuses is not None:
        steps.extend(
            {"id": step, "status": status}
            for step, status in zip(product_statuses.steps, product_statuses.statuses, strict=True)
        )
    steps.extend(
        [
            {"id": "evidence", "status": wrappers["evidence"]},
            {"id": "cleanup-settlement", "status": wrappers["cleanup-settlement"]},
        ]
    )

    running = [step for step in steps if step["status"] == "running"]
    if len(running) > 1:
        raise ProgressError("projection_multiple_running")
    completed = [step for step in steps if step["status"] in TERMINAL_STATUSES]
    current_id: str | None
    current_status: str | None
    if running:
        current_id = running[0]["id"]
        current_status = "running"
    elif completed:
        current_id = completed[-1]["id"]
        current_status = completed[-1]["status"]
    else:
        current_id = None
        current_status = None

    # Match the backend's ordered-plan validator before attempting any RPC.
    seen_pending = False
    seen_running = False
    for step in steps:
        status = step["status"]
        if status == "pending":
            seen_pending = True
        elif status == "running":
            if seen_pending or seen_running:
                raise ProgressError("projection_out_of_order")
            seen_running = True
        elif seen_pending or seen_running:
            raise ProgressError("projection_out_of_order")

    return {
        "schema_version": 1,
        "total_steps": len(steps),
        "completed_steps": len(completed),
        "current_step_id": current_id,
        "current_step_status": current_status,
        "steps": steps,
    }


def _activity_mtime_ns(log_path: Path, progress_path: Path) -> int:
    latest = 0
    for path, category, maximum in (
        (log_path, "log", MAX_PROGRESS_FILE_BYTES),
        (progress_path, "progress", MAX_PROGRESS_FILE_BYTES),
    ):
        _regular_file(path, maximum=maximum, category=category)
        latest = max(latest, path.stat().st_mtime_ns)
    return latest


def load_relay_state(path: Path) -> RelayState:
    if not path.exists():
        return RelayState()
    _regular_file(path, maximum=MAX_RELAY_STATE_BYTES, category="relay_state")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProgressError("invalid_relay_state") from None
    if not isinstance(value, dict) or set(value) != {
        "revision", "last_shape", "last_activity_mtime_ns", "last_report_monotonic"
    }:
        raise ProgressError("invalid_relay_state")
    revision = value["revision"]
    last_shape = value["last_shape"]
    activity = value["last_activity_mtime_ns"]
    report_time = value["last_report_monotonic"]
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or (last_shape is not None and not isinstance(last_shape, dict))
        or not isinstance(activity, int)
        or isinstance(activity, bool)
        or activity < 0
        or not isinstance(report_time, (int, float))
        or isinstance(report_time, bool)
        or report_time < 0
    ):
        raise ProgressError("invalid_relay_state")
    return RelayState(revision, last_shape, activity, float(report_time))


def write_relay_state(path: Path, state: RelayState) -> None:
    if path.is_symlink():
        raise ProgressError("invalid_relay_state_path")
    payload = json.dumps(
        {
            "revision": state.revision,
            "last_shape": state.last_shape,
            "last_activity_mtime_ns": state.last_activity_mtime_ns,
            "last_report_monotonic": state.last_report_monotonic,
        },
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    if len(payload.encode("utf-8")) > MAX_RELAY_STATE_BYTES:
        raise ProgressError("relay_state_too_large")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def read_credentials(stream: object = sys.stdin) -> tuple[str, str]:
    line = stream.readline(MAX_CREDENTIALS_BYTES + 1)  # type: ignore[attr-defined]
    if not line or len(line.encode("utf-8")) > MAX_CREDENTIALS_BYTES:
        raise ReportError("invalid_credentials")
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        raise ReportError("invalid_credentials") from None
    if not isinstance(value, dict) or set(value) != {"url", "key"}:
        raise ReportError("invalid_credentials")
    url = value["url"]
    key = value["key"]
    if not isinstance(url, str) or not isinstance(key, str) or not key:
        raise ReportError("invalid_credentials")
    parsed = urllib.parse.urlsplit(url.rstrip("/"))
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
        raise ReportError("invalid_credentials")
    return url.rstrip("/"), key


class AgentStateReporter:
    def __init__(
        self,
        url: str,
        key: str,
        *,
        opener: object = urllib.request,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url
        self.key = key
        self.opener = opener
        self.sleep_fn = sleep_fn

    def _request(self, request: urllib.request.Request) -> bytes:
        try:
            with self.opener.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # type: ignore[attr-defined]
                return response.read()
        except urllib.error.HTTPError as exc:
            transient = exc.code in TRANSIENT_HTTP or 500 <= exc.code <= 599
            raise ReportError(f"http_{exc.code}", transient=transient) from None
        except (TimeoutError, OSError, urllib.error.URLError):
            raise ReportError("transport", transient=True) from None

    def report(self, ci_run_id: str, projection: Mapping[str, object]) -> str:
        data = json.dumps(
            {"p_ci_run_id": ci_run_id, "p_progress": dict(projection)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url + "/rest/v1/rpc/report_ci_run_progress",
            data=data,
            method="POST",
            headers={
                "apikey": self.key,
                "Content-Type": "application/json",
                "Content-Profile": "agent_api",
                "Accept-Profile": "agent_api",
            },
        )
        last_error: ReportError | None = None
        for attempt in range(MAX_REPORT_ATTEMPTS):
            try:
                raw = self._request(request)
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ReportError("invalid_response") from None
                if not isinstance(value, dict) or not isinstance(value.get("code"), str):
                    raise ReportError("invalid_response")
                code = value["code"]
                if value.get("ok") is True and code in {
                    "progress_recorded", "activity_recorded", "already_reported"
                }:
                    return code
                if code == "ci_run_terminal":
                    return code
                raise ReportError(f"rpc_{code}")
            except ReportError as exc:
                last_error = exc
                if not exc.transient or attempt + 1 >= MAX_REPORT_ATTEMPTS:
                    raise
                self.sleep_fn(RETRY_DELAYS_SECONDS[attempt])
        assert last_error is not None
        raise last_error


def relay_loop(
    *,
    ci_run_id: str,
    progress_path: Path,
    timeline_state_path: Path,
    log_path: Path,
    state_path: Path,
    reporter: AgentStateReporter,
    stop_event: threading.Event,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> RelayState:
    if poll_seconds <= 0 or poll_seconds > 30:
        raise ProgressError("invalid_poll_interval")
    if heartbeat_seconds < 60 or heartbeat_seconds > 300:
        raise ProgressError("invalid_heartbeat_interval")
    state = load_relay_state(state_path)
    last_safe_product: ProductProgress | None = None
    last_warning = ""

    while True:
        stopping = stop_event.is_set()
        try:
            try:
                candidate = parse_product_progress(progress_path)
                last_safe_product = preserve_monotonic_product(last_safe_product, candidate)
                product = last_safe_product
                last_warning = ""
            except ProgressError as exc:
                if exc.category != last_warning:
                    print(f"repository CI progress parser warning: {exc.category}", file=sys.stderr)
                    last_warning = exc.category
                product = last_safe_product

            timeline = load_timeline(timeline_state_path)
            shape = compose_projection(timeline, product)
            activity_mtime_ns = _activity_mtime_ns(log_path, progress_path)
            now = monotonic_fn()
            shape_changed = state.last_shape != shape
            active = any(step["status"] == "running" for step in shape["steps"])  # type: ignore[index]
            activity_due = (
                active
                and activity_mtime_ns > state.last_activity_mtime_ns
                and now - state.last_report_monotonic >= heartbeat_seconds
            )
            should_report = shape_changed or activity_due or stopping
            if should_report:
                payload = dict(shape)
                payload["revision"] = state.revision + 1
                code = reporter.report(ci_run_id, payload)
                if code == "ci_run_terminal":
                    return state
                state.revision += 1
                state.last_shape = shape
                state.last_activity_mtime_ns = activity_mtime_ns
                state.last_report_monotonic = now
                write_relay_state(state_path, state)
        except (ProgressError, ReportError) as exc:
            category = exc.category
            print(f"repository CI progress relay warning: {category}", file=sys.stderr)

        if stopping:
            return state
        stop_event.wait(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("watch", "snapshot"))
    parser.add_argument("--ci-run-id")
    parser.add_argument("--progress-path", required=True)
    parser.add_argument("--timeline-state-path", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--state-path")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    args = parser.parse_args()

    try:
        product = parse_product_progress(Path(args.progress_path))
        timeline = load_timeline(Path(args.timeline_state_path))
        if args.command == "snapshot":
            print(json.dumps(compose_projection(timeline, product), sort_keys=True, separators=(",", ":")))
            return 0

        if not args.ci_run_id or not args.state_path:
            raise ProgressError("missing_watch_argument")
        url, key = read_credentials()
        stop_event = threading.Event()

        def stop(_signum: int, _frame: object) -> None:
            stop_event.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        relay_loop(
            ci_run_id=args.ci_run_id,
            progress_path=Path(args.progress_path),
            timeline_state_path=Path(args.timeline_state_path),
            log_path=Path(args.log_path),
            state_path=Path(args.state_path),
            reporter=AgentStateReporter(url, key),
            stop_event=stop_event,
            poll_seconds=args.poll_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
        )
        return 0
    except (ProgressError, ReportError) as exc:
        print(f"repository CI progress relay failed: {exc.category}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
