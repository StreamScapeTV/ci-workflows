"""Bounded Android execution resource measurements for one existing CI executor."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX import boundary
    resource = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class AndroidResourceMetrics:
    """Finite, path-free resource facts safe for bounded CI evidence."""

    wall_ms: int
    child_cpu_ms: int | None
    peak_memory_bytes: int | None
    peak_processes: int | None
    measurement_source: str


def _child_cpu_seconds() -> float | None:
    if resource is None:
        return None
    try:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    except (OSError, ValueError):
        return None
    value = float(usage.ru_utime) + float(usage.ru_stime)
    return value if value >= 0 else None


def _read_nonnegative_integer(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
        value = int(text, 10)
    except (OSError, UnicodeError, ValueError):
        return None
    return value if value >= 0 else None


def _cgroup_v2_directory(proc_cgroup: Path, cgroup_root: Path) -> Path | None:
    """Resolve only this process' unified cgroup beneath the fixed cgroup root."""

    try:
        root = cgroup_root.resolve(strict=True)
        rows = proc_cgroup.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return None
    relative: str | None = None
    for row in rows:
        if row.startswith("0::"):
            relative = row[3:]
            break
    if relative is None or not relative.startswith("/") or "\x00" in relative:
        return None
    pure = PurePosixPath(relative)
    if ".." in pure.parts:
        return None
    try:
        candidate = (root / relative.lstrip("/")).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_dir() and not candidate.is_symlink() else None


class AndroidResourceSampler:
    """Sample Linux cgroup-v2 usage without changing the measured child process."""

    def __init__(
        self,
        *,
        proc_cgroup: Path = Path("/proc/self/cgroup"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        poll_interval_seconds: float = 0.1,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        cpu_reader: Callable[[], float | None] = _child_cpu_seconds,
    ) -> None:
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or poll_interval_seconds <= 0
            or poll_interval_seconds > 5
        ):
            raise ValueError("poll interval must be a finite positive number at most five seconds")
        self._proc_cgroup = Path(proc_cgroup)
        self._cgroup_root = Path(cgroup_root)
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._clock_ns = clock_ns
        self._cpu_reader = cpu_reader
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._memory_current: Path | None = None
        self._pids_current: Path | None = None
        self._peak_memory_bytes: int | None = None
        self._peak_processes: int | None = None
        self._started_ns: int | None = None
        self._cpu_started: float | None = None
        self._result: AndroidResourceMetrics | None = None

    def _sample(self) -> None:
        if self._memory_current is not None:
            memory = _read_nonnegative_integer(self._memory_current)
            if memory is not None:
                self._peak_memory_bytes = (
                    memory
                    if self._peak_memory_bytes is None
                    else max(self._peak_memory_bytes, memory)
                )
        if self._pids_current is not None:
            processes = _read_nonnegative_integer(self._pids_current)
            if processes is not None:
                self._peak_processes = (
                    processes
                    if self._peak_processes is None
                    else max(self._peak_processes, processes)
                )

    def _poll(self) -> None:
        while not self._stop.wait(self._poll_interval_seconds):
            self._sample()

    def start(self) -> "AndroidResourceSampler":
        if self._started_ns is not None or self._result is not None:
            raise RuntimeError("resource sampler may be started only once")
        self._started_ns = int(self._clock_ns())
        self._cpu_started = self._cpu_reader()
        directory = _cgroup_v2_directory(self._proc_cgroup, self._cgroup_root)
        if directory is not None:
            memory_current = directory / "memory.current"
            pids_current = directory / "pids.current"
            self._memory_current = memory_current if memory_current.is_file() else None
            self._pids_current = pids_current if pids_current.is_file() else None
        self._sample()
        if self._memory_current is not None or self._pids_current is not None:
            self._thread = threading.Thread(
                target=self._poll,
                name="ciw-android-resource-sampler",
                daemon=True,
            )
            self._thread.start()
        return self

    def stop(self) -> AndroidResourceMetrics:
        if self._result is not None:
            return self._result
        if self._started_ns is None:
            raise RuntimeError("resource sampler was not started")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._poll_interval_seconds * 2))
        self._sample()
        stopped_ns = int(self._clock_ns())
        cpu_stopped = self._cpu_reader()
        child_cpu_ms: int | None = None
        if self._cpu_started is not None and cpu_stopped is not None:
            child_cpu_ms = max(0, int(round((cpu_stopped - self._cpu_started) * 1000)))
        source = (
            "cgroup-v2-sampled"
            if self._peak_memory_bytes is not None or self._peak_processes is not None
            else "unavailable"
        )
        self._result = AndroidResourceMetrics(
            wall_ms=max(0, (stopped_ns - self._started_ns) // 1_000_000),
            child_cpu_ms=child_cpu_ms,
            peak_memory_bytes=self._peak_memory_bytes,
            peak_processes=self._peak_processes,
            measurement_source=source,
        )
        return self._result

    @property
    def result(self) -> AndroidResourceMetrics:
        if self._result is None:
            raise RuntimeError("resource sampler has not stopped")
        return self._result

    def __enter__(self) -> "AndroidResourceSampler":
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.stop()
        return False
