"""Exact-source rootless service-runner canary primitives."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Sequence

_PROJECT_NAME = re.compile(r"^ciw-service-[A-Za-z0-9_.-]+$")
_PROJECT_LABEL = "io.podman.compose.project"


class ServiceRunnerSmokeError(RuntimeError):
    """Raised when the service runner violates its canary contract."""


def validate_project_name(value: str) -> str:
    """Accept only run-owned Compose project names."""

    if _PROJECT_NAME.fullmatch(value) is None:
        raise ServiceRunnerSmokeError(f"invalid service canary project name: {value!r}")
    return value


def compose_document() -> str:
    """Return the fixed two-service Compose fixture used by the live canary."""

    return textwrap.dedent(
        """\
        services:
          backend:
            image: docker.io/library/busybox:1.37.0
            command:
              - sh
              - -ec
              - |
                mkdir -p /www
                printf 'backend-ready\\n' > /www/ready
                exec httpd -f -p 8080 -h /www
            volumes:
              - backend-data:/data
          client:
            image: docker.io/library/busybox:1.37.0
            depends_on:
              - backend
            command:
              - sh
              - -ec
              - |
                for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
                  if wget -qO- http://backend:8080/ready | grep -Fx backend-ready; then
                    printf 'client-ready\\n' > /tmp/client-ready
                    exec sleep 600
                  fi
                  sleep 1
                done
                exit 1
        volumes:
          backend-data: {}
        """
    )


def _run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _capture(command: Sequence[str]) -> str:
    return _run(command).stdout.strip()


def verify_runtime_boundary() -> None:
    """Fail closed unless this is the dedicated rootless daemonless runtime."""

    if os.geteuid() == 0:
        raise ServiceRunnerSmokeError("service runner must not execute as root")
    if os.environ.get("STORAGE_DRIVER") != "vfs":
        raise ServiceRunnerSmokeError("service runner must use VFS storage")

    for forbidden in ("docker", "dockerd", "containerd", "buildah", "skopeo"):
        if shutil.which(forbidden) is not None:
            raise ServiceRunnerSmokeError(f"forbidden service-runner tool is present: {forbidden}")
    for socket in (Path("/var/run/docker.sock"), Path("/run/docker.sock")):
        if socket.exists():
            raise ServiceRunnerSmokeError(f"forbidden Docker socket is present: {socket}")

    podman_version = _capture(["podman", "--version"])
    compose_version = _capture(["podman-compose", "--version"])
    if "4.9.3" not in podman_version:
        raise ServiceRunnerSmokeError(f"unexpected Podman version: {podman_version}")
    if "1.0.6" not in compose_version:
        raise ServiceRunnerSmokeError(f"unexpected podman-compose version: {compose_version}")
    rootless = _capture(["podman", "info", "--format", "{{.Host.Security.Rootless}}"])
    if rootless != "true":
        raise ServiceRunnerSmokeError(f"Podman is not rootless: {rootless!r}")


def _safe_work_dir(path: Path, *, create: bool) -> Path:
    path = path.expanduser()
    if path.exists() and path.is_symlink():
        raise ServiceRunnerSmokeError(f"service canary work directory is a symlink: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_dir():
        raise ServiceRunnerSmokeError(f"service canary work path is not a directory: {path}")
    return path.resolve()


def write_compose_file(work_dir: Path) -> Path:
    """Create the fixed Compose fixture in a run-owned non-symlink directory."""

    root = _safe_work_dir(work_dir, create=True)
    compose_path = root / "compose.yml"
    if compose_path.exists() and compose_path.is_symlink():
        raise ServiceRunnerSmokeError(f"service canary Compose path is a symlink: {compose_path}")
    compose_path.write_text(compose_document(), encoding="utf-8")
    return compose_path


def _compose_prefix(compose_path: Path, project_name: str) -> list[str]:
    return [
        "podman-compose",
        "-f",
        str(compose_path),
        "-p",
        validate_project_name(project_name),
    ]


def _project_filter(project_name: str) -> str:
    return f"label={_PROJECT_LABEL}={validate_project_name(project_name)}"


def _wait_for_client(client_id: str, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        probe = _run(
            ["podman", "exec", client_id, "test", "-f", "/tmp/client-ready"],
            check=False,
        )
        if probe.returncode == 0:
            ready = _capture(
                ["podman", "exec", client_id, "wget", "-qO-", "http://backend:8080/ready"]
            )
            if ready == "backend-ready":
                return
        time.sleep(1)
    raise ServiceRunnerSmokeError("client did not observe backend readiness before timeout")


def run_smoke(*, project_name: str, work_dir: Path) -> None:
    """Start the fixed two-service fixture and prove cross-service readiness."""

    compose_path = write_compose_file(work_dir)
    prefix = _compose_prefix(compose_path, project_name)
    _run([*prefix, "up", "-d"])

    backend_id = _capture([*prefix, "ps", "-q", "backend"])
    client_id = _capture([*prefix, "ps", "-q", "client"])
    if not backend_id or not client_id:
        raise ServiceRunnerSmokeError("Compose did not create both service containers")
    _wait_for_client(client_id)

    volumes = _capture(["podman", "volume", "ls", "-q", "--filter", _project_filter(project_name)])
    if not volumes:
        raise ServiceRunnerSmokeError("Compose fixture did not create its run-owned volume")


def _residual_resources(project_name: str) -> dict[str, str]:
    project_filter = _project_filter(project_name)
    return {
        "containers": _capture(["podman", "ps", "-aq", "--filter", project_filter]),
        "pods": _capture(["podman", "pod", "ps", "-q", "--filter", project_filter]),
        "volumes": _capture(["podman", "volume", "ls", "-q", "--filter", project_filter]),
    }


def cleanup_smoke(*, project_name: str, work_dir: Path) -> None:
    """Remove only the current run's Compose resources and prove zero residue."""

    validate_project_name(project_name)
    root = _safe_work_dir(work_dir, create=False)
    compose_path = root / "compose.yml"
    if compose_path.exists():
        _run([*_compose_prefix(compose_path, project_name), "down", "--volumes", "--remove-orphans"])

    residual = {kind: value for kind, value in _residual_resources(project_name).items() if value}
    if residual:
        raise ServiceRunnerSmokeError(f"service canary left run-owned resources: {residual}")
    if root.exists():
        shutil.rmtree(root)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-runtime")
    for name in ("run", "cleanup"):
        command = sub.add_parser(name)
        command.add_argument("--project-name", required=True)
        command.add_argument("--work-dir", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "verify-runtime":
        verify_runtime_boundary()
    elif args.command == "run":
        run_smoke(project_name=args.project_name, work_dir=args.work_dir)
    else:
        cleanup_smoke(project_name=args.project_name, work_dir=args.work_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
