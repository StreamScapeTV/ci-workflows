"""Expose isolated credentials at SwiftPM's standard native-home lookup path."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def remove_stale_link(destination: Path) -> None:
    """Drop a dangling native netrc link a killed job left on a persistent runner.

    Only a symlink whose target is gone and is one of our own private copies
    (``<RUNNER_TEMP>/central-registry-auth/native-netrc-*``) counts; a live link of a
    concurrent job, a regular file or any other link stays and install refuses it.
    """
    if not destination.is_symlink() or os.path.exists(destination):
        return
    target = Path(os.readlink(destination))
    if target.is_absolute() and target.parent.name == "central-registry-auth" and target.name.startswith("native-netrc-"):
        destination.unlink()


def install(auth_root: Path, runner_home: Path) -> None:
    native_home = subprocess.run(
        ["swift", "-e", "import Foundation; print(NSHomeDirectory())"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not Path(native_home).samefile(runner_home):
        raise ValueError("runner HOME does not match native macOS home")
    source = auth_root / "home" / ".netrc"
    if source.is_symlink() or not source.is_file() or source.stat().st_mode & 0o077:
        raise ValueError("isolated netrc must be a private regular file")
    destination = runner_home / ".netrc"
    remove_stale_link(destination)
    if os.path.lexists(destination):
        raise FileExistsError("native netrc already exists")
    # A unique private target proves ownership even if installation is interrupted.
    # Publish intent before creating the native-home link, so always cleanup can
    # recover that interruption without mistaking a preexisting link for ours.
    with tempfile.NamedTemporaryFile(prefix="native-netrc-", dir=auth_root, delete=False) as handle:
        handle.write(source.read_bytes())
        source = Path(handle.name)
    with (auth_root / "native-netrc.json").open("x") as handle:
        json.dump({"destination": str(destination), "source": str(source)}, handle)
    # symlink() is exclusive, including when a dangling destination already exists.
    destination.symlink_to(source)


def cleanup(auth_root: Path) -> None:
    marker = auth_root / "native-netrc.json"
    if not marker.exists():
        return
    owned = json.loads(marker.read_text())
    source = Path(owned["source"])
    destination = Path(owned["destination"])
    if source.parent != auth_root or not source.name.startswith("native-netrc-") or destination.name != ".netrc":
        raise ValueError("invalid native netrc ownership marker")
    if not os.path.lexists(destination):
        source.unlink(missing_ok=True)
        marker.unlink()
        return
    if not destination.is_symlink() or os.readlink(destination) != str(source):
        raise ValueError("native netrc entry changed; refusing to remove it")
    destination.unlink()
    source.unlink(missing_ok=True)
    marker.unlink()


if __name__ == "__main__":
    root = Path(os.environ["RUNNER_TEMP"]) / "central-registry-auth"
    if sys.argv[1:] == ["install"]:
        install(root, Path(os.environ["HOME"]))
    elif sys.argv[1:] == ["cleanup"]:
        cleanup(root)
    else:
        raise SystemExit("expected install or cleanup")
