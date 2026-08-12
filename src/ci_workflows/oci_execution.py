"""Engine-neutral execution with one reviewed daemonless Buildah adapter."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from .oci_contract import bounded_path, load_contract, metadata_labels
from .oci_types import (
    OciBuildError,
    OciBuildPlan,
    OciBuildResult,
    OciPlatformResult,
    OciTarget,
    OciTargetResult,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FROM = re.compile(r"^\s*FROM(?:\s+--platform=[^\s]+)?\s+([^\s]+)(?:\s+AS\s+[^\s]+)?\s*$", re.I)
_PLATFORM = re.compile(r"^linux/(?:amd64|arm64/v8)$")
_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
_LAYER_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.v1.tar+zstd",
    }
)


def execute_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        check=True,
        text=True,
        capture_output=capture,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def state_root(environment: Mapping[str, str]) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", ".ci-state"))
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
            if "$" in image or (image != "scratch" and "@sha256:" not in image):
                raise OciBuildError("base_identity_mutable")
            if image != "scratch":
                digest = image.rsplit("@", 1)[1]
                if _DIGEST.fullmatch(digest) is None:
                    raise OciBuildError("base_identity_mutable")
            images.append(image)
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
    try:
        path.write_text('{"auths":{}}\n', encoding="utf-8")
        path.chmod(0o600)
    except OSError as error:
        raise OciBuildError("cleanup_failed") from error
    return path


def credential_free_environment(
    authfile: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Pin every Buildah operation to the per-run empty auth file."""

    result = dict(os.environ if environment is None else environment)
    result["REGISTRY_AUTH_FILE"] = str(authfile)
    return result


def verify_builder_runtime() -> None:
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
        execute_command([tool, "--version"], capture=True)


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
) -> tuple[OciTargetResult, str]:
    base = _buildah_base(root, plan.storage_driver)
    token = hashlib.sha256(f"{plan.admitted_sha}:{target.target_id}".encode()).hexdigest()[:16]
    manifest = f"ciw-{target.target_id}-{token}"
    execute_command([*base, "manifest", "create", manifest], env=builder_environment)
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
            "--pull=never",
            "--network",
            "none",
            "--layers=false",
            "--no-cache",
            "--identity-label=false",
            "--inherit-labels=false",
            "--platform", platform, "--manifest", manifest,
            "--timestamp", str(epoch), "--file", str(dockerfile),
        ]
        if target.target_stage:
            argv.extend(["--target", target.target_stage])
        for key, value in sorted(target.fixed_build_args.items()):
            argv.extend(["--build-arg", f"{key}={value}"])
        for key, value in sorted(labels.items()):
            argv.extend(["--label", f"{key}={value}"])
        for secret_id in target.secret_mount_ids:
            path = secret_files.get(secret_id)
            if path is None:
                raise OciBuildError("secret_mount_missing")
            argv.extend(["--secret", f"id={secret_id},src={path}"])
        argv.append(str(context))
        execute_command(argv, cwd=staged_root, env=builder_environment)
    layout = root / "layouts" / target.target_id
    layout.parent.mkdir(parents=True, exist_ok=True)
    execute_command(
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
    return replace(result, smoke_result="skipped"), manifest


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
    verify_builder_runtime()
    root = state_root(environment)
    if root.exists() or root.is_symlink():
        raise OciBuildError("residue_detected")
    root.mkdir(parents=True, mode=0o700)
    authfile = _credential_free_authfile(root)
    builder_environment = credential_free_environment(authfile, environment)
    contract = load_contract(repository_root)
    epoch = source_date_epoch(source_root)
    secrets = dict(secret_files or {})
    results: list[OciTargetResult] = []
    for target in plan.targets:
        staged = stage_context(source_root, target, root / "staged" / target.target_id)
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
        cleanup_environment = credential_free_environment(cleanup_authfile)
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
            result = subprocess.run(
                [*base, "manifest", "rm", manifest],
                text=True,
                capture_output=True,
                env=cleanup_environment,
            )
            if result.returncode != 0 and "no such" not in result.stderr.lower():
                failures = True
        for command in ([*base, "rm", "--all"], [*base, "rmi", "--all", "--force"]):
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                env=cleanup_environment,
            )
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
