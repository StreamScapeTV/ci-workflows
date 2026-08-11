"""Security adapter for central filesystem assertions and isolated OCI smoke."""
from __future__ import annotations

import hashlib
import io
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
        blob = base._blob(layout, digest).read_bytes()
        try:
            archive = tarfile.open(fileobj=io.BytesIO(blob), mode="r:*")
        except tarfile.TarError as error:
            raise OciBuildError("oci_layout_malformed") from error
        with archive:
            for member in archive.getmembers():
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise OciBuildError("oci_layout_malformed")
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    if link.is_absolute() or ".." in link.parts:
                        raise OciBuildError("oci_layout_malformed")
                name = pure.as_posix().lstrip("./")
                if not name:
                    continue
                basename = PurePosixPath(name).name
                parent = PurePosixPath(name).parent.as_posix()
                prefix = "/" + ("" if parent == "." else parent.rstrip("/") + "/")
                if basename == ".wh..wh..opq":
                    paths = {path for path in paths if not path.startswith(prefix)}
                    continue
                if basename.startswith(".wh."):
                    removed = prefix + basename.removeprefix(".wh.")
                    paths = {
                        path for path in paths
                        if path != removed and not path.startswith(removed.rstrip("/") + "/")
                    }
                    continue
                paths.add("/" + name.rstrip("/"))
    return paths


def _manifest_layer_sets(layout: Path) -> tuple[tuple[str, ...], ...]:
    try:
        index = base._read_json(layout / "index.json")
    except OciBuildError:
        raise
    except OSError as error:
        raise OciBuildError("oci_layout_malformed") from error
    if not isinstance(index, dict) or not isinstance(index.get("manifests"), list):
        raise OciBuildError("oci_layout_malformed")
    sets: list[tuple[str, ...]] = []
    for descriptor in index["manifests"]:
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("digest"), str):
            raise OciBuildError("oci_layout_malformed")
        try:
            manifest = json.loads(base._blob(layout, descriptor["digest"]).read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise OciBuildError("oci_layout_malformed") from error
        layers = manifest.get("layers") if isinstance(manifest, dict) else None
        if not isinstance(layers, list):
            raise OciBuildError("oci_layout_malformed")
        digests: list[str] = []
        for layer in layers:
            if not isinstance(layer, dict) or not isinstance(layer.get("digest"), str):
                raise OciBuildError("oci_layout_malformed")
            digests.append(layer["digest"])
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
    created = False
    try:
        base.execute_command([*command, "from", "--name", container, "--platform", "linux/amd64", manifest])
        created = True
        base.execute_command([*command, "copy", container, str(script), "/tmp/ciw-smoke.sh"])
        base.execute_command(
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
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise OciBuildError("smoke_failed") from error
    finally:
        if created:
            removed = subprocess.run(
                [*command, "rm", "--force", container],
                text=True,
                capture_output=True,
            )
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
