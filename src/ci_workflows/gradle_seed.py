"""Portable Gradle dependency-module cache selection and framing primitives.

This module contains no GitHub identity, event, ref, token, or transport authority.
The active internal cache-sync transport lives in ``gradle_seed_internal`` and
reuses only the bounded ``caches/modules-*`` selection/framing contract here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

MAGIC = b"GRADLE-SEED-V1\n"
CONTENT_TYPE = "application/vnd.faruqi.gradle-seed-v1"
FLUX_HOST = "arc-gradle-seed-promoter.github-actions-runners.svc.cluster.local"
FLUX_PORT = 8080
FLUX_PATH = "/v1/gradle-seed"

MAX_HEADER_BYTES = 2048
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_FILE_COUNT = 50_000
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
READ_CHUNK_BYTES = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 30

_MODULE_CACHE = re.compile(r"^modules-[A-Za-z0-9._-]+$")


class GradleSeedError(RuntimeError):
    """Fail-closed Gradle seed error with a stable non-secret code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SeedFile:
    relative_path: str
    home_path: Path
    home_device: int
    home_inode: int
    size: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int

    def header_bytes(self) -> bytes:
        payload = json.dumps(
            {
                "path": self.relative_path,
                "sha256": self.sha256,
                "size": self.size,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(payload) > MAX_HEADER_BYTES:
            raise GradleSeedError("gradle_seed_header_too_large")
        return payload


@dataclass(frozen=True)
class UploadResponse:
    status: int
    content_type: str
    body: bytes


@dataclass(frozen=True)
class GradleSeedResult:
    source_sha: str
    generation: str
    file_count: int
    total_bytes: int
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        return {
            "result": "promoted",
            "source_sha": self.source_sha,
            "generation": self.generation,
            "file_count": str(self.file_count),
            "total_bytes": str(self.total_bytes),
            "evidence_id": self.evidence_id,
        }


def _safe_relative_path(parts: tuple[str, ...]) -> str:
    if not parts or _MODULE_CACHE.fullmatch(parts[0]) is None:
        raise GradleSeedError("gradle_seed_path_rejected")
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
        ):
            raise GradleSeedError("gradle_seed_path_rejected")
    relative = "/".join(parts)
    if len(relative.encode("utf-8")) > 2048:
        raise GradleSeedError("gradle_seed_path_rejected")
    return relative


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _regular_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_absolute_directory_nofollow(
    path: Path,
    *,
    code: str = "gradle_seed_path_rejected",
) -> tuple[int, os.stat_result]:
    if not path.is_absolute():
        raise GradleSeedError(code)
    parts = path.parts
    if not parts or parts[0] != os.sep or any(part in {"", ".", ".."} for part in parts[1:]):
        raise GradleSeedError(code)
    try:
        current_fd = os.open(os.sep, _directory_flags())
    except OSError as error:
        raise GradleSeedError(code) from error
    try:
        current_stat = os.fstat(current_fd)
        if not stat.S_ISDIR(current_stat.st_mode):
            raise GradleSeedError(code)
        for part in parts[1:]:
            try:
                next_fd = os.open(part, _directory_flags(), dir_fd=current_fd)
            except OSError as error:
                raise GradleSeedError(code) from error
            try:
                next_stat = os.fstat(next_fd)
                if not stat.S_ISDIR(next_stat.st_mode):
                    raise GradleSeedError(code)
            except BaseException:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
            current_stat = next_stat
        return current_fd, current_stat
    except BaseException:
        os.close(current_fd)
        raise


def _open_child_directory(
    parent_fd: int,
    name: str,
    *,
    expected: os.stat_result | None = None,
    code: str = "gradle_seed_path_rejected",
) -> tuple[int, os.stat_result]:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise GradleSeedError(code)
    try:
        child_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as error:
        raise GradleSeedError(code) from error
    try:
        current = os.fstat(child_fd)
        if not stat.S_ISDIR(current.st_mode):
            raise GradleSeedError(code)
        if expected is not None and (
            current.st_dev != expected.st_dev or current.st_ino != expected.st_ino
        ):
            raise GradleSeedError("gradle_seed_path_changed")
        return child_fd, current
    except BaseException:
        os.close(child_fd)
        raise


def _directory_entries(directory_fd: int) -> tuple[tuple[str, os.stat_result], ...]:
    entries: list[tuple[str, os.stat_result]] = []
    try:
        with os.scandir(directory_fd) as iterator:
            for entry in iterator:
                try:
                    entry_stat = os.stat(
                        entry.name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except OSError as error:
                    raise GradleSeedError("gradle_seed_path_rejected") from error
                entries.append((entry.name, entry_stat))
    except GradleSeedError:
        raise
    except OSError as error:
        raise GradleSeedError("gradle_seed_path_rejected") from error
    return tuple(sorted(entries, key=lambda item: item[0]))


def _require_regular_identity(
    current: os.stat_result,
    expected: os.stat_result,
) -> None:
    if not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(current.st_mode):
        raise GradleSeedError("gradle_seed_file_rejected")
    if expected.st_nlink != 1 or current.st_nlink != 1:
        raise GradleSeedError("gradle_seed_hardlink_rejected")
    if (
        current.st_dev != expected.st_dev
        or current.st_ino != expected.st_ino
        or current.st_size != expected.st_size
        or current.st_mtime_ns != expected.st_mtime_ns
    ):
        raise GradleSeedError("gradle_seed_file_changed")


def _open_regular_at(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
) -> tuple[int, os.stat_result]:
    if expected.st_nlink != 1:
        raise GradleSeedError("gradle_seed_hardlink_rejected")
    try:
        fd = os.open(name, _regular_flags(), dir_fd=parent_fd)
    except OSError as error:
        raise GradleSeedError("gradle_seed_file_rejected") from error
    try:
        current = os.fstat(fd)
        _require_regular_identity(current, expected)
        return fd, current
    except BaseException:
        os.close(fd)
        raise


def _hash_open_regular_file(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
) -> str:
    fd, _current = _open_regular_at(parent_fd, name, expected)
    try:
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        final = os.fstat(fd)
        _require_regular_identity(final, expected)
        if total != expected.st_size:
            raise GradleSeedError("gradle_seed_file_changed")
        return digest.hexdigest()
    finally:
        os.close(fd)


def collect_seed_files(gradle_user_home: Path) -> tuple[SeedFile, ...]:
    """Collect deterministic writable ``modules-*`` files without following links."""

    home = gradle_user_home
    home_fd, home_stat = _open_absolute_directory_nofollow(
        home,
        code="gradle_seed_home_rejected",
    )
    selected: list[SeedFile] = []
    total_bytes = 0

    def visit(directory_fd: int, prefix: tuple[str, ...]) -> None:
        nonlocal total_bytes
        for name, entry_stat in _directory_entries(directory_fd):
            parts = (*prefix, name)
            _safe_relative_path(parts)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise GradleSeedError("gradle_seed_symlink_rejected")
            if stat.S_ISDIR(entry_stat.st_mode):
                child_fd, _child_stat = _open_child_directory(
                    directory_fd,
                    name,
                    expected=entry_stat,
                )
                try:
                    visit(child_fd, parts)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise GradleSeedError("gradle_seed_entry_unsupported")
            if name == "gc.properties" or name.endswith(".lock"):
                continue
            if entry_stat.st_nlink != 1:
                raise GradleSeedError("gradle_seed_hardlink_rejected")
            if entry_stat.st_size > MAX_FILE_BYTES:
                raise GradleSeedError("gradle_seed_file_too_large")
            if len(selected) >= MAX_FILE_COUNT:
                raise GradleSeedError("gradle_seed_file_count_exceeded")
            total_bytes += entry_stat.st_size
            if total_bytes > MAX_UPLOAD_BYTES:
                raise GradleSeedError("gradle_seed_payload_too_large")
            sha256 = _hash_open_regular_file(directory_fd, name, entry_stat)
            selected.append(
                SeedFile(
                    relative_path=_safe_relative_path(parts),
                    home_path=home,
                    home_device=home_stat.st_dev,
                    home_inode=home_stat.st_ino,
                    size=entry_stat.st_size,
                    sha256=sha256,
                    device=entry_stat.st_dev,
                    inode=entry_stat.st_ino,
                    mtime_ns=entry_stat.st_mtime_ns,
                )
            )

    try:
        caches_fd, _caches_stat = _open_child_directory(
            home_fd,
            "caches",
            code="gradle_seed_cache_rejected",
        )
        try:
            module_roots: list[tuple[str, os.stat_result]] = []
            for name, entry_stat in _directory_entries(caches_fd):
                if _MODULE_CACHE.fullmatch(name) is None:
                    continue
                if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
                    raise GradleSeedError("gradle_seed_path_rejected")
                module_roots.append((name, entry_stat))
            if not module_roots:
                raise GradleSeedError("gradle_seed_delta_empty")
            for name, entry_stat in module_roots:
                module_fd, _module_stat = _open_child_directory(
                    caches_fd,
                    name,
                    expected=entry_stat,
                )
                try:
                    visit(module_fd, (name,))
                finally:
                    os.close(module_fd)
        finally:
            os.close(caches_fd)
    finally:
        os.close(home_fd)

    if not selected:
        raise GradleSeedError("gradle_seed_delta_empty")
    return tuple(selected)


def _open_selected_file(seed_file: SeedFile) -> int:
    home_fd, home_stat = _open_absolute_directory_nofollow(
        seed_file.home_path,
        code="gradle_seed_home_rejected",
    )
    if home_stat.st_dev != seed_file.home_device or home_stat.st_ino != seed_file.home_inode:
        os.close(home_fd)
        raise GradleSeedError("gradle_seed_file_changed")
    opened_directories: list[int] = [home_fd]
    try:
        caches_fd, _caches_stat = _open_child_directory(home_fd, "caches")
        opened_directories.append(caches_fd)
        parts = tuple(seed_file.relative_path.split("/"))
        _safe_relative_path(parts)
        current_fd = caches_fd
        for part in parts[:-1]:
            child_fd, _child_stat = _open_child_directory(current_fd, part)
            opened_directories.append(child_fd)
            current_fd = child_fd
        try:
            file_fd = os.open(parts[-1], _regular_flags(), dir_fd=current_fd)
        except OSError as error:
            raise GradleSeedError("gradle_seed_file_rejected") from error
        try:
            current = os.fstat(file_fd)
            if current.st_nlink != 1:
                raise GradleSeedError("gradle_seed_hardlink_rejected")
            if (
                current.st_dev != seed_file.device
                or current.st_ino != seed_file.inode
                or current.st_size != seed_file.size
                or current.st_mtime_ns != seed_file.mtime_ns
            ):
                raise GradleSeedError("gradle_seed_file_changed")
        except BaseException:
            os.close(file_fd)
            raise
        return file_fd
    finally:
        for directory_fd in reversed(opened_directories):
            os.close(directory_fd)


def framed_seed_stream(files: tuple[SeedFile, ...]) -> Iterator[bytes]:
    """Yield the protocol without materializing an archive or candidate copy."""

    yield MAGIC
    for seed_file in files:
        header = seed_file.header_bytes()
        yield struct.pack(">I", len(header))
        yield header
        fd = _open_selected_file(seed_file)
        digest = hashlib.sha256()
        total = 0
        try:
            while True:
                chunk = os.read(fd, READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                yield chunk
            final = os.fstat(fd)
        finally:
            os.close(fd)
        if final.st_nlink != 1:
            raise GradleSeedError("gradle_seed_hardlink_rejected")
        if (
            total != seed_file.size
            or digest.hexdigest() != seed_file.sha256
            or final.st_dev != seed_file.device
            or final.st_ino != seed_file.inode
            or final.st_size != seed_file.size
            or final.st_mtime_ns != seed_file.mtime_ns
        ):
            raise GradleSeedError("gradle_seed_file_changed")
    yield struct.pack(">I", 0)
