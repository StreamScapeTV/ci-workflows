"""No-follow temporary state and credential file handling for Flux reconciliation."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from .maintenance_contract import MaintenanceError

def _state_directory(state_root: Path) -> Path:
    state = state_root.absolute()
    try:
        metadata = state.lstat()
    except FileNotFoundError:
        parent = state.parent
        if not parent.is_dir() or parent.is_symlink():
            raise MaintenanceError("flux_state_invalid")
        try:
            state.mkdir(mode=0o700)
        except OSError as error:
            raise MaintenanceError("flux_state_invalid") from error
        metadata = state.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise MaintenanceError("flux_state_invalid")
    try:
        state.chmod(0o700)
    except OSError as error:
        raise MaintenanceError("flux_state_invalid") from error
    return state


def _exclusive_write(path: Path, value: str, *, code: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise MaintenanceError(code) from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _write_secret(path: Path, value: str) -> None:
    _exclusive_write(path, value, code="flux_state_invalid")
    try:
        if stat.S_IMODE(path.lstat().st_mode) != 0o600:
            raise MaintenanceError("flux_state_invalid")
    except OSError as error:
        raise MaintenanceError("flux_state_invalid") from error
