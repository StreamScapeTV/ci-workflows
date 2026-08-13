"""Engine-neutral execution with one reviewed daemonless Buildah adapter."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence
from typing import Callable
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
_SAFE_IMAGE_ID = re.compile(r"^[0-9a-f]{64}$")
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


@dataclass(frozen=True)
class MaterializedTargetInputs:
    lock: OciTargetInputLock
    evidence: OciBuildInputEvidence
    image_ids_by_platform: Mapping[str, Mapping[str, str]]


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


def execute_engine_command(
    root: Path,
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if not argv or Path(argv[0]).name not in {"buildah", "skopeo", "podman"}:
        raise OciBuildError("builder_unavailable")
    try:
        return execute_command(
            argv,
            cwd=cwd,
            capture=capture,
            env=env,
            preexec_fn=_private_engine_preexec(root),
        )
    except subprocess.CalledProcessError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        # subprocess intentionally hides the concrete exception raised by a
        # pre-exec hook. Preserve a closed failure code when namespace setup or
        # process creation is unavailable instead of leaking an untyped
        # traceback past the CIW adapter.
        raise OciBuildError("engine_isolation_failed") from error


def _private_engine_preexec(
    root: Path,
    system_containers: Path = Path("/var/lib/containers"),
) -> Callable[[], None]:
    """Confine rootful containers/image implicit state to registered run state.

    Containers/image 5.26.2, used by the pinned Skopeo 1.13.3, otherwise
    hard-codes its rootful blob-info cache below ``/var/lib/containers`` and
    exposes no CLI cache-path override.  Every engine subprocess therefore
    receives a fresh private mount namespace whose complete implicit containers
    root is a bind mount of this run's registered state.
    """

    if not root.is_absolute() or not system_containers.is_absolute():
        raise OciBuildError("engine_isolation_failed")
    private_root = root / "implicit-containers"

    def prepare() -> None:
        unshare = getattr(os, "unshare", None)
        clone_newns = getattr(os, "CLONE_NEWNS", None)
        if unshare is None or clone_newns is None or _MOUNT is None:
            raise OSError(errno.ENOSYS, "private mount namespaces unavailable")
        private_info = os.lstat(private_root)
        target_info = os.lstat(system_containers)
        if (
            stat.S_ISLNK(private_info.st_mode)
            or not stat.S_ISDIR(private_info.st_mode)
            or stat.S_ISLNK(target_info.st_mode)
            or not stat.S_ISDIR(target_info.st_mode)
        ):
            raise OSError(errno.EPERM, "unsafe implicit containers root")
        unshare(clone_newns)
        if _MOUNT(None, b"/", None, _MOUNT_REC | _MOUNT_PRIVATE, None) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        if (
            _MOUNT(
                os.fsencode(private_root),
                os.fsencode(system_containers),
                None,
                _MOUNT_BIND | _MOUNT_REC,
                None,
            )
            != 0
        ):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))

    return prepare


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def state_root(environment: Mapping[str, str]) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", ".ci-state")).resolve()
    run_id = environment.get("GITHUB_RUN_ID", "local")
    attempt = environment.get("GITHUB_RUN_ATTEMPT", "1")
    token = hashlib.sha256(f"oci-build:{run_id}:{attempt}".encode()).hexdigest()[:16]
    return runner_temp / f"ciw-oci-{token}"


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
    images: list[str] = []
    stage_aliases: set[str] = set()
    logical = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
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
        if target.required_user is not None and runtime.get("User", "") != target.required_user:
            raise OciBuildError("assertion_failed")
        if target.required_entrypoint and tuple(runtime.get("Entrypoint") or ()) != target.required_entrypoint:
            raise OciBuildError("assertion_failed")
        if target.required_command and tuple(runtime.get("Cmd") or ()) != target.required_command:
            raise OciBuildError("assertion_failed")
        if target.required_ports and tuple(sorted((runtime.get("ExposedPorts") or {}).keys())) != tuple(sorted(target.required_ports)):
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


def _buildah_base(root: Path, driver: str) -> list[str]:
    return [
        "buildah", "--storage-driver", driver,
        "--root", str(root / "storage"), "--runroot", str(root / "runroot"),
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


def _write_private_runtime_files(root: Path) -> tuple[Path, Path, Path]:
    graphroot = root / "storage"
    runroot = root / "runroot"
    for directory in (
        graphroot,
        runroot,
        root / "home",
        root / "implicit-containers",
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
        f'runroot="{runroot}"\n'
        f'graphroot="{graphroot}"\n'
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


def _ensure_cleanup_storage_config_unchecked(root: Path) -> Path:
    for directory in (
        root / "storage",
        root / "runroot",
        root / "home",
        root / "implicit-containers",
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
            f'runroot="{root / "runroot"}"\n'
            f'graphroot="{root / "storage"}"\n'
            "[storage.options]\n"
            "additionalimagestores=[]\n",
            encoding="utf-8",
        )
        storage.chmod(0o600)
    if not storage.is_file():
        raise OciBuildError("cleanup_failed")
    return storage


def _ensure_cleanup_storage_config(root: Path) -> Path:
    try:
        return _ensure_cleanup_storage_config_unchecked(root)
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("cleanup_failed") from error


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
    root: Path,
    authfile: Path,
    policy_file: Path,
    builder_environment: Mapping[str, str],
    target: OciTarget,
    base_lock: OciBaseLock,
    platform: str,
    manifest_digest: str,
    config_digest: str,
) -> str:
    token = hashlib.sha256(
        f"{target.target_id}:{base_lock.stage_id}:{platform}:{manifest_digest}".encode()
    ).hexdigest()[:16]
    alias = (
        f"localhost/ciw-input/{target.target_id}-{base_lock.stage_id}:"
        f"{platform.replace('/', '-')}-{token}"
    )
    storage_transport = f"containers-storage:[vfs@{root / 'storage'}+{root / 'runroot'}]{alias}"
    prefix = ["skopeo", "--policy", str(policy_file)]
    execute_engine_command(
        root,
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
            f"oci:{child_layout}:locked",
            storage_transport,
        ],
        env=builder_environment,
    )
    raw = execute_engine_command(
        root,
        [*prefix, "inspect", "--raw", "--authfile", str(authfile), storage_transport],
        capture=True,
        env=builder_environment,
    ).stdout.strip().encode()
    config = execute_engine_command(
        root,
        [
            *prefix,
            "inspect",
            "--raw",
            "--config",
            "--authfile",
            str(authfile),
            storage_transport,
        ],
        capture=True,
        env=builder_environment,
    ).stdout.strip().encode()
    if (
        "sha256:" + hashlib.sha256(raw).hexdigest() != manifest_digest
        or "sha256:" + hashlib.sha256(config).hexdigest() != config_digest
    ):
        raise OciBuildError("base_import_failed")
    image_ids = execute_engine_command(
        root,
        [*_buildah_base(root, "vfs"), "images", "--no-trunc", "--quiet", alias],
        capture=True,
        env=builder_environment,
    ).stdout.splitlines()
    if (
        len(image_ids) != 1
        or _SAFE_IMAGE_ID.fullmatch(image_ids[0]) is None
        or f"sha256:{image_ids[0]}" != config_digest
    ):
        raise OciBuildError("base_import_failed")
    return f"sha256:{image_ids[0]}"


def _materialize_target_inputs(
    source_root: Path,
    plan: OciBuildPlan,
    target: OciTarget,
    staged_root: Path,
    root: Path,
    authfile: Path,
    policy_file: Path,
    builder_environment: Mapping[str, str],
) -> MaterializedTargetInputs:
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
                root=root,
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


def verify_builder_runtime(root: Path, environment: Mapping[str, str]) -> None:
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
        execute_engine_command(root, [tool, "--version"], capture=True, env=environment)


def build_target(
    plan: OciBuildPlan,
    target: OciTarget,
    staged_root: Path,
    root: Path,
    labels: Mapping[str, str],
    epoch: int,
    secret_files: Mapping[str, Path],
    authfile: Path,
    builder_environment: Mapping[str, str],
    materialized_inputs: MaterializedTargetInputs,
    policy_file: Path | None = None,
) -> tuple[OciTargetResult, str]:
    materialized = materialized_inputs
    base = _buildah_base(root, plan.storage_driver)
    token = hashlib.sha256(f"{plan.admitted_sha}:{target.target_id}".encode()).hexdigest()[:16]
    manifest = f"ciw-{target.target_id}-{token}"
    execute_engine_command(
        root, [*base, "manifest", "create", manifest], env=builder_environment
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
            root, argv, cwd=staged_root, env=builder_environment
        )
    _verify_materialized_external_inputs(context, materialized)
    layout = root / "layouts" / target.target_id
    layout.parent.mkdir(parents=True, exist_ok=True)
    execute_engine_command(
        root,
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
) -> OciBuildResult:
    if plan.builder_id != "buildah-v1":
        raise OciBuildError("invalid_contract")
    assert_clean_source(source_root, plan.admitted_sha)
    root = state_root(environment)
    if root.exists() or root.is_symlink():
        raise OciBuildError("residue_detected")
    root.mkdir(parents=True, mode=0o700)
    authfile = _credential_free_authfile(root)
    storage_config, policy_file, registries_config = _write_private_runtime_files(root)
    builder_environment = _private_builder_environment(
        root, authfile, storage_config, environment, registries_config
    )
    verify_builder_runtime(root, builder_environment)
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
            root,
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
            root,
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
    evidence_payload = {
        "api": "oci.build",
        "version": "1.0.0",
        "source": plan.admitted_sha,
        "product": plan.product_id,
        "release_version": plan.release_version,
        "targets": [row.to_dict() for row in results],
        "flux": {
            "canary_id": plan.canary_id,
            "previous_known_good": plan.previous_known_good,
            "rollback_id": plan.rollback_id,
        },
    }
    evidence_id = hashlib.sha256(json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
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
    (root / "result.json").write_text(json.dumps(result.output_values(), sort_keys=True) + "\n")
    return result


def cleanup(environment: Mapping[str, str], storage_driver: str = "vfs") -> None:
    root = state_root(environment)
    try:
        root_mode = root.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as error:
        raise OciBuildError("cleanup_failed") from error

    # A failed or cancelled build may leave an attacker-controlled alias at the
    # deterministic state path.  Never inspect through that alias or hand it to
    # Buildah; unlink the alias itself and leave its target untouched.
    if stat.S_ISLNK(root_mode):
        try:
            root.unlink()
        except OSError as error:
            raise OciBuildError("cleanup_failed") from error
        return
    if not stat.S_ISDIR(root_mode):
        try:
            root.unlink()
        except OSError as error:
            raise OciBuildError("cleanup_failed") from error
        raise OciBuildError("cleanup_failed")

    failures = False
    try:
        cleanup_authfile = _credential_free_authfile(root, replace_existing=True)
        storage_config = _ensure_cleanup_storage_config(root)
        registries_config = root / "registries.conf"
        if registries_config.is_symlink():
            raise OciBuildError("cleanup_failed")
        cleanup_environment = _private_builder_environment(
            root,
            cleanup_authfile,
            storage_config,
            os.environ,
            registries_config if registries_config.is_file() else None,
        )
    except OciBuildError:
        cleanup_environment = None
        failures = True
    state_file = root / "manifests.json"
    manifests: list[str] = []
    try:
        state_mode = state_file.lstat().st_mode
    except FileNotFoundError:
        state_mode = None
    except OSError:
        state_mode = None
        failures = True
    if state_mode is not None:
        if not stat.S_ISREG(state_mode):
            failures = True
        else:
            try:
                value = json.loads(state_file.read_text(encoding="utf-8"))
                if isinstance(value, list) and all(isinstance(item, str) for item in value):
                    manifests = value
                else:
                    failures = True
            except (OSError, json.JSONDecodeError):
                failures = True
    base = _buildah_base(root, storage_driver)
    if shutil.which("buildah") and cleanup_environment is not None:
        for manifest in reversed(manifests):
            try:
                result = subprocess.run(
                    [*base, "manifest", "rm", manifest],
                    text=True,
                    capture_output=True,
                    env=cleanup_environment,
                    preexec_fn=_private_engine_preexec(root),
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
                    preexec_fn=_private_engine_preexec(root),
                )
            except (OSError, subprocess.SubprocessError):
                failures = True
            else:
                if result.returncode != 0 and "no such" not in result.stderr.lower():
                    failures = True
    try:
        if root.is_symlink():
            root.unlink()
        else:
            shutil.rmtree(root)
    except OSError:
        failures = True
    if failures:
        raise OciBuildError("cleanup_failed")


def residue(environment: Mapping[str, str]) -> None:
    root = state_root(environment)
    if root.exists() or root.is_symlink():
        raise OciBuildError("residue_detected")
