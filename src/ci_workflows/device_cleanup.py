"""No-follow checkout and device-state cleanup with exact checkout validation."""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath

from .device_contract_common import require
from .device_types import DeviceValidationError

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise DeviceValidationError("cleanup_failed") from error

def remove_no_follow(path: Path) -> None:
    metadata = _lstat(path)
    if metadata is None:
        return
    try:
        if stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
            os.unlink(path)
        elif stat.S_ISDIR(metadata.st_mode):
            with os.scandir(path) as entries:
                children = [path / entry.name for entry in entries]
            for child in children:
                remove_no_follow(child)
            os.rmdir(path)
        else:
            raise DeviceValidationError("cleanup_failed")
    except DeviceValidationError:
        raise
    except OSError as error:
        raise DeviceValidationError("cleanup_failed") from error
    require(_lstat(path) is None, "cleanup_failed")

def registered_state_paths(state_root: Path) -> tuple[Path, ...]:
    root = Path(os.path.abspath(state_root))
    metadata = _lstat(root)
    if metadata is not None:
        require(stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode), "cleanup_failed")
    return tuple(root / name for name in ("device-validation", "device-evidence", "device-credentials", "device-results"))

def cleanup_device_state(state_root: Path) -> None:
    for path in registered_state_paths(state_root):
        remove_no_follow(path)

def assert_zero_device_residue(state_root: Path) -> None:
    require(not [path for path in registered_state_paths(state_root) if _lstat(path) is not None], "cleanup_failed")

def cleanup_checkout_path(workspace: Path, relative: str) -> None:
    """Remove only the fixed `.ciw` or `source` checkout without following links."""

    require(relative in {".ciw", "source"}, "cleanup_failed")
    root = workspace.resolve()
    target = root / PurePosixPath(relative)
    require(target.parent == root, "cleanup_failed")
    remove_no_follow(target)
    require(_lstat(target) is None, "cleanup_failed")

def validate_exact_checkout(source_root: Path, expected_sha: str) -> None:
    require(FULL_SHA.fullmatch(expected_sha) is not None, "source_mismatch")
    require(source_root.is_dir() and not source_root.is_symlink(), "source_mismatch")
    try:
        head = subprocess.check_output(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(source_root), "status", "--porcelain=v1", "--untracked-files=all"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise DeviceValidationError("source_mismatch") from error
    require(head == expected_sha and status == "", "source_mismatch")

