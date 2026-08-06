"""Install the locked validation parser without pip or virtualenv support."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import platform
import re
import stat
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_DOWNLOAD_HOST = "files.pythonhosted.org"


class ValidationRuntimeError(RuntimeError):
    """Raised when the locked parser runtime cannot be installed safely."""


@dataclass(frozen=True)
class WheelArtifact:
    package: str
    version: str
    runtime: str
    filename: str
    url: str
    sha256: str


def detect_runtime() -> str:
    """Return the one lock key supported by the current Python runtime."""

    implementation = getattr(sys.implementation, "name", "")
    if implementation != "cpython":
        raise ValidationRuntimeError(
            f"unsupported Python implementation {implementation!r}; CPython is required"
        )
    machine = platform.machine().lower()
    if machine in {"amd64", "x64"}:
        machine = "x86_64"
    if sys.platform != "linux" or machine != "x86_64":
        raise ValidationRuntimeError(
            f"unsupported validation host {sys.platform}/{machine}; expected linux/x86_64"
        )
    major, minor = sys.version_info[:2]
    return f"cp{major}{minor}-manylinux-x86_64"


def _read_lock(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationRuntimeError(f"cannot read validation lock {path}: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValidationRuntimeError("validation lock root must be an object")
    return payload


def select_wheel(lock_path: Path, runtime: str | None = None) -> WheelArtifact:
    """Select and validate the exact wheel approved for ``runtime``."""

    runtime = runtime or detect_runtime()
    payload = _read_lock(lock_path)
    python = payload.get("python", {})
    packages = python.get("packages", []) if isinstance(python, Mapping) else []
    if not isinstance(packages, list):
        raise ValidationRuntimeError("python.packages must be a list")
    for package in packages:
        if not isinstance(package, Mapping) or package.get("name") != "PyYAML":
            continue
        wheels = package.get("wheels", [])
        if not isinstance(wheels, list):
            raise ValidationRuntimeError("PyYAML wheels must be a list")
        for wheel in wheels:
            if not isinstance(wheel, Mapping) or wheel.get("runtime") != runtime:
                continue
            artifact = WheelArtifact(
                package="PyYAML",
                version=str(package.get("version", "")),
                runtime=runtime,
                filename=str(wheel.get("filename", "")),
                url=str(wheel.get("url", "")),
                sha256=str(wheel.get("sha256", "")),
            )
            _validate_artifact(artifact)
            return artifact
    raise ValidationRuntimeError(f"no locked PyYAML wheel for runtime {runtime!r}")


def _validate_artifact(artifact: WheelArtifact) -> None:
    parsed = urllib.parse.urlparse(artifact.url)
    if parsed.scheme != "https" or parsed.hostname != _ALLOWED_DOWNLOAD_HOST:
        raise ValidationRuntimeError(
            f"wheel URL must use https://{_ALLOWED_DOWNLOAD_HOST}"
        )
    if Path(parsed.path).name != artifact.filename:
        raise ValidationRuntimeError("wheel URL filename differs from the locked filename")
    if not artifact.filename.endswith(".whl"):
        raise ValidationRuntimeError("locked parser artifact must be a wheel")
    if not _SHA256_RE.fullmatch(artifact.sha256):
        raise ValidationRuntimeError("locked wheel must include a lowercase SHA-256 digest")
    expected_tag = artifact.runtime.split("-", 1)[0]
    if f"-{expected_tag}-{expected_tag}-" not in artifact.filename:
        raise ValidationRuntimeError(
            f"wheel filename is not compatible with locked runtime {artifact.runtime!r}"
        )


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "StreamScapeTV-ci-workflows-validation-bootstrap/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except OSError as error:
        raise ValidationRuntimeError(f"cannot download locked parser wheel: {error}") from error


def _validate_member(member: zipfile.ZipInfo) -> None:
    path = PurePosixPath(member.filename)
    if path.is_absolute() or ".." in path.parts:
        raise ValidationRuntimeError(f"unsafe wheel member path {member.filename!r}")
    if stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK:
        raise ValidationRuntimeError(f"wheel member may not be a symlink: {member.filename!r}")


def install_locked_wheel(
    lock_path: Path,
    target: Path,
    *,
    runtime: str | None = None,
    downloader: Callable[[str], bytes] = _download,
) -> WheelArtifact:
    """Download, verify, and safely extract the locked parser wheel."""

    artifact = select_wheel(lock_path, runtime)
    if target.exists() and any(target.iterdir()):
        raise ValidationRuntimeError(f"validation target must be empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    payload = downloader(artifact.url)
    digest = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(digest, artifact.sha256):
        raise ValidationRuntimeError(
            f"wheel digest mismatch: expected {artifact.sha256}, received {digest}"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if not members:
                raise ValidationRuntimeError("locked parser wheel is empty")
            for member in members:
                _validate_member(member)
            archive.extractall(target)
    except zipfile.BadZipFile as error:
        raise ValidationRuntimeError("locked parser artifact is not a valid wheel") from error
    if not (target / "yaml/__init__.py").is_file():
        raise ValidationRuntimeError("locked wheel did not install the yaml package")
    return artifact


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--runtime")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = install_locked_wheel(
            args.lock.resolve(), args.target.resolve(), runtime=args.runtime
        )
    except ValidationRuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "artifact": artifact.filename,
                "package": artifact.package,
                "runtime": artifact.runtime,
                "sha256": artifact.sha256,
                "status": "installed",
                "target": str(args.target.resolve()),
                "version": artifact.version,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
