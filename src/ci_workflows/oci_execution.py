"""Engine-neutral execution with one reviewed daemonless Buildah adapter."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence, TypeVar
from urllib.parse import urlsplit

from .oci_contract import bounded_path, load_contract, metadata_labels
from . import oci_base_inspection
from .foundation_types import FoundationError
from .oci_input_contract import (
    OciBaseLock,
    OciTargetInputLock,
    load_input_lock_contract,
    validate_target_dockerfile_lock,
)
from .oci_input_download import OciInputDownloadRequest, download_oci_input
from .oci_registry_download import (
    OciRegistryAcquisitionError,
    OciRegistryAcquisitionRequest,
    acquire_oci_base,
)
from .oci_types import (
    OciBuildInputEvidence,
    OciBuildError,
    OciBuildPlan,
    OciBuildResult,
    OciInputPolicy,
    OciPlatformResult,
    OciResolvedBase,
    OciResolvedBasePlatform,
    OciResolvedExternalInput,
    OciTarget,
    OciTargetResult,
    oci_build_evidence_id,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FROM = re.compile(
    r"^\s*FROM(?:\s+--platform=[^\s]+)?\s+([^\s]+)"
    r"(?:\s+AS\s+([^\s]+))?\s*$",
    re.I,
)
_PLATFORM = re.compile(r"^linux/(?:amd64|arm64/v8)$")
_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
_LAYER_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
    }
)
_MAXIMUM_LAYERS = 256
_SAFE_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SENSITIVE_ENVIRONMENT = re.compile(
    r"(?:AUTH|TOKEN|PASSWORD|PASSWD|SECRET|CREDENTIAL|COOKIE|PROXY|DOCKER)",
    re.IGNORECASE,
)
_SAFE_RUNTIME_ENVIRONMENT = frozenset(
    {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR"}
)
_LIBC = ctypes.CDLL(None, use_errno=True)
_MOUNT = getattr(_LIBC, "mount", None)
if _MOUNT is not None:
    _MOUNT.argtypes = (
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    )
    _MOUNT.restype = ctypes.c_int
_MOUNT_BIND = 4096
_MOUNT_REC = 16384
_MOUNT_PRIVATE = 1 << 18
_CAPACITY_MARKER = ".ciw-capacity-root.json"
_CAPACITY_PROFILES = frozenset(
    {
        ("oci-build", "ciw-oci"),
        ("oci-publish", "ciw-oci-publish"),
    }
)


@dataclass(frozen=True)
class CapacityRoots:
    """Internal fixed-capacity allocation for one OCI operation.

    Production callers can select only a reviewed domain/prefix pair.  Path
    parents are fixed here rather than accepted from action inputs or ambient
    environment.  Tests inject a complete immutable instance with
    ``production=False``; production never derives capacity paths from that
    seam.
    """

    domain: str
    prefix: str
    token: str
    scratch_parent: Path
    graph_parent: Path
    run_parent: Path
    production: bool

    def __post_init__(self) -> None:
        if (self.domain, self.prefix) not in _CAPACITY_PROFILES:
            raise OciBuildError("capacity_identity_invalid")
        if re.fullmatch(r"[0-9a-f]{20}", self.token) is None:
            raise OciBuildError("capacity_identity_invalid")
        for parent in (self.scratch_parent, self.graph_parent, self.run_parent):
            if not parent.is_absolute() or parent.name in {"", ".", ".."}:
                raise OciBuildError("capacity_root_invalid")
        if self.production and (
            self.scratch_parent != Path("/var/tmp/buildah")
            or self.graph_parent != Path("/var/lib/containers/storage")
            or self.run_parent != Path("/run/containers/storage")
        ):
            raise OciBuildError("capacity_root_invalid")

    @property
    def leaf_name(self) -> str:
        return f"{self.prefix}-{self.token}"

    @property
    def scratch_root(self) -> Path:
        return self.scratch_parent / self.leaf_name

    @property
    def graph_root(self) -> Path:
        return self.graph_parent / self.leaf_name

    @property
    def run_root(self) -> Path:
        return self.run_parent / self.leaf_name

    @property
    def roots(self) -> tuple[Path, Path, Path]:
        return self.scratch_root, self.graph_root, self.run_root


@dataclass(frozen=True)
class DirectoryIdentity:
    device: int
    inode: int
    mount_id: int

    def to_dict(self) -> dict[str, int]:
        return {
            "device": self.device,
            "inode": self.inode,
            "mount_id": self.mount_id,
        }


@dataclass(frozen=True)
class MaterializedTargetInputs:
    lock: OciTargetInputLock
    evidence: OciBuildInputEvidence
    image_ids_by_platform: Mapping[str, Mapping[str, str]]


def build_capacity_roots(
    environment: Mapping[str, str],
    *,
    domain: str = "oci-build",
    prefix: str = "ciw-oci",
) -> CapacityRoots:
    """Resolve one production allocation without accepting path authority."""

    if (domain, prefix) not in _CAPACITY_PROFILES:
        raise OciBuildError("capacity_identity_invalid")
    repository = environment.get("GITHUB_REPOSITORY", "")
    run_id = environment.get("GITHUB_RUN_ID", "")
    run_attempt = environment.get("GITHUB_RUN_ATTEMPT", "")
    job = environment.get("GITHUB_JOB", "")
    if (
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
        or re.fullmatch(r"[1-9][0-9]*", run_id) is None
        or re.fullmatch(r"[1-9][0-9]*", run_attempt) is None
        or re.fullmatch(r"[A-Za-z0-9_.-]+", job) is None
    ):
        raise OciBuildError("capacity_identity_invalid")
    identity = {
        "domain": domain,
        "repository": repository,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "job": job,
    }
    token = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    return CapacityRoots(
        domain=domain,
        prefix=prefix,
        token=token,
        scratch_parent=Path("/var/tmp/buildah"),
        graph_parent=Path("/var/lib/containers/storage"),
        run_parent=Path("/run/containers/storage"),
        production=True,
    )


def _test_capacity_roots(
    base: Path,
    *,
    domain: str = "oci-build",
    prefix: str = "ciw-oci",
    token: str = "0" * 20,
) -> CapacityRoots:
    """Create test-only parents and return an immutable injected allocation."""

    if not base.is_absolute():
        raise OciBuildError("capacity_root_invalid")
    parents = (
        base / "scratch-capacity",
        base / "graph-capacity",
        base / "run-capacity",
    )
    for parent in parents:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return CapacityRoots(domain, prefix, token, *parents, production=False)


def _decode_mount_path(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _mounted_path_ids(
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> Mapping[Path, int]:
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise OciBuildError("capacity_mount_invalid") from error
    paths: dict[Path, int] = {}
    for line in lines:
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            raise OciBuildError("capacity_mount_invalid")
        try:
            mount_id = int(fields[0])
        except ValueError as error:
            raise OciBuildError("capacity_mount_invalid") from error
        paths[Path(_decode_mount_path(fields[4]))] = mount_id
    return paths


def _mounted_paths(mountinfo: Path = Path("/proc/self/mountinfo")) -> frozenset[Path]:
    return frozenset(_mounted_path_ids(mountinfo))


def _mount_id_for_fd(descriptor: int) -> int:
    """Return a mount identity, including same-filesystem bind boundaries."""

    if sys.platform != "linux":
        # Unit tests on non-Linux hosts inject this seam to model bind mounts.
        # The fallback still confines ordinary cross-filesystem traversal;
        # production is Linux-only and always uses fdinfo's kernel mount ID.
        return os.fstat(descriptor).st_dev
    try:
        lines = Path(f"/proc/self/fdinfo/{descriptor}").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError as error:
        raise OciBuildError("capacity_mount_invalid") from error
    values = [line.removeprefix("mnt_id:").strip() for line in lines if line.startswith("mnt_id:")]
    if len(values) != 1 or re.fullmatch(r"[1-9][0-9]*", values[0]) is None:
        raise OciBuildError("capacity_mount_invalid")
    return int(values[0])


def _directory_identity(descriptor: int) -> DirectoryIdentity:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise OciBuildError("capacity_root_invalid") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise OciBuildError("capacity_root_invalid")
    return DirectoryIdentity(metadata.st_dev, metadata.st_ino, _mount_id_for_fd(descriptor))


def _same_inode(metadata: os.stat_result, identity: DirectoryIdentity) -> bool:
    return metadata.st_dev == identity.device and metadata.st_ino == identity.inode


def _open_bound_parent(path: Path) -> tuple[int, DirectoryIdentity]:
    descriptor: int | None = None
    try:
        descriptor = _open_directory(path)
        identity = _directory_identity(descriptor)
        pathname = path.lstat()
    except (OSError, OciBuildError) as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if isinstance(error, OciBuildError):
            raise
        raise OciBuildError("capacity_root_invalid") from error
    if (
        stat.S_ISLNK(pathname.st_mode)
        or not stat.S_ISDIR(pathname.st_mode)
        or not _same_inode(pathname, identity)
    ):
        os.close(descriptor)
        raise OciBuildError("capacity_root_invalid")
    return descriptor, identity


def _require_reviewed_parent_mount(
    parent: Path, identity: DirectoryIdentity, roots: CapacityRoots
) -> None:
    if roots.production and _mounted_path_ids().get(parent) != identity.mount_id:
        raise OciBuildError("capacity_mount_invalid")


def _validate_capacity_parents(roots: CapacityRoots) -> None:
    if roots.production and (sys.platform != "linux" or os.geteuid() != 0):
        raise OciBuildError("capacity_host_invalid")
    descriptors: list[int] = []
    try:
        identities: list[DirectoryIdentity] = []
        for parent in (roots.scratch_parent, roots.graph_parent, roots.run_parent):
            descriptor, identity = _open_bound_parent(parent)
            descriptors.append(descriptor)
            identities.append(identity)
        for parent, identity in zip(
            (roots.scratch_parent, roots.graph_parent, roots.run_parent),
            identities,
            strict=True,
        ):
            _require_reviewed_parent_mount(parent, identity, roots)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _capacity_marker_payload(
    roots: CapacityRoots,
    *,
    parent_identities: Mapping[str, DirectoryIdentity] | None = None,
    leaf_identities: Mapping[str, DirectoryIdentity] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "ciw.oci-capacity.v1",
        "domain": roots.domain,
        "prefix": roots.prefix,
        "token": roots.token,
        "production": roots.production,
        "scratch_root": str(roots.scratch_root),
        "graph_root": str(roots.graph_root),
        "run_root": str(roots.run_root),
    }
    if parent_identities is not None and leaf_identities is not None:
        payload["parent_identities"] = {
            key: value.to_dict() for key, value in sorted(parent_identities.items())
        }
        payload["leaf_identities"] = {
            key: value.to_dict() for key, value in sorted(leaf_identities.items())
        }
    return payload


def _open_directory(path: Path) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    return os.open(path, flags)


def _write_capacity_marker(directory_fd: int, marker: bytes) -> None:
    marker_fd = os.open(
        _CAPACITY_MARKER,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(marker):
            written = os.write(marker_fd, marker[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short capacity marker write")
            offset += written
        os.fsync(marker_fd)
    finally:
        os.close(marker_fd)
    os.fsync(directory_fd)


def _read_marker_fd(directory_fd: int) -> dict[str, object]:
    directory_mode = os.fstat(directory_fd).st_mode
    if not stat.S_ISDIR(directory_mode) or stat.S_IMODE(directory_mode) != 0o700:
        raise OciBuildError("capacity_marker_invalid")
    try:
        marker_fd = os.open(
            _CAPACITY_MARKER,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
        try:
            marker_metadata = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(marker_metadata.st_mode)
                or stat.S_IMODE(marker_metadata.st_mode) != 0o600
                or marker_metadata.st_size > 4096
            ):
                raise OciBuildError("capacity_marker_invalid")
            payload = b""
            while len(payload) <= 4096:
                chunk = os.read(marker_fd, 4097 - len(payload))
                if not chunk:
                    break
                payload += chunk
        finally:
            os.close(marker_fd)
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("capacity_marker_invalid") from error
    if len(payload) > 4096:
        raise OciBuildError("capacity_marker_invalid")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciBuildError("capacity_marker_invalid") from error
    if not isinstance(value, dict):
        raise OciBuildError("capacity_marker_invalid")
    return value


def _identity_from_value(value: object) -> DirectoryIdentity:
    if not isinstance(value, dict) or set(value) != {"device", "inode", "mount_id"}:
        raise OciBuildError("capacity_marker_invalid")
    if any(
        not isinstance(value[key], int)
        or isinstance(value[key], bool)
        or value[key] < 0
        for key in value
    ):
        raise OciBuildError("capacity_marker_invalid")
    return DirectoryIdentity(value["device"], value["inode"], value["mount_id"])


def _parse_marker_identities(
    payload: Mapping[str, object], roots: CapacityRoots
) -> tuple[Mapping[str, DirectoryIdentity], Mapping[str, DirectoryIdentity]]:
    static = _capacity_marker_payload(roots)
    if any(payload.get(key) != value for key, value in static.items()):
        raise OciBuildError("capacity_marker_invalid")
    if set(payload) != set(static) | {"parent_identities", "leaf_identities"}:
        raise OciBuildError("capacity_marker_invalid")
    raw_parents = payload.get("parent_identities")
    raw_leaves = payload.get("leaf_identities")
    if not isinstance(raw_parents, dict) or not isinstance(raw_leaves, dict):
        raise OciBuildError("capacity_marker_invalid")
    if set(raw_parents) != {"scratch", "graph", "run"} or set(raw_leaves) != {
        "scratch", "graph", "run"
    }:
        raise OciBuildError("capacity_marker_invalid")
    return (
        {key: _identity_from_value(value) for key, value in raw_parents.items()},
        {key: _identity_from_value(value) for key, value in raw_leaves.items()},
    )


def _capacity_rows(roots: CapacityRoots) -> tuple[tuple[str, Path], ...]:
    return (
        ("scratch", roots.scratch_parent),
        ("graph", roots.graph_parent),
        ("run", roots.run_parent),
    )


def _open_capacity_leaf(
    parent_fd: int,
    leaf_name: str,
    *,
    error_code: str = "capacity_root_invalid",
) -> tuple[int, DirectoryIdentity]:
    try:
        descriptor = os.open(
            leaf_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise OciBuildError(error_code) from error
    try:
        identity = _directory_identity(descriptor)
        pathname = os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_inode(pathname, identity):
            raise OciBuildError("capacity_root_invalid")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, identity


def _open_verified_capacity(
    roots: CapacityRoots,
) -> tuple[
    Mapping[str, int],
    Mapping[str, DirectoryIdentity],
    Mapping[str, int],
    Mapping[str, DirectoryIdentity],
]:
    parent_fds: dict[str, int] = {}
    leaf_fds: dict[str, int] = {}
    leaf_identities: dict[str, DirectoryIdentity] = {}
    try:
        parent_identities: dict[str, DirectoryIdentity] = {}
        payloads: list[dict[str, object]] = []
        for key, parent in _capacity_rows(roots):
            parent_fd, parent_identity = _open_bound_parent(parent)
            parent_fds[key] = parent_fd
            parent_identities[key] = parent_identity
            _require_reviewed_parent_mount(parent, parent_identity, roots)
            leaf_fd, leaf_identity = _open_capacity_leaf(parent_fd, roots.leaf_name)
            leaf_fds[key] = leaf_fd
            leaf_identities[key] = leaf_identity
            if leaf_identity.mount_id != parent_identity.mount_id:
                raise OciBuildError("capacity_mount_invalid")
            payloads.append(_read_marker_fd(leaf_fd))
            registry = _read_capacity_registry(parent_fd, roots)
            if registry != payloads[-1]:
                raise OciBuildError("capacity_marker_invalid")
        if any(payload != payloads[0] for payload in payloads[1:]):
            raise OciBuildError("capacity_marker_invalid")
        expected_parents, expected_leaves = _parse_marker_identities(payloads[0], roots)
        if parent_identities != expected_parents or leaf_identities != expected_leaves:
            raise OciBuildError("capacity_marker_invalid")
        return parent_fds, parent_identities, leaf_fds, leaf_identities
    except BaseException:
        for descriptor in reversed(tuple(leaf_fds.values())):
            os.close(descriptor)
        for descriptor in reversed(tuple(parent_fds.values())):
            os.close(descriptor)
        raise


def _verify_capacity_markers(roots: CapacityRoots) -> None:
    parent_fds, _, leaf_fds, _ = _open_verified_capacity(roots)
    for descriptor in reversed(tuple(leaf_fds.values())):
        os.close(descriptor)
    for descriptor in reversed(tuple(parent_fds.values())):
        os.close(descriptor)


def _tombstone_name(roots: CapacityRoots) -> str:
    return f".{roots.leaf_name}.delete-{secrets.token_hex(16)}"


def _capacity_registry_name(roots: CapacityRoots) -> str:
    return f".{roots.leaf_name}.allocation.json"


def _write_capacity_registry(parent_fd: int, roots: CapacityRoots, payload: bytes) -> None:
    name = _capacity_registry_name(roots)
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=parent_fd,
    )
    created = os.fstat(descriptor)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short capacity registry write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.fsync(parent_fd)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        tombstone = _quarantine_nondirectory(parent_fd, name, created, roots)
        if tombstone is not None:
            try:
                os.unlink(tombstone, dir_fd=parent_fd)
            except OSError:
                pass
        raise


def _read_capacity_registry(
    parent_fd: int, roots: CapacityRoots, *, name: str | None = None
) -> dict[str, object]:
    selected_name = _capacity_registry_name(roots) if name is None else name
    try:
        descriptor = os.open(
            selected_name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > 4096
            ):
                raise OciBuildError("capacity_marker_invalid")
            payload = b""
            while len(payload) <= 4096:
                chunk = os.read(descriptor, 4097 - len(payload))
                if not chunk:
                    break
                payload += chunk
        finally:
            os.close(descriptor)
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("capacity_marker_invalid") from error
    if len(payload) > 4096:
        raise OciBuildError("capacity_marker_invalid")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciBuildError("capacity_marker_invalid") from error
    if not isinstance(value, dict):
        raise OciBuildError("capacity_marker_invalid")
    return value


def _remove_capacity_registry(
    parent_fd: int,
    parent_identity: DirectoryIdentity,
    key: str,
    roots: CapacityRoots,
) -> bool:
    name = _capacity_registry_name(roots)
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        # Registry tombstones are never accepted as durable authority. The
        # writer restores the exact canonical name after an interrupted
        # unlink; a forged or externally renamed record is preserved.
        return True
    except OSError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        return False
    try:
        payload = _read_capacity_registry(parent_fd, roots)
        parent_ids, _leaf_ids = _parse_marker_identities(payload, roots)
    except OciBuildError:
        return False
    if parent_ids[key] != parent_identity:
        return False
    tombstone = _quarantine_nondirectory(parent_fd, name, metadata, roots)
    if tombstone is None:
        return False
    try:
        os.unlink(tombstone, dir_fd=parent_fd)
    except OSError:
        try:
            os.rename(
                tombstone,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except OSError:
            pass
        return False
    return True


def _path_matches_directory(
    parent_fd: int, name: str, identity: DirectoryIdentity
) -> bool:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and _same_inode(metadata, identity)


def _path_matches_stat(parent_fd: int, name: str, expected: os.stat_result) -> bool:
    try:
        actual = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return actual.st_dev == expected.st_dev and actual.st_ino == expected.st_ino


def _quarantine_directory(
    parent_fd: int,
    name: str,
    directory_fd: int,
    identity: DirectoryIdentity,
    roots: CapacityRoots,
) -> str | None:
    if not _path_matches_directory(parent_fd, name, identity):
        return None
    tombstone = _tombstone_name(roots)
    try:
        os.rename(
            name,
            tombstone,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    except OSError:
        return None
    if not _path_matches_directory(parent_fd, tombstone, identity):
        rebound_name = _find_bound_directory_name(parent_fd, identity)
        if rebound_name is None:
            return None
        tombstone = rebound_name
    if _directory_identity(directory_fd) != identity:
        return None
    return tombstone


def _find_bound_directory_name(
    parent_fd: int, identity: DirectoryIdentity
) -> str | None:
    """Find the one directory entry still naming an already-bound inode."""

    matches: list[str] = []
    try:
        entries = list(os.scandir(parent_fd))
    except OSError:
        return None
    for entry in entries:
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISDIR(metadata.st_mode) or not _same_inode(metadata, identity):
            continue
        try:
            candidate_fd, candidate_identity = _open_capacity_leaf(
                parent_fd, entry.name
            )
        except OciBuildError:
            continue
        try:
            if candidate_identity == identity:
                matches.append(entry.name)
        finally:
            os.close(candidate_fd)
    return matches[0] if len(matches) == 1 else None


def _quarantine_nondirectory(
    parent_fd: int, name: str, expected: os.stat_result, roots: CapacityRoots
) -> str | None:
    if not _path_matches_stat(parent_fd, name, expected):
        return None
    tombstone = _tombstone_name(roots)
    try:
        os.rename(name, tombstone, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except OSError:
        return None
    return tombstone if _path_matches_stat(parent_fd, tombstone, expected) else None


def _remove_tree_contents_nofollow(
    directory_fd: int,
    expected_mount_id: int,
    roots: CapacityRoots,
    *,
    preserve_names: frozenset[str] = frozenset(),
) -> bool:
    identity = _directory_identity(directory_fd)
    mount_id = expected_mount_id
    if identity.mount_id != mount_id:
        return False
    try:
        entries = list(os.scandir(directory_fd))
    except OSError:
        return False
    clean = True
    for entry in entries:
        name = entry.name
        if name in preserve_names:
            continue
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError:
            clean = False
            continue
        if stat.S_ISDIR(metadata.st_mode):
            try:
                child_fd = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                child_identity = _directory_identity(child_fd)
            except (OSError, OciBuildError):
                clean = False
                continue
            try:
                if (
                    not _same_inode(metadata, child_identity)
                    or child_identity.mount_id != mount_id
                ):
                    clean = False
                    continue
                tombstone = _quarantine_directory(
                    directory_fd,
                    name,
                    child_fd,
                    child_identity,
                    roots,
                )
                if tombstone is None:
                    clean = False
                    continue
                child_clean = _remove_tree_contents_nofollow(
                    child_fd, mount_id, roots
                )
                if (
                    not child_clean
                    or not _path_matches_directory(
                        directory_fd, tombstone, child_identity
                    )
                ):
                    clean = False
                    continue
                try:
                    os.rmdir(tombstone, dir_fd=directory_fd)
                except OSError:
                    clean = False
            finally:
                os.close(child_fd)
        else:
            tombstone = _quarantine_nondirectory(
                directory_fd, name, metadata, roots
            )
            if tombstone is None or not _path_matches_stat(
                directory_fd, tombstone, metadata
            ):
                clean = False
                continue
            try:
                os.unlink(tombstone, dir_fd=directory_fd)
            except OSError:
                clean = False
    return clean


def _capacity_tree_mounts_confined(
    directory_fd: int, expected_mount_id: int
) -> bool:
    """Reject every descendant mount without following symlink targets."""

    if _mount_id_for_fd(directory_fd) != expected_mount_id:
        return False
    try:
        entries = list(os.scandir(directory_fd))
    except OSError:
        return False
    for entry in entries:
        try:
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
            else:
                flags = (
                    getattr(os, "O_PATH", os.O_RDONLY)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                )
            descriptor = os.open(entry.name, flags, dir_fd=directory_fd)
        except OSError:
            return False
        try:
            actual = os.fstat(descriptor)
            if (
                actual.st_dev != metadata.st_dev
                or actual.st_ino != metadata.st_ino
                or _mount_id_for_fd(descriptor) != expected_mount_id
            ):
                return False
            if stat.S_ISDIR(metadata.st_mode) and not _capacity_tree_mounts_confined(
                descriptor, expected_mount_id
            ):
                return False
        except (OSError, OciBuildError):
            return False
        finally:
            os.close(descriptor)
    return True


def _remove_open_capacity_leaf(
    parent_fd: int,
    leaf_fd: int,
    leaf_identity: DirectoryIdentity,
    roots: CapacityRoots,
    *,
    require_marker: bool = True,
) -> bool:
    leaf_name = roots.leaf_name
    if not _path_matches_directory(parent_fd, leaf_name, leaf_identity):
        rebound_name = _find_bound_directory_name(parent_fd, leaf_identity)
        if rebound_name is None:
            return False
        leaf_name = rebound_name
    tombstone = _quarantine_directory(
        parent_fd, leaf_name, leaf_fd, leaf_identity, roots
    )
    if tombstone is None:
        return False
    clean = _remove_tree_contents_nofollow(
        leaf_fd,
        leaf_identity.mount_id,
        roots,
        preserve_names=(
            frozenset({_CAPACITY_MARKER}) if require_marker else frozenset()
        ),
    )
    if clean and require_marker:
        try:
            _read_marker_fd(leaf_fd)
            marker_metadata = os.stat(
                _CAPACITY_MARKER,
                dir_fd=leaf_fd,
                follow_symlinks=False,
            )
        except (OSError, OciBuildError):
            clean = False
        else:
            marker_tombstone = _quarantine_nondirectory(
                leaf_fd,
                _CAPACITY_MARKER,
                marker_metadata,
                roots,
            )
            if marker_tombstone is None or not _path_matches_stat(
                leaf_fd, marker_tombstone, marker_metadata
            ):
                clean = False
            else:
                try:
                    os.unlink(marker_tombstone, dir_fd=leaf_fd)
                except OSError:
                    clean = False
    if not _path_matches_directory(parent_fd, tombstone, leaf_identity):
        return False
    if clean:
        try:
            os.rmdir(tombstone, dir_fd=parent_fd)
        except OSError:
            clean = False
    # A concurrent replacement at the public leaf name is not ours and must
    # survive, but it also prevents terminal cleanup from being declared clean.
    try:
        os.stat(roots.leaf_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError:
        clean = False
    else:
        clean = False
    return clean


def prepare_capacity_roots(roots: CapacityRoots) -> None:
    """Exclusively allocate all three reviewed capacity leaves."""

    _validate_capacity_parents(roots)
    parent_fds: dict[str, int] = {}
    leaf_fds: dict[str, int] = {}
    parent_identities: dict[str, DirectoryIdentity] = {}
    leaf_identities: dict[str, DirectoryIdentity] = {}
    registries: list[str] = []
    allocation_unbound = False
    try:
        for key, parent in _capacity_rows(roots):
            parent_fd, parent_identity = _open_bound_parent(parent)
            parent_fds[key] = parent_fd
            parent_identities[key] = parent_identity
            _require_reviewed_parent_mount(parent, parent_identity, roots)
            os.mkdir(roots.leaf_name, 0o700, dir_fd=parent_fd)
            try:
                leaf_fd, leaf_identity = _open_capacity_leaf(
                    parent_fd,
                    roots.leaf_name,
                    error_code="capacity_root_invalid",
                )
            except BaseException:
                # mkdirat succeeded, but without a bound descriptor identity we
                # cannot safely remove whatever now occupies the public name.
                allocation_unbound = True
                raise
            leaf_fds[key] = leaf_fd
            leaf_identities[key] = leaf_identity
            if leaf_identity.mount_id != parent_identity.mount_id:
                raise OciBuildError("capacity_mount_invalid")
        marker = (
            json.dumps(
                _capacity_marker_payload(
                    roots,
                    parent_identities=parent_identities,
                    leaf_identities=leaf_identities,
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for key, _parent in _capacity_rows(roots):
            _write_capacity_marker(leaf_fds[key], marker)
        for key, _parent in _capacity_rows(roots):
            _write_capacity_registry(parent_fds[key], roots, marker)
            registries.append(key)
        # Verify public names before committing while the allocation's
        # original descriptors are still available for race-safe rollback.
        _verify_capacity_markers(roots)
    except BaseException as error:
        rollback_ok = not allocation_unbound
        for key in reversed(tuple(leaf_fds)):
            if not _remove_open_capacity_leaf(
                parent_fds[key],
                leaf_fds[key],
                leaf_identities[key],
                roots,
                require_marker=False,
            ):
                rollback_ok = False
        for key in reversed(tuple(registries)):
            if not _remove_capacity_registry(
                parent_fds[key], parent_identities[key], key, roots
            ):
                rollback_ok = False
        if not rollback_ok:
            raise OciBuildError("cleanup_failed") from error
        if isinstance(error, FileExistsError):
            raise OciBuildError("residue_detected") from error
        if isinstance(error, OciBuildError):
            raise
        if isinstance(error, OSError):
            raise OciBuildError("capacity_root_invalid") from error
        raise
    finally:
        for descriptor in reversed(tuple(leaf_fds.values())):
            os.close(descriptor)
        for descriptor in reversed(tuple(parent_fds.values())):
            os.close(descriptor)


def _remove_capacity_leaf(parent: Path, roots: CapacityRoots) -> bool:
    try:
        parent_fd, parent_identity = _open_bound_parent(parent)
        _require_reviewed_parent_mount(parent, parent_identity, roots)
    except (OSError, OciBuildError):
        return False
    try:
        try:
            metadata = os.stat(
                roots.leaf_name, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            # A substituted non-directory is never traversed. Moving it to an
            # unpredictable tombstone first prevents unlinking a later
            # replacement at the public name.
            tombstone = _quarantine_nondirectory(
                parent_fd, roots.leaf_name, metadata, roots
            )
            if tombstone is None or not _path_matches_stat(parent_fd, tombstone, metadata):
                return False
            try:
                os.unlink(tombstone, dir_fd=parent_fd)
            except OSError:
                return False
            return False
        try:
            leaf_fd, leaf_identity = _open_capacity_leaf(parent_fd, roots.leaf_name)
        except (OSError, OciBuildError):
            return False
        try:
            key = next(key for key, selected in _capacity_rows(roots) if selected == parent)
            if not _capacity_leaf_is_owned(
                parent_fd,
                parent_identity,
                key,
                leaf_fd,
                leaf_identity,
                roots,
            ):
                return False
            return _remove_open_capacity_leaf(
                parent_fd,
                leaf_fd,
                leaf_identity,
                roots,
                require_marker=not _capacity_registry_authenticates_leaf(
                    parent_fd,
                    parent_identity,
                    key,
                    leaf_identity,
                    roots,
                ),
            )
        finally:
            os.close(leaf_fd)
    finally:
        os.close(parent_fd)


def _owned_capacity_leaf_names(
    parent_fd: int,
    parent_identity: DirectoryIdentity,
    key: str,
    roots: CapacityRoots,
) -> tuple[str, ...]:
    """Find marker-authenticated leaves for one exact capacity parent."""

    names: list[str] = []
    registry_present = False
    expected_leaf: DirectoryIdentity | None = None
    try:
        os.stat(
            _capacity_registry_name(roots),
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        registry_present = True
        registry = _read_capacity_registry(parent_fd, roots)
        parent_ids, leaf_ids = _parse_marker_identities(registry, roots)
        if parent_ids[key] != parent_identity:
            raise OciBuildError("capacity_marker_invalid")
        expected_leaf = leaf_ids[key]
    except FileNotFoundError:
        pass
    except OSError as error:
        raise OciBuildError("residue_detected") from error
    try:
        entries = list(os.scandir(parent_fd))
    except OSError as error:
        raise OciBuildError("residue_detected") from error
    for entry in entries:
        try:
            candidate_fd, candidate_identity = _open_capacity_leaf(
                parent_fd, entry.name
            )
        except OciBuildError:
            continue
        try:
            if registry_present:
                owned = (
                    candidate_identity == expected_leaf
                    and candidate_identity.mount_id == parent_identity.mount_id
                )
            else:
                payload = _read_marker_fd(candidate_fd)
                parent_ids, leaf_ids = _parse_marker_identities(payload, roots)
                owned = (
                    parent_identity == parent_ids[key]
                    and candidate_identity == leaf_ids[key]
                    and candidate_identity.mount_id == parent_identity.mount_id
                )
            if owned:
                names.append(entry.name)
        except OciBuildError:
            pass
        finally:
            os.close(candidate_fd)
    return tuple(names)


def _capacity_leaf_is_owned(
    parent_fd: int,
    parent_identity: DirectoryIdentity,
    key: str,
    leaf_fd: int,
    leaf_identity: DirectoryIdentity,
    roots: CapacityRoots,
) -> bool:
    try:
        registry = _read_capacity_registry(parent_fd, roots)
    except OciBuildError:
        try:
            os.stat(
                _capacity_registry_name(roots),
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            try:
                marker = _read_marker_fd(leaf_fd)
                parent_ids, leaf_ids = _parse_marker_identities(marker, roots)
            except OciBuildError:
                return False
        except OSError:
            return False
        else:
            return False
    else:
        try:
            parent_ids, leaf_ids = _parse_marker_identities(registry, roots)
        except OciBuildError:
            return False
    return (
        parent_identity == parent_ids[key]
        and leaf_identity == leaf_ids[key]
        and leaf_identity.mount_id == parent_identity.mount_id
    )


def _capacity_registry_authenticates_leaf(
    parent_fd: int,
    parent_identity: DirectoryIdentity,
    key: str,
    leaf_identity: DirectoryIdentity,
    roots: CapacityRoots,
) -> bool:
    try:
        registry = _read_capacity_registry(parent_fd, roots)
        parent_ids, leaf_ids = _parse_marker_identities(registry, roots)
    except OciBuildError:
        return False
    return (
        parent_ids[key] == parent_identity
        and leaf_ids[key] == leaf_identity
        and leaf_identity.mount_id == parent_identity.mount_id
    )


def _remove_stranded_capacity_leaf(
    key: str, parent: Path, roots: CapacityRoots
) -> bool:
    """Recover exactly one renamed invocation-owned leaf, never ambiguity."""

    try:
        parent_fd, parent_identity = _open_bound_parent(parent)
        _require_reviewed_parent_mount(parent, parent_identity, roots)
    except (OSError, OciBuildError):
        return False
    try:
        try:
            names = _owned_capacity_leaf_names(
                parent_fd, parent_identity, key, roots
            )
        except OciBuildError:
            return False
        if not names:
            return True
        if len(names) != 1:
            return False
        try:
            leaf_fd, leaf_identity = _open_capacity_leaf(parent_fd, names[0])
        except OciBuildError:
            return False
        try:
            if not _capacity_leaf_is_owned(
                parent_fd,
                parent_identity,
                key,
                leaf_fd,
                leaf_identity,
                roots,
            ):
                return False
            return _remove_open_capacity_leaf(
                parent_fd,
                leaf_fd,
                leaf_identity,
                roots,
                require_marker=not _capacity_registry_authenticates_leaf(
                    parent_fd,
                    parent_identity,
                    key,
                    leaf_identity,
                    roots,
                ),
            )
        finally:
            os.close(leaf_fd)
    finally:
        os.close(parent_fd)


def _capacity_residue_names(parent: Path, roots: CapacityRoots) -> tuple[str, ...]:
    parent_fd: int | None = None
    try:
        parent_fd, parent_identity = _open_bound_parent(parent)
        _require_reviewed_parent_mount(parent, parent_identity, roots)
        entries = list(os.scandir(parent_fd))
        tombstone_prefix = f".{roots.leaf_name}.delete-"
        key = next(
            key for key, selected in _capacity_rows(roots) if selected == parent
        )
        owned_names = frozenset(
            _owned_capacity_leaf_names(parent_fd, parent_identity, key, roots)
        )
        names: list[str] = []
        for entry in entries:
            if (
                entry.name == roots.leaf_name
                or entry.name.startswith(tombstone_prefix)
                or entry.name == _capacity_registry_name(roots)
                or entry.name in owned_names
            ):
                names.append(entry.name)
        return tuple(names)
    except (OSError, OciBuildError) as error:
        raise OciBuildError("residue_detected") from error
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def remove_capacity_roots(roots: CapacityRoots) -> bool:
    clean = True
    for key, parent in _capacity_rows(roots):
        leaf_clean = _remove_capacity_leaf(parent, roots)
        stranded_clean = _remove_stranded_capacity_leaf(key, parent, roots)
        if not leaf_clean:
            clean = False
        if not stranded_clean:
            clean = False
        if leaf_clean and stranded_clean:
            try:
                parent_fd, parent_identity = _open_bound_parent(parent)
            except OciBuildError:
                clean = False
            else:
                try:
                    if not _remove_capacity_registry(
                        parent_fd, parent_identity, key, roots
                    ):
                        clean = False
                finally:
                    os.close(parent_fd)
        if _capacity_residue_names(parent, roots):
            clean = False
    return clean


def state_root(
    environment: Mapping[str, str],
    *,
    _capacity_roots: CapacityRoots | None = None,
) -> Path:
    return (_capacity_roots or build_capacity_roots(environment)).scratch_root


def execute_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    env: Mapping[str, str] | None = None,
    preexec_fn: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        check=True,
        text=True,
        capture_output=capture,
        preexec_fn=preexec_fn,
    )


def execute_binary_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    preexec_fn: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        check=True,
        text=False,
        capture_output=True,
        preexec_fn=preexec_fn,
    )


_EngineResult = TypeVar("_EngineResult")


def _validated_engine_operation(
    argv: Sequence[str], operation: Callable[[], _EngineResult]
) -> _EngineResult:
    if not argv or Path(argv[0]).name not in {"buildah", "skopeo", "podman"}:
        raise OciBuildError("builder_unavailable")
    try:
        return operation()
    except subprocess.CalledProcessError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        # subprocess intentionally hides the concrete exception raised by a
        # pre-exec hook. Preserve a closed failure code when namespace setup or
        # process creation is unavailable instead of leaking an untyped
        # traceback past the CIW adapter.
        raise OciBuildError("engine_isolation_failed") from error


def execute_engine_command(
    roots: CapacityRoots,
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _validated_engine_operation(
        argv,
        lambda: execute_command(
            argv,
            cwd=cwd,
            capture=capture,
            env=env,
            preexec_fn=_private_engine_preexec(roots),
        ),
    )


def capture_engine_bytes(
    roots: CapacityRoots,
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> bytes:
    """Capture an OCI engine's stdout without text or newline conversion."""

    result = _validated_engine_operation(
        argv,
        lambda: execute_binary_command(
            argv,
            cwd=cwd,
            env=env,
            preexec_fn=_private_engine_preexec(roots),
        ),
    )
    if not isinstance(result.stdout, bytes):
        raise OciBuildError("engine_isolation_failed")
    return result.stdout


def _private_engine_preexec(
    roots: CapacityRoots,
    system_containers: Path = Path("/var/lib/containers"),
) -> Callable[[], None]:
    """Confine rootful containers/image implicit state to registered run state.

    Containers/image 5.26.2, used by the pinned Skopeo 1.13.3, otherwise
    hard-codes its rootful blob-info cache below ``/var/lib/containers`` and
    exposes no CLI cache-path override.  Every engine subprocess therefore
    receives a fresh private mount namespace whose complete implicit containers
    root is a bind mount of this run's registered state.
    """

    if not system_containers.is_absolute():
        raise OciBuildError("engine_isolation_failed")
    _verify_capacity_markers(roots)

    def open_child(parent_fd: int, name: str) -> tuple[int, DirectoryIdentity]:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            identity = _directory_identity(descriptor)
            pathname = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not _same_inode(pathname, identity):
                raise OSError(errno.EPERM, "capacity child identity changed")
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor, identity

    def prepare() -> None:
        unshare = getattr(os, "unshare", None)
        clone_newns = getattr(os, "CLONE_NEWNS", None)
        if unshare is None or clone_newns is None or _MOUNT is None:
            raise OSError(errno.ENOSYS, "private mount namespaces unavailable")
        parent_fds: Mapping[str, int] = {}
        leaf_fds: Mapping[str, int] = {}
        opened: list[int] = []
        try:
            (
                parent_fds,
                parent_identities,
                leaf_fds,
                leaf_identities,
            ) = _open_verified_capacity(roots)
            private_fd, private_identity = open_child(
                leaf_fds["scratch"], "implicit-containers"
            )
            opened.append(private_fd)
            private_storage_fd, private_storage_identity = open_child(
                private_fd, "storage"
            )
            opened.append(private_storage_fd)
            if (
                private_identity.mount_id
                != leaf_identities["scratch"].mount_id
                or private_storage_identity.mount_id
                != leaf_identities["scratch"].mount_id
            ):
                raise OSError(errno.EPERM, "capacity child mount changed")
            system_fd, system_identity = _open_bound_parent(system_containers)
            opened.append(system_fd)
            unshare(clone_newns)
            if _MOUNT(None, b"/", None, _MOUNT_REC | _MOUNT_PRIVATE, None) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            for descriptor, identity in (
                *tuple(
                    (parent_fds[key], parent_identities[key])
                    for key, _ in _capacity_rows(roots)
                ),
                (leaf_fds["graph"], leaf_identities["graph"]),
                (private_fd, private_identity),
                (private_storage_fd, private_storage_identity),
                (system_fd, system_identity),
            ):
                if _directory_identity(descriptor) != identity:
                    raise OSError(errno.EPERM, "capacity identity changed")
            if not _capacity_tree_mounts_confined(
                leaf_fds["graph"], leaf_identities["graph"].mount_id
            ) or not _capacity_tree_mounts_confined(
                private_fd, leaf_identities["scratch"].mount_id
            ):
                raise OSError(errno.EPERM, "capacity descendant mount changed")
            for key in ("scratch", "run"):
                source = os.fsencode(f"/proc/self/fd/{leaf_fds[key]}")
                target = os.fsencode(getattr(roots, f"{key}_root"))
                if _MOUNT(source, target, None, _MOUNT_BIND, None) != 0:
                    error = ctypes.get_errno()
                    raise OSError(error, os.strerror(error))
                # The bind pins the canonical engine-consumed pathname. A
                # target swap before mount is harmlessly covered; a rename
                # after mount is rejected by the kernel as a busy mountpoint.
                if not _path_matches_directory(
                    parent_fds[key], roots.leaf_name, leaf_identities[key]
                ):
                    raise OSError(errno.EPERM, "capacity pathname changed")
            graph_source = os.fsencode(f"/proc/self/fd/{leaf_fds['graph']}")
            storage_target = os.fsencode(f"/proc/self/fd/{private_storage_fd}")
            private_source = os.fsencode(f"/proc/self/fd/{private_fd}")
            system_target = os.fsencode(f"/proc/self/fd/{system_fd}")
            if (
                _MOUNT(
                    graph_source,
                    storage_target,
                    None,
                    _MOUNT_BIND | _MOUNT_REC,
                    None,
                )
                != 0
            ):
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            if (
                _MOUNT(
                    private_source,
                    system_target,
                    None,
                    _MOUNT_BIND | _MOUNT_REC,
                    None,
                )
                != 0
            ):
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
            for descriptor in reversed(tuple(leaf_fds.values())):
                os.close(descriptor)
            for descriptor in reversed(tuple(parent_fds.values())):
                os.close(descriptor)

    return prepare


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _write_build_result_file(path: Path, result: OciBuildResult) -> None:
    """Write private exact-build state without following a result symlink."""

    payload = (
        json.dumps(result.persisted_values(), sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(payload) > 2 * 1024 * 1024:
        raise OciBuildError("build_result_invalid")
    directory_fd = -1
    descriptor = -1
    try:
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_TRUNC
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OciBuildError("build_result_invalid")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short build-result write")
            offset += written
        os.fsync(descriptor)
        final = os.fstat(descriptor)
        linked = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(final.st_mode)
            or stat.S_IMODE(final.st_mode) != 0o600
            or final.st_size != len(payload)
            or final.st_nlink != 1
            or (final.st_dev, final.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            raise OciBuildError("build_result_invalid")
        os.fsync(directory_fd)
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("build_result_invalid") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)


def exact_git_head(source_root: Path) -> str:
    result = execute_command(["git", "rev-parse", "HEAD"], cwd=source_root, capture=True)
    return result.stdout.strip()


def assert_clean_source(source_root: Path, admitted_sha: str) -> None:
    if exact_git_head(source_root) != admitted_sha:
        raise OciBuildError("source_mismatch")
    status = execute_command(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=source_root,
        capture=True,
    ).stdout
    if status.strip():
        raise OciBuildError("dirty_tree")


def source_date_epoch(source_root: Path) -> int:
    value = execute_command(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=source_root, capture=True).stdout.strip()
    if not value.isdigit() or int(value) <= 0:
        raise OciBuildError("source_mismatch")
    return int(value)


def validate_dockerfile_bases(path: Path) -> tuple[str, ...]:
    if not path.is_file() or path.is_symlink():
        raise OciBuildError("invalid_path")
    source = path.read_text(encoding="utf-8")
    if source.startswith("\ufeff"):
        raise OciBuildError("base_identity_mutable")
    images: list[str] = []
    stage_aliases: set[str] = set()
    logical = ""
    for raw in source.splitlines():
        if re.match(r"^\s*#\s*escape\s*=", raw, re.IGNORECASE):
            # The bounded parser implements only Dockerfile's default
            # backslash continuation.  Reject alternate escape directives so
            # Buildah cannot recognize a FROM instruction that this evidence
            # parser silently omits.
            raise OciBuildError("base_identity_mutable")
        if re.match(r"^\s*#", raw):
            # Full-line comments never continue Dockerfile instructions even
            # when their final byte is a backslash.
            continue
        stripped = raw.rstrip()
        logical += stripped[:-1] + " " if stripped.endswith("\\") else stripped
        if stripped.endswith("\\"):
            continue
        line = logical.strip()
        logical = ""
        match = _FROM.fullmatch(line)
        if match:
            image = match.group(1)
            alias = match.group(2)
            normalized_image = image.lower()
            if (
                "$" in image
                or (
                    image != "scratch"
                    and normalized_image not in stage_aliases
                    and "@sha256:" not in image
                )
            ):
                raise OciBuildError("base_identity_mutable")
            if image != "scratch" and normalized_image not in stage_aliases:
                digest = image.rsplit("@", 1)[1]
                if _DIGEST.fullmatch(digest) is None:
                    raise OciBuildError("base_identity_mutable")
            images.append(image)
            if alias:
                normalized_alias = alias.lower()
                if normalized_alias in stage_aliases:
                    raise OciBuildError("base_identity_mutable")
                stage_aliases.add(normalized_alias)
    if logical:
        raise OciBuildError("base_identity_mutable")
    if not images:
        raise OciBuildError("base_identity_mutable")
    return tuple(images)


def _tracked_files(source_root: Path, context_path: str) -> tuple[Path, ...]:
    args = ["git", "ls-files", "-z"]
    if context_path != ".":
        args.extend(["--", context_path])
    payload = execute_command(args, cwd=source_root, capture=True).stdout
    files = tuple(Path(item) for item in payload.split("\0") if item)
    if not files:
        raise OciBuildError("dirty_context")
    status_args = ["git", "status", "--porcelain", "--untracked-files=all"]
    if context_path != ".":
        status_args.extend(["--", context_path])
    if execute_command(status_args, cwd=source_root, capture=True).stdout.strip():
        raise OciBuildError("dirty_context")
    return files


def stage_context(source_root: Path, target: OciTarget, destination: Path) -> Path:
    context = bounded_path(source_root, target.context_path, allow_root=True)
    dockerfile = bounded_path(source_root, target.dockerfile_path)
    if context != source_root.resolve() and context not in dockerfile.parents:
        raise OciBuildError("invalid_path")
    validate_dockerfile_bases(dockerfile)
    if target.smoke_script:
        smoke = bounded_path(source_root, target.smoke_script)
        if not smoke.is_file() or smoke.is_symlink():
            raise OciBuildError("invalid_path")
    destination.mkdir(parents=True, mode=0o700)
    for relative in _tracked_files(source_root, target.context_path):
        source = source_root / relative
        info = source.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OciBuildError("symlink_path_forbidden")
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output, follow_symlinks=False)
    copied_dockerfile = destination / target.dockerfile_path
    if not copied_dockerfile.is_file():
        raise OciBuildError("invalid_path")
    return destination


def _read_json(path: Path) -> object:
    try:
        if not path.is_file() or path.is_symlink():
            raise OciBuildError("oci_layout_malformed")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciBuildError("oci_layout_malformed") from error


def _blob(layout: Path, digest: str) -> Path:
    if _DIGEST.fullmatch(digest) is None:
        raise OciBuildError("oci_layout_malformed")
    blob_root = layout / "blobs"
    algorithm_root = blob_root / "sha256"
    if (
        not blob_root.is_dir()
        or blob_root.is_symlink()
        or not algorithm_root.is_dir()
        or algorithm_root.is_symlink()
    ):
        raise OciBuildError("oci_layout_malformed")
    path = algorithm_root / digest.removeprefix("sha256:")
    try:
        valid = path.is_file() and not path.is_symlink() and sha256_file(path) == digest
    except OSError as error:
        raise OciBuildError("oci_digest_mismatch") from error
    if not valid:
        raise OciBuildError("oci_digest_mismatch")
    return path


def _descriptor_blob(
    layout: Path,
    descriptor: object,
    media_types: frozenset[str],
) -> tuple[Path, str]:
    if not isinstance(descriptor, Mapping):
        raise OciBuildError("oci_layout_malformed")
    media_type = descriptor.get("mediaType")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if (
        media_type not in media_types
        or not isinstance(digest, str)
        or type(size) is not int
        or size < 0
    ):
        raise OciBuildError("oci_layout_malformed")
    blob = _blob(layout, digest)
    try:
        matches_size = blob.stat().st_size == size
    except OSError as error:
        raise OciBuildError("oci_digest_mismatch") from error
    if not matches_size:
        raise OciBuildError("oci_layout_malformed")
    return blob, digest


def _platform_name(platform: object) -> tuple[str, str, str, str | None]:
    if not isinstance(platform, Mapping):
        raise OciBuildError("oci_layout_malformed")
    os_name = platform.get("os")
    architecture = platform.get("architecture")
    variant = platform.get("variant")
    if (
        not isinstance(os_name, str)
        or not isinstance(architecture, str)
        or variant is not None and not isinstance(variant, str)
    ):
        raise OciBuildError("oci_layout_malformed")
    name = f"{os_name}/{architecture}" + (f"/{variant}" if variant else "")
    if _PLATFORM.fullmatch(name) is None:
        raise OciBuildError("oci_layout_malformed")
    return name, os_name, architecture, variant


def _index_manifests(value: object) -> list[object]:
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 2
        or value.get("mediaType") not in {None, _INDEX_MEDIA_TYPE}
        or not isinstance(value.get("manifests"), list)
    ):
        raise OciBuildError("oci_layout_malformed")
    return value["manifests"]


def _image_manifest_descriptors(
    layout: Path,
    index: object,
    *,
    nested: bool = False,
) -> tuple[Mapping[str, object], ...]:
    """Return the platform manifests from an OCI layout's bounded index graph.

    OCI directory transports are allowed to put a named image-index descriptor
    in the layout's root ``index.json``.  Buildah 1.33 uses that representation
    for a pushed manifest list, while small synthetic layouts often place the
    platform manifests directly in the root index.  Support both forms, but do
    not recursively walk arbitrary producer-controlled index graphs.
    """

    manifests: list[Mapping[str, object]] = []
    for descriptor in _index_manifests(index):
        if not isinstance(descriptor, Mapping):
            raise OciBuildError("oci_layout_malformed")
        media_type = descriptor.get("mediaType")
        if media_type == _MANIFEST_MEDIA_TYPE:
            manifests.append(descriptor)
            continue
        if media_type != _INDEX_MEDIA_TYPE or nested:
            raise OciBuildError("oci_layout_malformed")
        nested_blob, _ = _descriptor_blob(
            layout, descriptor, frozenset({_INDEX_MEDIA_TYPE})
        )
        manifests.extend(
            _image_manifest_descriptors(layout, _read_json(nested_blob), nested=True)
        )
    return tuple(manifests)


def inspect_layout(layout: Path, target: OciTarget, labels: Mapping[str, str]) -> OciTargetResult:
    if not layout.is_dir() or layout.is_symlink():
        raise OciBuildError("oci_layout_malformed")
    if _read_json(layout / "oci-layout") != {"imageLayoutVersion": "1.0.0"}:
        raise OciBuildError("oci_layout_malformed")
    index_path = layout / "index.json"
    index = _read_json(index_path)
    root_descriptors = _index_manifests(index)
    if len(root_descriptors) != 1:
        raise OciBuildError("oci_layout_malformed")
    _, publication_manifest_digest = _descriptor_blob(
        layout,
        root_descriptors[0],
        frozenset({_INDEX_MEDIA_TYPE, _MANIFEST_MEDIA_TYPE}),
    )
    results: dict[str, OciPlatformResult] = {}
    for descriptor in _image_manifest_descriptors(layout, index):
        declared_platform = descriptor.get("platform")
        declared = (
            None
            if declared_platform is None
            else _platform_name(declared_platform)
        )
        manifest_blob, manifest_digest = _descriptor_blob(
            layout, descriptor, frozenset({_MANIFEST_MEDIA_TYPE})
        )
        manifest = _read_json(manifest_blob)
        if (
            not isinstance(manifest, dict)
            or manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") not in {None, _MANIFEST_MEDIA_TYPE}
        ):
            raise OciBuildError("oci_layout_malformed")
        config = manifest.get("config")
        layers = manifest.get("layers")
        if (
            not isinstance(config, Mapping)
            or not isinstance(layers, list)
            or not 1 <= len(layers) <= _MAXIMUM_LAYERS
        ):
            raise OciBuildError("oci_layout_malformed")
        config_blob, config_digest = _descriptor_blob(
            layout, config, frozenset({_CONFIG_MEDIA_TYPE})
        )
        config_payload = _read_json(config_blob)
        if (
            not isinstance(config_payload, dict)
            or not isinstance(config_payload.get("config"), dict)
        ):
            raise OciBuildError("oci_layout_malformed")
        config_platform = _platform_name(
            {
                "os": config_payload.get("os"),
                "architecture": config_payload.get("architecture"),
                "variant": config_payload.get("variant"),
            }
        )
        if declared is not None and declared != config_platform:
            raise OciBuildError("oci_layout_malformed")
        name = config_platform[0]
        if name in results:
            raise OciBuildError("oci_layout_malformed")
        runtime = config_payload["config"]
        actual_labels = runtime.get("Labels") or {}
        if not isinstance(actual_labels, dict) or actual_labels != dict(labels):
            raise OciBuildError("metadata_mismatch")
        expected_user = "" if target.required_user is None else target.required_user
        if runtime.get("User", "") != expected_user:
            raise OciBuildError("assertion_failed")
        entrypoint = runtime.get("Entrypoint") or ()
        command = runtime.get("Cmd") or ()
        ports = runtime.get("ExposedPorts") or {}
        if (
            not isinstance(entrypoint, (list, tuple))
            or not all(isinstance(item, str) for item in entrypoint)
            or tuple(entrypoint) != target.required_entrypoint
        ):
            raise OciBuildError("assertion_failed")
        if (
            not isinstance(command, (list, tuple))
            or not all(isinstance(item, str) for item in command)
            or tuple(command) != target.required_command
        ):
            raise OciBuildError("assertion_failed")
        if (
            not isinstance(ports, Mapping)
            or not all(isinstance(key, str) for key in ports)
            or tuple(sorted(ports)) != tuple(sorted(target.required_ports))
        ):
            raise OciBuildError("assertion_failed")
        rootfs = config_payload.get("rootfs")
        if (
            not isinstance(rootfs, dict)
            or rootfs.get("type") != "layers"
            or not isinstance(rootfs.get("diff_ids"), list)
            or len(rootfs["diff_ids"]) != len(layers)
            or any(
                not isinstance(diff_id, str) or _DIGEST.fullmatch(diff_id) is None
                for diff_id in rootfs["diff_ids"]
            )
        ):
            raise OciBuildError("oci_layout_malformed")
        layer_digests: list[str] = []
        for layer in layers:
            _, layer_digest = _descriptor_blob(layout, layer, _LAYER_MEDIA_TYPES)
            layer_digests.append(layer_digest)
        results[name] = OciPlatformResult(name, manifest_digest, config_digest, tuple(layer_digests))
    if tuple(sorted(results)) != tuple(sorted(target.platforms)):
        raise OciBuildError("platform_mismatch")
    return OciTargetResult(
        target_id=target.target_id,
        index_digest=sha256_file(index_path),
        publication_manifest_digest=publication_manifest_digest,
        platform_results=tuple(results[name] for name in sorted(results)),
        labels=dict(sorted(labels.items())),
        smoke_result="not-run",
    )


def verify_no_secret_leakage(layout: Path, secret_files: Mapping[str, Path]) -> None:
    needles: set[bytes] = set()
    for path in secret_files.values():
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise OciBuildError("secret_permissions_invalid")
        value = path.read_bytes()
        needles.add(value)
        needles.add(hashlib.sha256(value).hexdigest().encode())
    if not needles:
        return
    for file in layout.rglob("*"):
        if file.is_file() and not file.is_symlink():
            content = file.read_bytes()
            if any(needle and needle in content for needle in needles):
                raise OciBuildError("secret_leakage")


def _buildah_base(roots: CapacityRoots, driver: str) -> list[str]:
    return [
        "buildah", "--storage-driver", driver,
        "--root", "/var/lib/containers/storage", "--runroot", str(roots.run_root),
    ]


def _credential_free_authfile(root: Path, *, replace_existing: bool = False) -> Path:
    path = root / "auth.json"
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise OciBuildError("cleanup_failed") from error
    else:
        if not replace_existing:
            raise OciBuildError("residue_detected")
        try:
            path.unlink()
        except OSError as error:
            raise OciBuildError("cleanup_failed") from error
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, b'{"auths":{}}\n')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise OciBuildError("cleanup_failed") from error
    return path


def _credential_free_cleanup_authfile(root: Path, root_fd: int) -> Path:
    """Replace cleanup auth state relative to the verified scratch inode."""

    try:
        try:
            os.stat("auth.json", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            os.unlink("auth.json", dir_fd=root_fd)
        descriptor = os.open(
            "auth.json",
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=root_fd,
        )
        try:
            payload = b'{"auths":{}}\n'
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError(errno.EIO, "short cleanup auth write")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(root_fd)
    except OSError as error:
        raise OciBuildError("cleanup_failed") from error
    return root / "auth.json"


def credential_free_environment(
    authfile: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Pin every Buildah operation to the per-run empty auth file."""

    source = os.environ if environment is None else environment
    result = {
        key: value
        for key, value in source.items()
        if key in _SAFE_RUNTIME_ENVIRONMENT
        and _SENSITIVE_ENVIRONMENT.search(key) is None
    }
    result["REGISTRY_AUTH_FILE"] = str(authfile)
    return result


def _write_private_runtime_files(roots: CapacityRoots) -> tuple[Path, Path, Path]:
    root = roots.scratch_root
    for directory in (
        root / "home",
        root / "implicit-containers",
        root / "implicit-containers" / "storage",
        root / "xdg-data",
        root / "xdg-cache",
        root / "xdg-config",
        root / "xdg-runtime",
        root / "tmp",
    ):
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    storage = root / "storage.conf"
    policy = root / "policy.json"
    registries = root / "registries.conf"
    storage.write_text(
        "[storage]\n"
        'driver="vfs"\n'
        f'runroot="{roots.run_root}"\n'
        'graphroot="/var/lib/containers/storage"\n'
        "[storage.options]\n"
        "additionalimagestores=[]\n"
        "[storage.options.pull_options]\n"
        'enable_partial_images="false"\n'
        'use_hard_links="false"\n',
        encoding="utf-8",
    )
    # The acquisition policy admits no mutable name: every remote source is a
    # central-host-allowed sha256 reference and the complete descriptor graph
    # is independently hashed before import.  Skopeo still requires a signature
    # policy file, so this per-run file accepts transport bytes only inside that
    # stronger content-identity boundary.
    policy.write_text(
        json.dumps({"default": [{"type": "insecureAcceptAnything"}]}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    registries.write_text(
        "unqualified-search-registries = []\n"
        "short-name-mode = \"disabled\"\n",
        encoding="utf-8",
    )
    storage.chmod(0o600)
    policy.chmod(0o600)
    registries.chmod(0o600)
    return storage, policy, registries


def _ensure_cleanup_storage_config_unchecked(roots: CapacityRoots) -> Path:
    root = roots.scratch_root
    for directory in (
        root / "home",
        root / "implicit-containers",
        root / "implicit-containers" / "storage",
        root / "xdg-data",
        root / "xdg-cache",
        root / "xdg-config",
        root / "xdg-runtime",
        root / "tmp",
    ):
        if directory.is_symlink():
            raise OciBuildError("cleanup_failed")
        directory.mkdir(parents=False, mode=0o700, exist_ok=True)
        if not directory.is_dir():
            raise OciBuildError("cleanup_failed")
    storage = root / "storage.conf"
    if storage.is_symlink():
        raise OciBuildError("cleanup_failed")
    if not storage.exists():
        storage.write_text(
            "[storage]\n"
            'driver="vfs"\n'
            f'runroot="{roots.run_root}"\n'
            'graphroot="/var/lib/containers/storage"\n'
            "[storage.options]\n"
            "additionalimagestores=[]\n",
            encoding="utf-8",
        )
        storage.chmod(0o600)
    if not storage.is_file():
        raise OciBuildError("cleanup_failed")
    return storage


def _ensure_cleanup_storage_config(roots: CapacityRoots) -> Path:
    try:
        return _ensure_cleanup_storage_config_unchecked(roots)
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("cleanup_failed") from error


def _ensure_cleanup_storage_config_at(
    roots: CapacityRoots, root_fd: int
) -> Path:
    """Prepare cleanup runtime files beneath a verified scratch descriptor."""

    scratch_mount_id = _directory_identity(root_fd).mount_id

    def ensure_directory(parent_fd: int, name: str) -> int:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        identity = _directory_identity(descriptor)
        if (
            stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700
            or identity.mount_id != scratch_mount_id
        ):
            os.close(descriptor)
            raise OciBuildError("cleanup_failed")
        return descriptor

    try:
        implicit_fd: int | None = None
        for name in (
            "home",
            "implicit-containers",
            "xdg-data",
            "xdg-cache",
            "xdg-config",
            "xdg-runtime",
            "tmp",
        ):
            descriptor = ensure_directory(root_fd, name)
            if name == "implicit-containers":
                implicit_fd = descriptor
            else:
                os.close(descriptor)
        assert implicit_fd is not None
        try:
            os.close(ensure_directory(implicit_fd, "storage"))
        finally:
            os.close(implicit_fd)
        try:
            metadata = os.stat("storage.conf", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            descriptor = os.open(
                "storage.conf",
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=root_fd,
            )
            try:
                payload = (
                    "[storage]\n"
                    'driver="vfs"\n'
                    f'runroot="{roots.run_root}"\n'
                    'graphroot="/var/lib/containers/storage"\n'
                    "[storage.options]\n"
                    "additionalimagestores=[]\n"
                ).encode()
                offset = 0
                while offset < len(payload):
                    written = os.write(descriptor, payload[offset:])
                    if written <= 0:
                        raise OSError(errno.EIO, "short storage config write")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            metadata = os.stat("storage.conf", dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise OciBuildError("cleanup_failed")
        os.fsync(root_fd)
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("cleanup_failed") from error
    return roots.scratch_root / "storage.conf"


def _read_cleanup_manifests(root_fd: int) -> tuple[list[str], bool]:
    try:
        descriptor = os.open(
            "manifests.json",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=root_fd,
        )
    except FileNotFoundError:
        return [], True
    except OSError:
        return [], False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
            return [], False
        payload = b""
        while len(payload) <= 1024 * 1024:
            chunk = os.read(descriptor, 1024 * 1024 + 1 - len(payload))
            if not chunk:
                break
            payload += chunk
        if len(payload) > 1024 * 1024:
            return [], False
        value = json.loads(payload)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return [], False
        return value, True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], False
    finally:
        os.close(descriptor)


def _private_builder_environment(
    root: Path,
    authfile: Path,
    storage_config: Path,
    environment: Mapping[str, str],
    registries_config: Path | None = None,
) -> dict[str, str]:
    result = credential_free_environment(authfile, environment)
    result.update(
        {
            "CONTAINERS_STORAGE_CONF": str(storage_config),
            "HOME": str(root / "home"),
            "XDG_CACHE_HOME": str(root / "xdg-cache"),
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "XDG_DATA_HOME": str(root / "xdg-data"),
            "XDG_RUNTIME_DIR": str(root / "xdg-runtime"),
            "TMPDIR": str(root / "tmp"),
        }
    )
    if registries_config is not None:
        result["CONTAINERS_REGISTRIES_CONF"] = str(registries_config)
    return result


def _reference_host(reference: str) -> str:
    return reference.split("/", 1)[0]


def _record_base_image_id(
    image_ids: dict[str, dict[str, str]],
    build_platforms: Sequence[str],
    reference: str,
    image_id: str,
) -> None:
    for build_platform in build_platforms:
        existing_image_id = image_ids[build_platform].get(reference)
        if existing_image_id is not None and existing_image_id != image_id:
            raise OciBuildError("input_lock_mismatch")
        image_ids[build_platform][reference] = image_id


def _verify_materialized_external_inputs(
    context: Path,
    materialized: MaterializedTargetInputs,
) -> None:
    """Re-prove immutable input bytes after every Dockerfile instruction ran."""

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    if no_follow == 0 or directory == 0:
        raise OciBuildError("input_materialization_failed")
    evidence_by_id = {
        item.input_id: item
        for item in materialized.evidence.resolved_external_inputs
    }
    if set(evidence_by_id) != {
        item.input_id for item in materialized.lock.external_inputs
    }:
        raise OciBuildError("input_materialization_failed")
    descriptors: list[int] = []
    try:
        context_descriptor = os.open(
            context,
            os.O_RDONLY | directory | no_follow | close_on_exec,
        )
        descriptors.append(context_descriptor)
        for locked in materialized.lock.external_inputs:
            parts = PurePosixPath(locked.destination).parts
            if not parts:
                raise OciBuildError("input_materialization_failed")
            parent_descriptor = context_descriptor
            opened_parents: list[int] = []
            try:
                for part in parts[:-1]:
                    parent_descriptor = os.open(
                        part,
                        os.O_RDONLY | directory | no_follow | close_on_exec,
                        dir_fd=parent_descriptor,
                    )
                    opened_parents.append(parent_descriptor)
                file_descriptor = os.open(
                    parts[-1],
                    os.O_RDONLY | no_follow | close_on_exec,
                    dir_fd=parent_descriptor,
                )
                try:
                    metadata = os.fstat(file_descriptor)
                    expected = evidence_by_id[locked.input_id]
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                        or stat.S_IMODE(metadata.st_mode) & 0o222
                        or metadata.st_size != expected.size_bytes
                        or metadata.st_size > locked.maximum_bytes
                        or expected.digest != f"sha256:{locked.sha256}"
                    ):
                        raise OciBuildError("input_materialization_failed")
                    digest = hashlib.sha256()
                    while True:
                        block = os.read(file_descriptor, 1024 * 1024)
                        if not block:
                            break
                        digest.update(block)
                    if digest.hexdigest() != locked.sha256:
                        raise OciBuildError("input_materialization_failed")
                finally:
                    os.close(file_descriptor)
            finally:
                for descriptor in reversed(opened_parents):
                    os.close(descriptor)
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("input_materialization_failed") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _import_base_platform(
    child_layout: Path,
    *,
    roots: CapacityRoots,
    authfile: Path,
    policy_file: Path,
    builder_environment: Mapping[str, str],
    target: OciTarget,
    base_lock: OciBaseLock,
    platform: str,
    manifest_digest: str,
    config_digest: str,
) -> str:
    root = roots.scratch_root
    token = hashlib.sha256(
        f"{target.target_id}:{base_lock.stage_id}:{platform}:{manifest_digest}".encode()
    ).hexdigest()[:16]
    alias = (
        f"localhost/ciw-input/{target.target_id}-{base_lock.stage_id}:"
        f"{platform.replace('/', '-')}-{token}"
    )
    storage_transport = (
        f"containers-storage:[vfs@/var/lib/containers/storage+{roots.run_root}]{alias}"
    )
    prefix = ["skopeo", "--policy", str(policy_file)]
    execute_engine_command(
        roots,
        [
            *prefix,
            "--tmpdir",
            str(root / "tmp"),
            "copy",
            "--src-authfile",
            str(authfile),
            "--dest-authfile",
            str(authfile),
            "--preserve-digests",
            # The verified acquisition layout has exactly one root descriptor
            # and intentionally carries no mutable ref-name annotation.  An
            # empty OCI transport selector therefore chooses that sole
            # descriptor; spelling ``:locked`` would instead require an
            # org.opencontainers.image.ref.name annotation that is absent.
            f"oci:{child_layout}",
            storage_transport,
        ],
        env=builder_environment,
    )
    raw = capture_engine_bytes(
        roots,
        [*prefix, "inspect", "--raw", "--authfile", str(authfile), storage_transport],
        env=builder_environment,
    )
    config = capture_engine_bytes(
        roots,
        [
            *prefix,
            "inspect",
            "--raw",
            "--config",
            "--authfile",
            str(authfile),
            storage_transport,
        ],
        env=builder_environment,
    )
    if (
        "sha256:" + hashlib.sha256(raw).hexdigest() != manifest_digest
        or "sha256:" + hashlib.sha256(config).hexdigest() != config_digest
    ):
        raise OciBuildError("base_import_failed")
    image_ids = execute_engine_command(
        roots,
        [*_buildah_base(roots, "vfs"), "images", "--no-trunc", "--quiet", alias],
        capture=True,
        env=builder_environment,
    ).stdout.splitlines()
    if (
        len(image_ids) != 1
        or _SAFE_IMAGE_ID.fullmatch(image_ids[0]) is None
        or image_ids[0] != config_digest
    ):
        raise OciBuildError("base_import_failed")
    return image_ids[0]


def _materialize_target_inputs(
    source_root: Path,
    plan: OciBuildPlan,
    target: OciTarget,
    staged_root: Path,
    roots: CapacityRoots,
    authfile: Path,
    policy_file: Path,
    builder_environment: Mapping[str, str],
) -> MaterializedTargetInputs:
    root = roots.scratch_root
    policy = plan.input_policies.get(target.input_policy_id)
    if policy is None:
        raise OciBuildError("input_policy_mismatch")
    if not target.build_input_lock_path:
        raise OciBuildError("input_lock_incomplete")
    try:
        tracked_lock = execute_command(
            [
                "git",
                "ls-files",
                "--error-unmatch",
                "--",
                target.build_input_lock_path,
            ],
            cwd=source_root,
            capture=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise OciBuildError("input_lock_path_invalid") from error
    if tracked_lock != target.build_input_lock_path:
        raise OciBuildError("input_lock_path_invalid")
    lock = load_input_lock_contract(
        source_root,
        target.build_input_lock_path,
        product_id=plan.product_id,
        target_id=target.target_id,
        input_policy_id=target.input_policy_id,
        expected_platforms=target.platforms,
    )
    validate_target_dockerfile_lock(
        staged_root / target.dockerfile_path, lock, target.platforms
    )
    image_ids: dict[str, dict[str, str]] = {
        platform: {} for platform in target.platforms
    }
    resolved_bases: list[OciResolvedBase] = []
    for base_lock in lock.bases:
        reference = base_lock.declared_reference
        if base_lock.kind != "external":
            continue
        if _reference_host(reference) not in policy.allowed_registry_hosts:
            raise OciBuildError("input_host_forbidden")
        locked_platforms = {
            identity.platform: identity for identity in base_lock.platform_identities
        }
        if set(locked_platforms) != set(base_lock.platforms):
            raise OciBuildError("input_lock_mismatch")
        if len(policy.allowed_registry_api_hosts) != 1:
            raise OciBuildError("input_policy_mismatch")
        acquisition_parent = root / "input-layouts" / target.target_id
        acquisition_parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        acquisition = acquisition_parent / base_lock.stage_id
        try:
            acquired = acquire_oci_base(
                OciRegistryAcquisitionRequest(
                    reference=reference,
                    platform_manifest_digests=tuple(
                        (identity.platform, identity.manifest_digest)
                        for identity in base_lock.platform_identities
                    ),
                    registry_api_host=policy.allowed_registry_api_hosts[0],
                    allowed_reference_hosts=policy.allowed_registry_hosts,
                    allowed_registry_api_hosts=policy.allowed_registry_api_hosts,
                    allowed_token_hosts=policy.allowed_registry_token_hosts,
                    allowed_blob_hosts=policy.allowed_registry_blob_hosts,
                    maximum_redirects=policy.maximum_redirects,
                ),
                registered_state=acquisition.resolve(strict=False),
            )
        except OciRegistryAcquisitionError as error:
            raise OciBuildError(error.code) from error
        root_layout = acquired.root_layout
        child_layouts = acquired.child_layouts
        try:
            inspection = oci_base_inspection.inspect_oci_base_layout(
                root_layout,
                reference,
                base_lock.platforms,
                child_layouts,
            )
        except oci_base_inspection.OciBaseInspectionError as error:
            raise OciBuildError("base_acquisition_failed") from error
        if any(
            identity.manifest_digest
            != locked_platforms[identity.platform].manifest_digest
            or identity.config_digest
            != locked_platforms[identity.platform].config_digest
            for identity in inspection.platforms
        ):
            raise OciBuildError("input_lock_mismatch")
        platform_evidence: list[OciResolvedBasePlatform] = []
        for identity in inspection.platforms:
            import_layout = child_layouts.get(identity.platform, root_layout)
            image_id = _import_base_platform(
                import_layout,
                roots=roots,
                authfile=authfile,
                policy_file=policy_file,
                builder_environment=builder_environment,
                target=target,
                base_lock=base_lock,
                platform=identity.platform,
                manifest_digest=identity.manifest_digest,
                config_digest=identity.config_digest,
            )
            build_platforms = (
                target.platforms
                if base_lock.dockerfile_platform is not None
                else (identity.platform,)
            )
            _record_base_image_id(
                image_ids, build_platforms, reference, image_id
            )
            platform_evidence.append(
                OciResolvedBasePlatform(
                    platform=identity.platform,
                    manifest_digest=identity.manifest_digest,
                    config_digest=identity.config_digest,
                )
            )
        resolved_bases.append(
            OciResolvedBase(
                stage_id=base_lock.stage_id,
                declared_reference=reference,
                root_digest=inspection.root_digest,
                platforms=tuple(platform_evidence),
            )
        )
    context = staged_root if target.context_path == "." else staged_root / target.context_path
    reserved_input_root = context / ".ciw-build-inputs"
    if reserved_input_root.exists() or reserved_input_root.is_symlink():
        raise OciBuildError("input_materialization_failed")
    resolved_inputs: list[OciResolvedExternalInput] = []
    for external in lock.external_inputs:
        parsed_host = urlsplit(external.url).hostname
        if (
            parsed_host not in policy.allowed_download_hosts
            or external.maximum_bytes > policy.maximum_input_bytes
        ):
            raise OciBuildError("input_policy_mismatch")
        try:
            verified = download_oci_input(
                OciInputDownloadRequest(
                    input_id=external.input_id,
                    source_url=external.url,
                    sha256=external.sha256,
                    maximum_bytes=external.maximum_bytes,
                    destination=external.destination,
                    allowed_hosts=policy.allowed_download_hosts,
                    maximum_redirects=policy.maximum_redirects,
                ),
                registered_state=context,
            )
        except FoundationError as error:
            raise OciBuildError(error.instruction) from error
        except Exception as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise OciBuildError("external_input_failed") from error
        resolved_inputs.append(
            OciResolvedExternalInput(
                input_id=verified.input_id,
                digest=f"sha256:{verified.sha256}",
                size_bytes=verified.size_bytes,
            )
        )
    payload = {
        "lock_digest": lock.lock_digest,
        "input_policy_id": policy.policy_id,
        "bases": [item.to_dict() for item in resolved_bases],
        "external_inputs": [item.to_dict() for item in resolved_inputs],
    }
    evidence_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return MaterializedTargetInputs(
        lock=lock,
        evidence=OciBuildInputEvidence(
            lock_digest=lock.lock_digest,
            acquisition_policy_id=policy.policy_id,
            resolved_bases=tuple(resolved_bases),
            resolved_external_inputs=tuple(resolved_inputs),
            evidence_id=evidence_id,
        ),
        image_ids_by_platform=image_ids,
    )


def verify_builder_runtime(roots: CapacityRoots, environment: Mapping[str, str]) -> None:
    for tool in ("buildah", "skopeo", "podman"):
        if shutil.which(tool) is None:
            raise OciBuildError("builder_unavailable")
    for tool in ("docker", "dockerd"):
        if shutil.which(tool) is not None:
            raise OciBuildError("forbidden_engine_present")
    for socket in (Path("/var/run/docker.sock"), Path("/run/docker.sock")):
        if socket.exists():
            raise OciBuildError("forbidden_socket_present")
    for tool in ("buildah", "skopeo", "podman"):
        execute_engine_command(roots, [tool, "--version"], capture=True, env=environment)


def build_target(
    plan: OciBuildPlan,
    target: OciTarget,
    staged_root: Path,
    roots: CapacityRoots,
    labels: Mapping[str, str],
    epoch: int,
    secret_files: Mapping[str, Path],
    authfile: Path,
    builder_environment: Mapping[str, str],
    materialized_inputs: MaterializedTargetInputs,
    policy_file: Path | None = None,
) -> tuple[OciTargetResult, str]:
    root = roots.scratch_root
    materialized = materialized_inputs
    base = _buildah_base(roots, plan.storage_driver)
    token = hashlib.sha256(f"{plan.admitted_sha}:{target.target_id}".encode()).hexdigest()[:16]
    manifest = f"ciw-{target.target_id}-{token}"
    execute_engine_command(
        roots, [*base, "manifest", "create", manifest], env=builder_environment
    )
    state_file = root / "manifests.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    current = [] if not state_file.exists() else json.loads(state_file.read_text())
    current.append(manifest)
    state_file.write_text(json.dumps(current, sort_keys=True) + "\n")
    context = staged_root if target.context_path == "." else staged_root / target.context_path
    dockerfile = staged_root / target.dockerfile_path
    for platform in target.platforms:
        argv = [
            *base,
            "bud",
            "--authfile", str(authfile),
            *([] if policy_file is None else ["--signature-policy", str(policy_file)]),
            "--pull=never",
            "--network",
            "none",
            "--http-proxy=false",
            "--layers=false",
            "--no-cache",
            "--identity-label=false",
            "--platform", platform, "--manifest", manifest,
            "--timestamp", str(epoch), "--file", str(dockerfile),
        ]
        if target.target_stage:
            argv.extend(["--target", target.target_stage])
        for key, value in sorted(target.fixed_build_args.items()):
            argv.extend(["--build-arg", f"{key}={value}"])
        for key, value in sorted(labels.items()):
            argv.extend(["--label", f"{key}={value}"])
        for declared_reference, image_id in sorted(
            materialized.image_ids_by_platform.get(platform, {}).items()
        ):
            argv.extend(
                [
                    "--build-context",
                    f"{declared_reference}=container-image://{image_id}",
                ]
            )
        for secret_id in target.secret_mount_ids:
            path = secret_files.get(secret_id)
            if path is None:
                raise OciBuildError("secret_mount_missing")
            argv.extend(["--secret", f"id={secret_id},src={path}"])
        argv.append(str(context))
        execute_engine_command(
            roots, argv, cwd=staged_root, env=builder_environment
        )
    _verify_materialized_external_inputs(context, materialized)
    layout = root / "layouts" / target.target_id
    layout.parent.mkdir(parents=True, exist_ok=True)
    execute_engine_command(
        roots,
        [
            *base,
            "manifest",
            "push",
            "--authfile",
            str(authfile),
            "--all",
            manifest,
            f"oci:{layout}:validation",
        ],
        env=builder_environment,
    )
    result = inspect_layout(layout, target, labels)
    verify_no_secret_leakage(layout, secret_files)
    # Consumer smoke is performed only by oci_execution_safe in a networkless,
    # capability-dropped container.  The base builder must never execute caller
    # scripts on the privileged Buildah host.
    return replace(
        result,
        smoke_result="skipped",
        build_input_evidence=materialized.evidence,
    ), manifest


def execute_plan(
    repository_root: Path,
    source_root: Path,
    plan: OciBuildPlan,
    environment: Mapping[str, str],
    secret_files: Mapping[str, Path] | None = None,
    *,
    _capacity_roots: CapacityRoots | None = None,
) -> OciBuildResult:
    if plan.builder_id != "buildah-v1":
        raise OciBuildError("invalid_contract")
    assert_clean_source(source_root, plan.admitted_sha)
    roots = _capacity_roots or build_capacity_roots(environment)
    if roots.domain != "oci-build" or roots.prefix != "ciw-oci":
        raise OciBuildError("capacity_identity_invalid")
    prepare_capacity_roots(roots)
    root = roots.scratch_root
    authfile = _credential_free_authfile(root)
    storage_config, policy_file, registries_config = _write_private_runtime_files(roots)
    builder_environment = _private_builder_environment(
        root, authfile, storage_config, environment, registries_config
    )
    verify_builder_runtime(roots, builder_environment)
    contract = load_contract(repository_root)
    epoch = source_date_epoch(source_root)
    secrets = dict(secret_files or {})
    results: list[OciTargetResult] = []
    staged_targets: dict[str, Path] = {}
    for target in plan.targets:
        staged = stage_context(source_root, target, root / "staged" / target.target_id)
        staged_targets[target.target_id] = staged
    materialized_targets: dict[str, MaterializedTargetInputs] = {}
    for target in plan.targets:
        materialized_targets[target.target_id] = _materialize_target_inputs(
            source_root,
            plan,
            target,
            staged_targets[target.target_id],
            roots,
            authfile,
            policy_file,
            builder_environment,
        )
    # The loops above form the hard input barrier: every source lock, base
    # descriptor graph, local import and external blob is verified before the
    # first consumer Dockerfile instruction can execute below.
    for target in plan.targets:
        staged = staged_targets[target.target_id]
        labels = metadata_labels(contract, plan, target, epoch)
        result, _ = build_target(
            plan,
            target,
            staged,
            roots,
            labels,
            epoch,
            secrets,
            authfile,
            builder_environment,
            materialized_targets[target.target_id],
            policy_file,
        )
        results.append(result)
    assert_clean_source(source_root, plan.admitted_sha)
    evidence_id = oci_build_evidence_id(
        plan.admitted_sha,
        plan.product_id,
        plan.release_version,
        results,
        plan.canary_id,
        plan.previous_known_good,
        plan.rollback_id,
    )
    result = OciBuildResult(
        product_id=plan.product_id,
        admitted_sha=plan.admitted_sha,
        release_version=plan.release_version,
        source_date_epoch=epoch,
        targets=tuple(results),
        clean_tree=True,
        cleanup_result="not-run",
        evidence_id=evidence_id,
        canary_id=plan.canary_id,
        previous_known_good=plan.previous_known_good,
        rollback_id=plan.rollback_id,
    )
    _write_build_result_file(root / "result.json", result)
    return result


def cleanup(
    environment: Mapping[str, str],
    storage_driver: str = "vfs",
    *,
    _capacity_roots: CapacityRoots | None = None,
) -> None:
    roots = _capacity_roots or build_capacity_roots(environment)
    _validate_capacity_parents(roots)
    root = roots.scratch_root
    if not any(
        _capacity_residue_names(parent, roots)
        for _key, parent in _capacity_rows(roots)
    ):
        return
    failures = False
    cleanup_bound: tuple[
        Mapping[str, int],
        Mapping[str, DirectoryIdentity],
        Mapping[str, int],
        Mapping[str, DirectoryIdentity],
    ] | None = None
    try:
        cleanup_bound = _open_verified_capacity(roots)
        if not _path_matches_directory(
            cleanup_bound[0]["scratch"],
            roots.leaf_name,
            cleanup_bound[3]["scratch"],
        ):
            raise OciBuildError("cleanup_failed")
        scratch_fd = cleanup_bound[2]["scratch"]
        cleanup_authfile = _credential_free_cleanup_authfile(root, scratch_fd)
        storage_config = _ensure_cleanup_storage_config_at(roots, scratch_fd)
        if not _path_matches_directory(
            cleanup_bound[0]["scratch"],
            roots.leaf_name,
            cleanup_bound[3]["scratch"],
        ):
            raise OciBuildError("cleanup_failed")
        registries_config = root / "registries.conf"
        try:
            registries_metadata = os.stat(
                "registries.conf", dir_fd=scratch_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            registries_metadata = None
        except OSError as error:
            raise OciBuildError("cleanup_failed") from error
        if registries_metadata is not None and not stat.S_ISREG(
            registries_metadata.st_mode
        ):
            raise OciBuildError("cleanup_failed")
        cleanup_environment = _private_builder_environment(
            root,
            cleanup_authfile,
            storage_config,
            os.environ,
            registries_config if registries_metadata is not None else None,
        )
    except OciBuildError:
        cleanup_environment = None
        failures = True
    manifests: list[str] = []
    if cleanup_environment is not None and cleanup_bound is not None:
        manifests, manifests_valid = _read_cleanup_manifests(
            cleanup_bound[2]["scratch"]
        )
        if not manifests_valid:
            failures = True
    base = _buildah_base(roots, storage_driver)
    if cleanup_environment is not None and shutil.which("buildah"):
        for manifest in reversed(manifests):
            try:
                result = subprocess.run(
                    [*base, "manifest", "rm", manifest],
                    text=True,
                    capture_output=True,
                    env=cleanup_environment,
                    preexec_fn=_private_engine_preexec(roots),
                )
            except (OSError, subprocess.SubprocessError):
                failures = True
            else:
                if result.returncode != 0 and "no such" not in result.stderr.lower():
                    failures = True
        for command in ([*base, "rm", "--all"], [*base, "rmi", "--all", "--force"]):
            try:
                result = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    env=cleanup_environment,
                    preexec_fn=_private_engine_preexec(roots),
                )
            except (OSError, subprocess.SubprocessError):
                failures = True
            else:
                if result.returncode != 0 and "no such" not in result.stderr.lower():
                    failures = True
    if cleanup_bound is not None:
        parent_fds, _parent_ids, leaf_fds, leaf_ids = cleanup_bound
        for key in reversed(tuple(leaf_fds)):
            if not _remove_open_capacity_leaf(
                parent_fds[key], leaf_fds[key], leaf_ids[key], roots
            ):
                failures = True
        for descriptor in reversed(tuple(leaf_fds.values())):
            os.close(descriptor)
        for descriptor in reversed(tuple(parent_fds.values())):
            os.close(descriptor)
        cleanup_bound = None
    if not remove_capacity_roots(roots):
        failures = True
    if failures:
        raise OciBuildError("cleanup_failed")


def residue(
    environment: Mapping[str, str],
    *,
    _capacity_roots: CapacityRoots | None = None,
) -> None:
    roots = _capacity_roots or build_capacity_roots(environment)
    _validate_capacity_parents(roots)
    if any(
        _capacity_residue_names(parent, roots)
        for _key, parent in _capacity_rows(roots)
    ):
        raise OciBuildError("residue_detected")
