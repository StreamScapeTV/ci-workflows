"""Security adapter for central filesystem assertions and isolated OCI smoke."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from . import oci_execution as base
from .oci_types import OciBuildError, OciBuildPlan, OciBuildResult, OciTarget, OciTargetResult


def _layer_paths(layout: Path, layer_digests: Sequence[str]) -> set[str]:
    paths: set[str] = set()
    for digest in layer_digests:
        try:
            archive = tarfile.open(name=base._blob(layout, digest), mode="r:*")
        except (OSError, tarfile.TarError) as error:
            raise OciBuildError("oci_layout_malformed") from error
        additions: dict[str, bool] = {}
        opaque_prefixes: set[str] = set()
        removed_paths: set[str] = set()
        with archive:
            for member in archive:
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise OciBuildError("oci_layout_malformed")
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    # Absolute links are container-root identities, not host
                    # paths. Relative links may traverse within that root (for
                    # example BusyBox applets under /usr/bin commonly point to
                    # ../../bin/busybox). Reject only lexical traversal above
                    # the container root; this scanner never extracts or
                    # follows the links.
                    if link.is_absolute():
                        link_parts = link.parts[1:]
                        depth = 0
                    elif member.islnk():
                        # Tar hard-link names are archive-root relative, while
                        # symbolic-link names are relative to their parent.
                        link_parts = link.parts
                        depth = 0
                    else:
                        link_parts = link.parts
                        depth = len(pure.parent.parts)
                    escaped = False
                    for part in link_parts:
                        if part in {"", ".", "/"}:
                            continue
                        if part == "..":
                            if depth == 0:
                                escaped = True
                                break
                            depth -= 1
                        else:
                            depth += 1
                    if escaped:
                        raise OciBuildError("oci_layout_malformed")
                name = pure.as_posix().lstrip("./")
                if not name:
                    continue
                basename = PurePosixPath(name).name
                parent = PurePosixPath(name).parent.as_posix()
                prefix = "/" + ("" if parent == "." else parent.rstrip("/") + "/")
                if basename == ".wh..wh..opq":
                    opaque_prefixes.add(prefix)
                    continue
                if basename.startswith(".wh."):
                    removed = prefix + basename.removeprefix(".wh.")
                    removed_paths.add(removed)
                    continue
                additions["/" + name.rstrip("/")] = member.isdir()
        # OCI whiteouts remove entries inherited from lower layers, regardless
        # of where the marker appears relative to additions in this layer.
        for prefix in opaque_prefixes:
            paths = {path for path in paths if not path.startswith(prefix)}
        for removed in removed_paths:
            removed_prefix = removed.rstrip("/") + "/"
            paths = {
                path
                for path in paths
                if path != removed and not path.startswith(removed_prefix)
            }
        for added, is_directory in additions.items():
            if not is_directory:
                # A later file or link replacing a lower-layer directory also
                # removes every child inherited below that directory.
                added_prefix = added.rstrip("/") + "/"
                paths = {path for path in paths if not path.startswith(added_prefix)}
            paths.add(added)
    return paths


def _manifest_layer_sets(layout: Path) -> tuple[tuple[str, ...], ...]:
    """Return verified layer sets from direct or Buildah-named OCI indexes."""

    try:
        index = base._read_json(layout / "index.json")
        descriptors = base._image_manifest_descriptors(layout, index)
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("oci_layout_malformed") from error

    sets: list[tuple[str, ...]] = []
    for descriptor in descriptors:
        manifest_blob, _ = base._descriptor_blob(
            layout,
            descriptor,
            frozenset({base._MANIFEST_MEDIA_TYPE}),
        )
        manifest = base._read_json(manifest_blob)
        if (
            not isinstance(manifest, dict)
            or manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") not in {None, base._MANIFEST_MEDIA_TYPE}
            or not isinstance(manifest.get("layers"), list)
        ):
            raise OciBuildError("oci_layout_malformed")
        digests: list[str] = []
        for layer in manifest["layers"]:
            _, digest = base._descriptor_blob(layout, layer, base._LAYER_MEDIA_TYPES)
            digests.append(digest)
        sets.append(tuple(digests))
    return tuple(sets)


def _assert_target_filesystem(layout: Path, target: OciTarget) -> None:
    inventories = [_layer_paths(layout, layers) for layers in _manifest_layer_sets(layout)]
    if not inventories:
        raise OciBuildError("oci_layout_malformed")
    for paths in inventories:
        if any(path not in paths for path in target.required_files):
            raise OciBuildError("assertion_failed")
        basenames = {PurePosixPath(path).name for path in paths}
        if any(tool not in basenames for tool in target.required_tools):
            raise OciBuildError("assertion_failed")
        if any(tool in basenames for tool in target.forbidden_tools):
            raise OciBuildError("assertion_failed")


def _run_isolated_smoke(
    root: Path,
    plan: OciBuildPlan,
    target: OciTarget,
    script: Path,
) -> str:
    if not script.is_file() or script.is_symlink():
        raise OciBuildError("invalid_path")
    token = hashlib.sha256(f"{plan.admitted_sha}:{target.target_id}".encode()).hexdigest()[:16]
    manifest = f"ciw-{target.target_id}-{token}"
    container = f"{manifest}-smoke"
    command = base._buildah_base(root, plan.storage_driver)
    storage_config = base._ensure_cleanup_storage_config(root)
    registries_config = root / "registries.conf"
    builder_environment = base._private_builder_environment(
        root,
        root / "auth.json",
        storage_config,
        {},
        registries_config if registries_config.is_file() else None,
    )
    created = False
    try:
        base.execute_engine_command(
            root,
            [*command, "from", "--name", container, "--platform", "linux/amd64", manifest],
            env=builder_environment,
        )
        created = True
        base.execute_engine_command(
            root,
            [*command, "copy", container, str(script), "/tmp/ciw-smoke.sh"],
            env=builder_environment,
        )
        base.execute_engine_command(
            root,
            [
                *command,
                "run",
                "--network",
                "none",
                "--cap-drop",
                "all",
                "--security-opt",
                "no-new-privileges",
                "--env",
                f"OCI_SOURCE_SHA={plan.admitted_sha}",
                "--env",
                f"OCI_TARGET_ID={target.target_id}",
                container,
                "--",
                "/bin/sh",
                "/tmp/ciw-smoke.sh",
            ],
            capture=True,
            env=builder_environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise OciBuildError("smoke_failed") from error
    finally:
        if created:
            try:
                removed = subprocess.run(
                    [*command, "rm", "--force", container],
                    text=True,
                    capture_output=True,
                    env=builder_environment,
                    preexec_fn=base._private_engine_preexec(root),
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise OciBuildError("cleanup_failed") from error
            if removed.returncode != 0 and "no such" not in removed.stderr.lower():
                raise OciBuildError("cleanup_failed")
    return "isolated-script-passed"


def _rebuild_result(
    result: OciBuildResult,
    target_results: tuple[OciTargetResult, ...],
) -> OciBuildResult:
    evidence = {
        "api": "oci.build",
        "version": "1.0.0",
        "source": result.admitted_sha,
        "product": result.product_id,
        "release_version": result.release_version,
        "targets": [row.to_dict() for row in target_results],
        "flux": {
            "canary_id": result.canary_id,
            "previous_known_good": result.previous_known_good,
            "rollback_id": result.rollback_id,
        },
    }
    return replace(
        result,
        targets=target_results,
        evidence_id=hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )


def execute_plan(
    repository_root: Path,
    source_root: Path,
    plan: OciBuildPlan,
    environment: Mapping[str, str],
    secret_files: Mapping[str, Path] | None = None,
) -> OciBuildResult:
    """Execute the base builder with host smoke disabled, then assert safely."""

    masked = replace(
        plan,
        targets=tuple(replace(target, smoke_script=None) for target in plan.targets),
    )
    result = base.execute_plan(
        repository_root,
        source_root,
        masked,
        environment,
        secret_files,
    )
    root = base.state_root(environment)
    secured: list[OciTargetResult] = []
    by_id = {row.target_id: row for row in result.targets}
    for target in plan.targets:
        layout = root / "layouts" / target.target_id
        _assert_target_filesystem(layout, target)
        smoke = "inspection-passed"
        if target.smoke_script and plan.source_trust == "trusted-exact":
            script = root / "staged" / target.target_id / target.smoke_script
            smoke = _run_isolated_smoke(
                root,
                plan,
                target,
                script,
            )
        elif target.smoke_script:
            smoke = "inspection-passed-script-deferred"
        secured.append(replace(by_id[target.target_id], smoke_result=smoke))
    secured_result = _rebuild_result(result, tuple(secured))
    (root / "result.json").write_text(
        json.dumps(secured_result.output_values(), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return secured_result


cleanup = base.cleanup
residue = base.residue
