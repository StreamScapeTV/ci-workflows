"""Install the exact locked validation parser without pip or build tooling."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import platform
import re
import shutil
import stat
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_DOWNLOAD_HOST = "files.pythonhosted.org"
_SUPPORTED_LINUX_PYTHON = (3, 12)
_SUPPORTED_MACOS_PYTHON = (3, 12)
MAX_ARCHIVE_MEMBERS = 1024
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024


class ValidationRuntimeError(RuntimeError):
    """Raised when the locked parser runtime cannot be installed safely."""


@dataclass(frozen=True)
class LockedArtifact:
    package: str
    version: str
    runtime: str
    filename: str
    url: str
    sha256: str
    format: str


WheelArtifact = LockedArtifact


def _normalize_machine(machine: str) -> str:
    value = machine.strip().lower()
    if value in {"amd64", "x64", "x86_64"}:
        return "x86_64"
    if value in {"aarch64", "arm64"}:
        return "arm64"
    return value


def detect_runtime(
    *,
    implementation: str | None = None,
    system: str | None = None,
    machine: str | None = None,
    version: tuple[int, ...] | None = None,
) -> str:
    """Return the exact locked artifact key for a supported host."""

    implementation = implementation or getattr(sys.implementation, "name", "")
    if implementation != "cpython":
        raise ValidationRuntimeError(
            f"unsupported Python implementation {implementation!r}; CPython is required"
        )

    normalized_system = (system or platform.system()).strip().lower()
    normalized_machine = _normalize_machine(machine or platform.machine())
    actual_version = tuple(version or sys.version_info[:3])

    if normalized_system == "linux":
        if actual_version[:2] != _SUPPORTED_LINUX_PYTHON:
            raise ValidationRuntimeError(
                "unsupported Python version "
                f"{'.'.join(str(part) for part in actual_version)} for Linux; "
                "expected CPython 3.12"
            )
        if normalized_machine != "x86_64":
            raise ValidationRuntimeError(
                f"unsupported validation architecture {normalized_machine!r} for Linux"
            )
        return "cp312-manylinux-x86_64"

    if normalized_system == "darwin":
        if actual_version[:2] != _SUPPORTED_MACOS_PYTHON:
            raise ValidationRuntimeError(
                "unsupported Python version "
                f"{'.'.join(str(part) for part in actual_version)} for Darwin; "
                "expected CPython 3.12"
            )
        if normalized_machine not in {"x86_64", "arm64"}:
            raise ValidationRuntimeError(
                f"unsupported validation architecture {normalized_machine!r} for Darwin"
            )
        return f"cp312-macos-{normalized_machine}"

    raise ValidationRuntimeError(
        f"unsupported validation operating system {normalized_system!r}; "
        "expected Linux or Darwin"
    )


def _read_lock(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationRuntimeError(
            f"cannot read validation lock {path}: {error}"
        ) from error
    if not isinstance(payload, Mapping):
        raise ValidationRuntimeError("validation lock root must be an object")
    return payload


def _pyyaml_package(lock_path: Path) -> Mapping[str, Any]:
    payload = _read_lock(lock_path)
    python = payload.get("python", {})
    packages = python.get("packages", []) if isinstance(python, Mapping) else []
    if not isinstance(packages, list):
        raise ValidationRuntimeError("python.packages must be a list")
    matches = [
        package
        for package in packages
        if isinstance(package, Mapping) and package.get("name") == "PyYAML"
    ]
    if len(matches) != 1:
        raise ValidationRuntimeError(
            "validation lock must define exactly one PyYAML package"
        )
    return matches[0]


def _validate_artifact(artifact: LockedArtifact) -> None:
    parsed = urllib.parse.urlparse(artifact.url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _ALLOWED_DOWNLOAD_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationRuntimeError(
            f"artifact URL must use plain https://{_ALLOWED_DOWNLOAD_HOST}"
        )
    if Path(parsed.path).name != artifact.filename:
        raise ValidationRuntimeError(
            "artifact URL filename differs from the locked filename"
        )
    if not _SHA256_RE.fullmatch(artifact.sha256):
        raise ValidationRuntimeError(
            "locked artifact must include a lowercase SHA-256 digest"
        )
    if artifact.package != "PyYAML" or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+)+", artifact.version
    ):
        raise ValidationRuntimeError(
            "locked parser package identity or version is invalid"
        )

    if artifact.format == "wheel":
        if not artifact.filename.endswith(".whl"):
            raise ValidationRuntimeError("locked wheel filename must end in .whl")
        expected_tag = artifact.runtime.split("-", 1)[0]
        if f"-{expected_tag}-{expected_tag}-" not in artifact.filename:
            raise ValidationRuntimeError(
                "wheel filename is not compatible with locked runtime "
                f"{artifact.runtime!r}"
            )
        return

    if artifact.format == "sdist-tar-gz":
        if artifact.filename != f"pyyaml-{artifact.version}.tar.gz":
            raise ValidationRuntimeError(
                "source filename does not match the locked PyYAML package version"
            )
        return

    raise ValidationRuntimeError(
        f"unsupported locked artifact format {artifact.format!r}"
    )


def select_artifact(
    lock_path: Path, runtime: str | None = None
) -> LockedArtifact:
    """Select and validate the immutable PyYAML artifact for ``runtime``."""

    runtime = runtime or detect_runtime()
    package = _pyyaml_package(lock_path)
    version = str(package.get("version", ""))

    wheels = package.get("wheels", [])
    if not isinstance(wheels, list):
        raise ValidationRuntimeError("PyYAML wheels must be a list")
    for wheel in wheels:
        if not isinstance(wheel, Mapping) or wheel.get("runtime") != runtime:
            continue
        artifact = LockedArtifact(
            package="PyYAML",
            version=version,
            runtime=runtime,
            filename=str(wheel.get("filename", "")),
            url=str(wheel.get("url", "")),
            sha256=str(wheel.get("sha256", "")),
            format="wheel",
        )
        _validate_artifact(artifact)
        return artifact

    source = package.get("source")
    if isinstance(source, Mapping):
        runtimes = source.get("runtimes", [])
        if not isinstance(runtimes, list) or not all(
            isinstance(value, str) for value in runtimes
        ):
            raise ValidationRuntimeError(
                "PyYAML source runtimes must be a string list"
            )
        if runtime in runtimes:
            artifact = LockedArtifact(
                package="PyYAML",
                version=version,
                runtime=runtime,
                filename=str(source.get("filename", "")),
                url=str(source.get("url", "")),
                sha256=str(source.get("sha256", "")),
                format=str(source.get("format", "")),
            )
            _validate_artifact(artifact)
            if str(package.get("sha256", "")) != artifact.sha256:
                raise ValidationRuntimeError(
                    "PyYAML source digest differs between package and source lock fields"
                )
            return artifact

    raise ValidationRuntimeError(
        f"no locked PyYAML artifact for runtime {runtime!r}"
    )


def select_wheel(
    lock_path: Path, runtime: str | None = None
) -> WheelArtifact:
    """Select the retained Linux wheel and reject non-wheel runtimes."""

    artifact = select_artifact(lock_path, runtime)
    if artifact.format != "wheel":
        raise ValidationRuntimeError(
            f"locked runtime {artifact.runtime!r} does not select a wheel"
        )
    return artifact


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "StreamScapeTV-ci-workflows-validation-bootstrap/3"
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.geturl() != url:
                raise ValidationRuntimeError(
                    "locked parser download redirected away from the exact approved URL"
                )
            payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    except ValidationRuntimeError:
        raise
    except OSError as error:
        raise ValidationRuntimeError(
            f"cannot download locked parser artifact: {error}"
        ) from error
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise ValidationRuntimeError(
            "locked parser download exceeds the compressed-size bound"
        )
    return payload


def _safe_member_path(
    name: str, *, archive: str, directory: bool = False
) -> PurePosixPath:
    if not name or "\\" in name:
        raise ValidationRuntimeError(f"unsafe {archive} member path {name!r}")
    raw = name[:-1] if directory and name.endswith("/") else name
    if not raw or raw.startswith("/"):
        raise ValidationRuntimeError(f"unsafe {archive} member path {name!r}")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValidationRuntimeError(f"unsafe {archive} member path {name!r}")
    if parts[0].endswith(":"):
        raise ValidationRuntimeError(f"unsafe {archive} member path {name!r}")
    path = PurePosixPath(*parts)
    if path.is_absolute():
        raise ValidationRuntimeError(f"unsafe {archive} member path {name!r}")
    return path


def _bounded_members(
    members: Sequence[Any], *, archive: str, size: Callable[[Any], int]
) -> None:
    if not members:
        raise ValidationRuntimeError(f"locked parser {archive} is empty")
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValidationRuntimeError(
            f"locked parser {archive} exceeds the member-count bound"
        )
    expanded = 0
    for member in members:
        member_size = size(member)
        if member_size < 0:
            raise ValidationRuntimeError(
                f"{archive} member has a negative size"
            )
        expanded += member_size
        if expanded > MAX_EXPANDED_BYTES:
            raise ValidationRuntimeError(
                f"locked parser {archive} exceeds the expanded-size bound"
            )


def _reject_file_directory_collisions(
    destinations: Mapping[PurePosixPath, str], *, archive: str
) -> None:
    files = {
        path for path, kind in destinations.items() if kind == "file"
    }
    for destination in destinations:
        parent = destination.parent
        while parent != PurePosixPath("."):
            if parent in files:
                raise ValidationRuntimeError(
                    f"{archive} file collides with a destination directory"
                )
            parent = parent.parent


def _install_wheel(payload: bytes, target: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise ValidationRuntimeError(
            "locked parser artifact is not a valid wheel"
        ) from error

    with archive:
        members = archive.infolist()
        _bounded_members(
            members,
            archive="wheel",
            size=lambda member: int(member.file_size),
        )
        destinations: dict[PurePosixPath, str] = {}
        keys: set[str] = set()
        for member in members:
            kind = "directory" if member.is_dir() else "file"
            mode = stat.S_IFMT(member.external_attr >> 16)
            if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValidationRuntimeError(
                    f"wheel member type is forbidden: {member.filename!r}"
                )
            path = _safe_member_path(
                member.filename,
                archive="wheel",
                directory=member.is_dir(),
            )
            key = path.as_posix().casefold()
            if key in keys:
                raise ValidationRuntimeError(
                    f"duplicate wheel destination {path.as_posix()!r}"
                )
            keys.add(key)
            destinations[path] = kind
        _reject_file_directory_collisions(destinations, archive="wheel")

        for member in members:
            path = _safe_member_path(
                member.filename,
                archive="wheel",
                directory=member.is_dir(),
            )
            destination = target.joinpath(*path.parts)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            data = archive.read(member)
            if len(data) != member.file_size:
                raise ValidationRuntimeError(
                    f"wheel member size differs from header: {member.filename!r}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


def _classify_tar_member(member: tarfile.TarInfo) -> str:
    if member.issym():
        return "symlink"
    if member.islnk():
        return "hardlink"
    if member.ischr() or member.isblk():
        return "device"
    if member.isfifo():
        return "fifo"
    if member.type == tarfile.DIRTYPE:
        return "directory"
    if member.type in {tarfile.REGTYPE, tarfile.AREGTYPE}:
        return "file"
    return "unsupported"


def _metadata_value(payload: bytes, field: str) -> str:
    try:
        message = Parser().parsestr(payload.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ValidationRuntimeError("PyYAML PKG-INFO is not UTF-8") from error
    return str(message.get(field, ""))


def _read_bounded_tar_members(
    archive: tarfile.TarFile,
) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    expanded = 0
    while True:
        member = archive.next()
        if member is None:
            break
        members.append(member)
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValidationRuntimeError(
                "locked parser source archive exceeds the member-count bound"
            )
        if member.isfile():
            if member.size < 0:
                raise ValidationRuntimeError(
                    "source archive member has a negative size"
                )
            expanded += int(member.size)
            if expanded > MAX_EXPANDED_BYTES:
                raise ValidationRuntimeError(
                    "locked parser source archive exceeds the expanded-size bound"
                )
    if not members:
        raise ValidationRuntimeError(
            "locked parser source archive is empty"
        )
    return members


def _install_source(
    payload: bytes, target: Path, artifact: LockedArtifact
) -> None:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz")
    except (tarfile.TarError, OSError) as error:
        raise ValidationRuntimeError(
            "locked parser artifact is not a valid gzip tar archive"
        ) from error

    with archive:
        members = _read_bounded_tar_members(archive)
        paths: dict[str, PurePosixPath] = {}
        keys: set[str] = set()
        for member in members:
            kind = _classify_tar_member(member)
            if kind in {
                "symlink",
                "hardlink",
                "device",
                "fifo",
                "unsupported",
            }:
                raise ValidationRuntimeError(
                    f"source archive member type {kind} is forbidden: "
                    f"{member.name!r}"
                )
            path = _safe_member_path(
                member.name,
                archive="source archive",
                directory=kind == "directory",
            )
            key = path.as_posix().casefold()
            if key in keys:
                raise ValidationRuntimeError(
                    f"duplicate source destination {path.as_posix()!r}"
                )
            keys.add(key)
            paths[member.name] = path

        roots = {path.parts[0] for path in paths.values()}
        if len(roots) != 1:
            raise ValidationRuntimeError(
                "source archive root does not match the locked package version"
            )
        root = next(iter(roots))
        if root.lower() != f"pyyaml-{artifact.version}":
            raise ValidationRuntimeError(
                "source archive root does not match the locked package version"
            )

        metadata_name = f"{root}/PKG-INFO"
        metadata_member = next(
            (
                member
                for member in members
                if paths[member.name] == PurePosixPath(metadata_name)
            ),
            None,
        )
        if metadata_member is None or not metadata_member.isfile():
            raise ValidationRuntimeError(
                "source archive is missing regular PKG-INFO"
            )
        metadata_file = archive.extractfile(metadata_member)
        if metadata_file is None:
            raise ValidationRuntimeError(
                "cannot read source archive PKG-INFO"
            )
        metadata = metadata_file.read()
        if _metadata_value(metadata, "Name") != artifact.package:
            raise ValidationRuntimeError(
                "source archive package name is not PyYAML"
            )
        if _metadata_value(metadata, "Version") != artifact.version:
            raise ValidationRuntimeError(
                "source archive package version differs from the lock"
            )

        package_prefix = PurePosixPath(root, "lib", "yaml")
        selected: list[tuple[tarfile.TarInfo, PurePosixPath, str]] = []
        destinations: dict[PurePosixPath, str] = {}
        destination_keys: set[str] = set()
        for member in members:
            path = paths[member.name]
            try:
                relative = path.relative_to(package_prefix)
            except ValueError:
                continue
            destination = PurePosixPath("yaml", *relative.parts)
            kind = _classify_tar_member(member)
            key = destination.as_posix().casefold()
            if key in destination_keys:
                raise ValidationRuntimeError(
                    f"duplicate source destination {destination.as_posix()!r}"
                )
            destination_keys.add(key)
            destinations[destination] = kind
            selected.append((member, destination, kind))

        if destinations.get(PurePosixPath("yaml", "__init__.py")) != "file":
            raise ValidationRuntimeError(
                "locked source archive did not contain yaml/__init__.py"
            )
        _reject_file_directory_collisions(
            destinations, archive="source archive"
        )

        for member, relative, kind in sorted(
            selected,
            key=lambda item: (len(item[1].parts), item[1].as_posix()),
        ):
            destination = target.joinpath(*relative.parts)
            if kind == "directory":
                destination.mkdir(parents=True, exist_ok=True)
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValidationRuntimeError(
                    f"cannot read source archive member {member.name!r}"
                )
            data = extracted.read()
            if len(data) != member.size:
                raise ValidationRuntimeError(
                    f"source member size differs from header: {member.name!r}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


def install_locked_artifact(
    lock_path: Path,
    target: Path,
    *,
    runtime: str | None = None,
    downloader: Callable[[str], bytes] = _download,
) -> LockedArtifact:
    """Download, verify, and safely install the locked parser artifact."""

    artifact = select_artifact(lock_path, runtime)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValidationRuntimeError(
            f"validation target must be a real directory: {target}"
        )
    if target.exists() and any(target.iterdir()):
        raise ValidationRuntimeError(
            f"validation target must be empty: {target}"
        )
    target.mkdir(parents=True, exist_ok=True)

    try:
        payload = downloader(artifact.url)
        digest = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(digest, artifact.sha256):
            raise ValidationRuntimeError(
                "artifact digest mismatch: "
                f"expected {artifact.sha256}, received {digest}"
            )
        if artifact.format == "wheel":
            _install_wheel(payload, target)
        elif artifact.format == "sdist-tar-gz":
            _install_source(payload, target, artifact)
        else:  # pragma: no cover
            raise ValidationRuntimeError(
                f"unsupported locked artifact format {artifact.format!r}"
            )
        if not (target / "yaml/__init__.py").is_file():
            raise ValidationRuntimeError(
                "locked artifact did not install the yaml package"
            )
        return artifact
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def install_locked_wheel(
    lock_path: Path,
    target: Path,
    *,
    runtime: str | None = None,
    downloader: Callable[[str], bytes] = _download,
) -> WheelArtifact:
    """Install the retained Linux wheel for compatibility callers."""

    selected = select_wheel(lock_path, runtime)
    return install_locked_artifact(
        lock_path,
        target,
        runtime=selected.runtime,
        downloader=downloader,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--runtime")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = install_locked_artifact(
            args.lock.resolve(),
            args.target.resolve(),
            runtime=args.runtime,
        )
    except ValidationRuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "artifact": artifact.filename,
                "format": artifact.format,
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
