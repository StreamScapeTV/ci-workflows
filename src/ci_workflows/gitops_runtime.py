"""Verified tools, bounded subprocesses, and cleanup for GitOps validation."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .gitops_types import (
    GitOpsPlan,
    GitOpsToolPin,
    GitOpsValidationError,
)

_MARKER = ".gitops-validation-state.json"
_TOKEN_LIKE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"github_pat_[A-Za-z0-9_]{30,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.)"
)
_FORBIDDEN_POLICY_ENV = (
    "KUBECONFIG",
    "SOPS_AGE_KEY",
    "SOPS_AGE_KEY_FILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CLIENT_SECRET",
    "REGISTRY_TOKEN",
)


@dataclass(frozen=True, slots=True)
class GitOpsTools:
    """Exact runtime identities used by one execution."""

    yaml: Any
    binaries: Mapping[str, Path]
    versions: Mapping[str, str]


def _fail(code: str, detail: str = "") -> None:
    raise GitOpsValidationError(code, detail)


def _require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        _fail(code, detail)


def _bounded_output(value: bytes, maximum: int, code: str) -> str:
    _require(len(value) <= maximum, code, "output too large")
    text = value.decode("utf-8", errors="replace")
    _require(
        _TOKEN_LIKE.search(text) is None,
        code,
        "sensitive output rejected",
    )
    return text


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
    max_output: int,
    code: str,
) -> str:
    _require(
        bool(argv)
        and all(isinstance(value, str) and value for value in argv),
        code,
    )
    try:
        process = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitOpsValidationError(code) from error
    output = _bounded_output(process.stdout, max_output, code)
    if process.returncode != 0:
        summary = " ".join(output.splitlines()[-8:])[:400]
        _fail(code, summary)
    return output


def _state_marker(state_root: Path) -> Path:
    return state_root / _MARKER


def _ensure_no_follow_directory(
    path: Path,
    *,
    code: str,
    detail: str,
) -> None:
    """Create a state directory only through verified directory ancestors."""

    missing: list[Path] = []
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing.append(current)
            parent = current.parent
            _require(parent != current, code, detail)
            current = parent
            continue
        _require(
            stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),
            code,
            detail,
        )
        break
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            pass
        try:
            info = directory.lstat()
        except OSError as error:
            raise GitOpsValidationError(code, detail) from error
        _require(
            stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),
            code,
            detail,
        )


def _open_new_file_no_follow(
    path: Path,
    *,
    code: str,
    detail: str,
):
    """Open a new regular state file without following an existing link."""

    _ensure_no_follow_directory(path.parent, code=code, detail=detail)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise GitOpsValidationError(code, detail) from error
    else:
        _fail(code, detail)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    _require(no_follow is not None, code, detail)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise GitOpsValidationError(code, detail) from error
    return os.fdopen(descriptor, "wb")


def initialize_gitops_state(state_root: Path) -> None:
    """Create one marker-bound runtime root without following a symlink."""

    _require(state_root.is_absolute(), "cleanup_failed")
    _require(not state_root.is_symlink(), "cleanup_failed")
    state_root.mkdir(parents=True, exist_ok=True)
    _require(
        state_root.is_dir() and not state_root.is_symlink(),
        "cleanup_failed",
    )
    marker = _state_marker(state_root)
    payload = {
        "schema_version": 1,
        "root": str(state_root.resolve()),
    }
    marker.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_marker(state_root: Path) -> None:
    marker = _state_marker(state_root)
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GitOpsValidationError("cleanup_failed") from error
    _require(
        payload
        == {
            "schema_version": 1,
            "root": str(state_root.resolve()),
        },
        "cleanup_failed",
    )


def _remove_no_follow(path: Path) -> None:
    """Delete a registered tree with lstat and no symlink traversal."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        path.unlink()
        return
    for child in list(path.iterdir()):
        _remove_no_follow(child)
    path.rmdir()


def cleanup_gitops_state(state_root: Path) -> None:
    """Remove exactly one marker-bound issue-owned state root."""

    if not state_root.exists() and not state_root.is_symlink():
        return
    _require(
        state_root.is_absolute() and not state_root.is_symlink(),
        "cleanup_failed",
    )
    _validate_marker(state_root)
    try:
        _remove_no_follow(state_root)
    except OSError as error:
        raise GitOpsValidationError("cleanup_failed") from error
    _require(
        not state_root.exists() and not state_root.is_symlink(),
        "cleanup_failed",
    )


def assert_zero_gitops_residue(state_root: Path) -> None:
    _require(
        not state_root.exists() and not state_root.is_symlink(),
        "cleanup_failed",
    )


def _download(pin: GitOpsToolPin, destination: Path) -> None:
    def validate_url(url: str) -> None:
        parsed = urlparse(url)
        _require(
            parsed.scheme == "https"
            and parsed.hostname in pin.allowed_hosts
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443},
            "tool_download_failed",
            pin.name,
        )

    class PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(  # type: ignore[override]
            self,
            request: urllib.request.Request,
            file_pointer: object,
            code: int,
            message: str,
            headers: object,
            redirect_url: str,
        ) -> urllib.request.Request | None:
            validate_url(redirect_url)
            return super().redirect_request(
                request,
                file_pointer,
                code,
                message,
                headers,
                redirect_url,
            )

    validate_url(pin.url)
    request = urllib.request.Request(
        pin.url,
        headers={"User-Agent": "StreamScapeTV-ci-workflows-gitops/1.0"},
    )
    try:
        opener = urllib.request.build_opener(PinnedRedirectHandler())
        with _open_new_file_no_follow(
            destination,
            code="tool_download_failed",
            detail=pin.name,
        ) as output:
            with opener.open(request, timeout=60) as response:
                validate_url(response.geturl())
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    _require(
                        size <= pin.max_bytes,
                        "tool_download_failed",
                        pin.name,
                    )
                    digest.update(chunk)
                    output.write(chunk)
    except (
        OSError,
        urllib.error.URLError,
        GitOpsValidationError,
    ) as error:
        if isinstance(error, GitOpsValidationError):
            raise
        raise GitOpsValidationError(
            "tool_download_failed",
            pin.name,
        ) from error
    _require(
        digest.hexdigest() == pin.sha256,
        "tool_digest_mismatch",
        pin.name,
    )


def _safe_tar_member(
    archive: Path,
    pin: GitOpsToolPin,
    destination: Path,
) -> Path:
    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            _require(
                0 < len(members) <= 128,
                "tool_archive_rejected",
                pin.name,
            )
            seen: set[str] = set()
            selected: tarfile.TarInfo | None = None
            total = 0
            for member in members:
                name = member.name.replace("\\", "/")
                pure = PurePosixPath(name)
                _require(
                    name not in seen
                    and not pure.is_absolute()
                    and all(
                        part not in {"", ".", ".."}
                        for part in pure.parts
                    ),
                    "tool_archive_rejected",
                    pin.name,
                )
                seen.add(name)
                _require(
                    member.isfile() or member.isdir(),
                    "tool_archive_rejected",
                    pin.name,
                )
                total += max(member.size, 0)
                _require(
                    total <= pin.max_unpacked_bytes,
                    "tool_archive_rejected",
                    pin.name,
                )
                if name == pin.archive_member:
                    selected = member
            _require(
                selected is not None and selected.isfile(),
                "tool_archive_rejected",
                pin.name,
            )
            stream = handle.extractfile(selected)
            _require(
                stream is not None,
                "tool_archive_rejected",
                pin.name,
            )
            data = stream.read(pin.max_unpacked_bytes + 1)
            _require(
                len(data) <= pin.max_unpacked_bytes,
                "tool_archive_rejected",
                pin.name,
            )
    except (OSError, tarfile.TarError) as error:
        raise GitOpsValidationError(
            "tool_archive_rejected",
            pin.name,
        ) from error
    with _open_new_file_no_follow(
        destination,
        code="tool_archive_rejected",
        detail=pin.name,
    ) as output:
        output.write(data)
    destination.chmod(0o755)
    return destination


def _safe_wheel(
    archive: Path,
    pin: GitOpsToolPin,
    destination: Path,
) -> None:
    _ensure_no_follow_directory(
        destination,
        code="tool_archive_rejected",
        detail=pin.name,
    )
    try:
        with zipfile.ZipFile(archive) as handle:
            members = handle.infolist()
            _require(
                0 < len(members) <= 512,
                "tool_archive_rejected",
                pin.name,
            )
            seen: set[str] = set()
            total = 0
            for member in members:
                name = member.filename.replace("\\", "/")
                pure = PurePosixPath(name)
                _require(
                    name not in seen
                    and not pure.is_absolute()
                    and all(
                        part not in {"", ".", ".."}
                        for part in pure.parts
                    ),
                    "tool_archive_rejected",
                    pin.name,
                )
                seen.add(name)
                mode = (member.external_attr >> 16) & 0o170000
                _require(
                    mode
                    not in {
                        stat.S_IFLNK,
                        stat.S_IFCHR,
                        stat.S_IFBLK,
                        stat.S_IFIFO,
                    },
                    "tool_archive_rejected",
                    pin.name,
                )
                total += member.file_size
                _require(
                    total <= pin.max_unpacked_bytes,
                    "tool_archive_rejected",
                    pin.name,
                )
            _require(
                pin.archive_member in seen,
                "tool_archive_rejected",
                pin.name,
            )
            for member in members:
                if member.is_dir():
                    _ensure_no_follow_directory(
                        destination.joinpath(
                            *PurePosixPath(member.filename).parts
                        ),
                        code="tool_archive_rejected",
                        detail=pin.name,
                    )
                    continue
                target = destination.joinpath(
                    *PurePosixPath(member.filename).parts
                )
                with _open_new_file_no_follow(
                    target,
                    code="tool_archive_rejected",
                    detail=pin.name,
                ) as output:
                    output.write(handle.read(member))
    except (OSError, zipfile.BadZipFile) as error:
        raise GitOpsValidationError(
            "tool_archive_rejected",
            pin.name,
        ) from error


def _load_pinned_yaml(root: Path, expected_version: str) -> Any:
    root = root.resolve()
    original = list(sys.path)
    old_modules = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "yaml"
        or name.startswith("yaml.")
        or name == "_yaml"
    }
    try:
        sys.path.insert(0, str(root))
        yaml = importlib.import_module("yaml")
        _require(
            getattr(yaml, "__version__", "") == expected_version,
            "tool_identity_mismatch",
            "pyyaml",
        )
        module_path = Path(yaml.__file__).resolve()
        _require(
            root in module_path.parents,
            "tool_identity_mismatch",
            "pyyaml",
        )
        return yaml
    except Exception:
        for name in list(sys.modules):
            if (
                name == "yaml"
                or name.startswith("yaml.")
                or name == "_yaml"
            ):
                sys.modules.pop(name, None)
        sys.modules.update(old_modules)
        raise
    finally:
        sys.path[:] = original


def prepare_gitops_tools(
    plan: GitOpsPlan,
    state_root: Path,
) -> GitOpsTools:
    """Download, verify, safely install, and identify exact pinned tools."""

    initialize_gitops_state(state_root)
    archives = state_root / "archives"
    install = state_root / "install"
    binaries: dict[str, Path] = {}
    versions: dict[str, str] = {}
    yaml_module: Any | None = None
    environment = _tool_environment(state_root)
    for pin in plan.tools:
        suffix = ".whl" if pin.name == "pyyaml" else ".tar.gz"
        archive = archives / f"{pin.name}-{pin.version}{suffix}"
        _download(pin, archive)
        if pin.name == "pyyaml":
            package_root = install / "python"
            _safe_wheel(archive, pin, package_root)
            yaml_module = _load_pinned_yaml(
                package_root,
                pin.version,
            )
            versions[pin.name] = pin.version
            continue
        binary = install / "bin" / pin.name
        _safe_tar_member(archive, pin, binary)
        output = _run(
            (str(binary), *pin.version_args),
            cwd=state_root,
            environment=environment,
            timeout=30,
            max_output=16384,
            code="tool_identity_mismatch",
        )
        _require(
            re.search(pin.version_pattern, output) is not None,
            "tool_identity_mismatch",
            pin.name,
        )
        binaries[pin.name] = binary
        versions[pin.name] = pin.version
    _require(
        yaml_module is not None,
        "tool_identity_mismatch",
        "pyyaml",
    )
    return GitOpsTools(
        yaml=yaml_module,
        binaries=binaries,
        versions=versions,
    )


def _tool_environment(state_root: Path) -> dict[str, str]:
    home = state_root / "home"
    cache = state_root / "cache"
    tmp = state_root / "tmp"
    for path in (home, cache, tmp):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "XDG_CACHE_HOME": str(cache),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }
