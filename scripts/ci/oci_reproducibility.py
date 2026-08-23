#!/usr/bin/env python3
"""Two-clean-build OCI reproducibility proof for the public reusable workflow.

The caller supplies only an admitted commit SHA and bounded repository-relative
Dockerfile/context paths.  Central fixes the Buildah runner, the platform set,
the engine, deterministic source metadata, isolated state layout, and the raw
OCI config comparison contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


REQUIRED_PLATFORMS = (
    ("linux/amd64", "amd64"),
    ("linux/arm64/v8", "arm64"),
)
_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReproducibilityError(RuntimeError):
    """Raised when the reproducibility contract cannot be proven."""


@dataclass(frozen=True)
class ProofInputs:
    source_root: Path
    admitted_sha: str
    dockerfile: Path
    context: Path
    source_date_epoch: int
    source_created: str
    state_root: Path


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReproducibilityError(message)


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            check=False,
            timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReproducibilityError(f"tool execution failed: {argv[0]}") from error
    if check and result.returncode:
        stderr = result.stderr.strip() if isinstance(result.stderr, str) else ""
        if stderr:
            print(stderr[-4000:], file=sys.stderr)
        raise ReproducibilityError(f"tool execution failed: {argv[0]}")
    return result


def _safe_relative(value: str, field: str) -> PurePosixPath:
    _require(isinstance(value, str) and bool(value), f"invalid {field}")
    _require("\x00" not in value and "\n" not in value and "\r" not in value, f"invalid {field}")
    _require(_PATH_RE.fullmatch(value) is not None, f"invalid {field}")
    path = PurePosixPath(value)
    _require(not path.is_absolute(), f"invalid {field}")
    _require(all(part not in ("", "..") for part in path.parts), f"invalid {field}")
    return path


def _resolve_source_path(source_root: Path, value: str, field: str, *, directory: bool) -> Path:
    relative = _safe_relative(value, field)
    root = source_root.resolve(strict=True)
    candidate = (root / Path(*relative.parts)).resolve(strict=True)
    _require(candidate == root or root in candidate.parents, f"invalid {field}")
    _require(not candidate.is_symlink(), f"invalid {field}")
    if directory:
        _require(candidate.is_dir(), f"invalid {field}")
    else:
        _require(candidate.is_file(), f"invalid {field}")
    return candidate


def _git(source_root: Path, *arguments: str) -> str:
    return _run(["git", "-C", str(source_root), *arguments]).stdout.strip()


def verify_exact_source(source_root: Path, admitted_sha: str) -> None:
    _require(_SHA_RE.fullmatch(admitted_sha) is not None, "invalid admitted SHA")
    _require(_git(source_root, "rev-parse", "HEAD") == admitted_sha, "caller source SHA drifted")
    _require(
        _git(source_root, "status", "--porcelain=v1", "--untracked-files=all") == "",
        "caller source is not clean",
    )


def load_inputs(environment: Mapping[str, str] | None = None) -> ProofInputs:
    env = os.environ if environment is None else environment
    source_root = Path(env.get("SOURCE_ROOT", "source"))
    admitted_sha = env.get("ADMITTED_SHA", "")
    verify_exact_source(source_root, admitted_sha)
    dockerfile = _resolve_source_path(
        source_root,
        env.get("DOCKERFILE_PATH", "Dockerfile"),
        "dockerfile_path",
        directory=False,
    )
    context = _resolve_source_path(
        source_root,
        env.get("BUILD_CONTEXT", "."),
        "build_context",
        directory=True,
    )
    timestamp_text = _git(source_root, "show", "-s", "--format=%ct", admitted_sha)
    _require(timestamp_text.isdigit(), "invalid source commit timestamp")
    source_date_epoch = int(timestamp_text)
    _require(source_date_epoch > 0, "invalid source commit timestamp")
    source_created = (
        datetime.fromtimestamp(source_date_epoch, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    runner_temp = Path(env.get("RUNNER_TEMP", ""))
    _require(runner_temp.is_absolute(), "RUNNER_TEMP must be absolute")
    run_id = env.get("GITHUB_RUN_ID", "local")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    _require(re.fullmatch(r"[A-Za-z0-9._-]+", run_id) is not None, "invalid run id")
    _require(re.fullmatch(r"[A-Za-z0-9._-]+", attempt) is not None, "invalid run attempt")
    state_root = runner_temp / f"ciw-oci-repro-{run_id}-{attempt}"
    return ProofInputs(
        source_root=source_root.resolve(strict=True),
        admitted_sha=admitted_sha,
        dockerfile=dockerfile,
        context=context,
        source_date_epoch=source_date_epoch,
        source_created=source_created,
        state_root=state_root,
    )


def _buildah_base(build_root: Path) -> list[str]:
    return [
        "buildah",
        "--root",
        str(build_root / "graphroot"),
        "--runroot",
        str(build_root / "runroot"),
        "--storage-driver",
        "vfs",
    ]


def _isolated_environment(build_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    for directory in ("tmp", "cache", "runtime", "auth"):
        (build_root / directory).mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "BUILDAH_ISOLATION": "chroot",
            "BUILDAH_TMPDIR": str(build_root / "tmp"),
            "TMPDIR": str(build_root / "tmp"),
            "XDG_CACHE_HOME": str(build_root / "cache"),
            "XDG_RUNTIME_DIR": str(build_root / "runtime"),
            "REGISTRY_AUTH_FILE": str(build_root / "auth" / "auth.json"),
            "STORAGE_DRIVER": "vfs",
        }
    )
    return env


def build_command(inputs: ProofInputs, build_root: Path, platform: str, image_tag: str) -> list[str]:
    _require(platform in {item[0] for item in REQUIRED_PLATFORMS}, "unsupported platform")
    return [
        *_buildah_base(build_root),
        "bud",
        "--format",
        "oci",
        "--isolation",
        "chroot",
        "--layers=false",
        "--no-cache",
        "--timestamp",
        str(inputs.source_date_epoch),
        "--platform",
        platform,
        "--label",
        f"org.opencontainers.image.revision={inputs.admitted_sha}",
        "--label",
        f"org.opencontainers.image.created={inputs.source_created}",
        "--build-arg",
        f"SOURCE_DATE_EPOCH={inputs.source_date_epoch}",
        "--file",
        str(inputs.dockerfile),
        "--tag",
        image_tag,
        str(inputs.context),
    ]


def raw_config_identity(layout: Path, expected_architecture: str) -> tuple[str, bytes]:
    reference = f"oci:{layout}:candidate"
    raw_manifest = _run(["skopeo", "inspect", "--raw", reference]).stdout.encode("utf-8")
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError as error:
        raise ReproducibilityError("invalid OCI manifest JSON") from error
    _require(isinstance(manifest, dict), "invalid OCI manifest")
    config = manifest.get("config")
    _require(isinstance(config, dict), "OCI manifest has no config descriptor")
    digest = config.get("digest")
    _require(isinstance(digest, str) and _DIGEST_RE.fullmatch(digest) is not None, "invalid config digest")
    blob = layout / "blobs" / "sha256" / digest.split(":", 1)[1]
    _require(blob.is_file() and not blob.is_symlink(), "raw OCI config blob is missing")
    raw_config = blob.read_bytes()
    actual = f"sha256:{hashlib.sha256(raw_config).hexdigest()}"
    _require(actual == digest, "raw OCI config digest mismatch")
    try:
        payload = json.loads(raw_config)
    except json.JSONDecodeError as error:
        raise ReproducibilityError("invalid raw OCI config JSON") from error
    _require(isinstance(payload, dict), "invalid raw OCI config")
    _require(payload.get("os") == "linux", "OCI config has wrong operating system")
    _require(payload.get("architecture") == expected_architecture, "OCI config has wrong architecture")
    return digest, raw_config


def compare_builds(
    first: Mapping[str, tuple[str, bytes]],
    second: Mapping[str, tuple[str, bytes]],
) -> dict[str, str]:
    required = {item[0] for item in REQUIRED_PLATFORMS}
    _require(set(first) == required, "build A platform set mismatch")
    _require(set(second) == required, "build B platform set mismatch")
    identities: dict[str, str] = {}
    for platform in sorted(required):
        first_digest, first_raw = first[platform]
        second_digest, second_raw = second[platform]
        _require(first_raw == second_raw, f"raw OCI config drift for {platform}")
        _require(first_digest == second_digest, f"OCI config digest drift for {platform}")
        identities[platform] = first_digest
    return identities


def _cleanup_build_root(build_root: Path) -> None:
    if not build_root.exists():
        return
    base = _buildah_base(build_root)
    env = _isolated_environment(build_root)
    for command in (
        [*base, "unmount", "--all"],
        [*base, "rm", "--all"],
        [*base, "rmi", "--all", "--force"],
    ):
        _run(command, environment=env, check=False)
    shutil.rmtree(build_root, ignore_errors=True)
    _require(not build_root.exists(), f"run-owned residue remains: {build_root}")


def cleanup_state(state_root: Path) -> None:
    for build_name in ("build-a", "build-b"):
        _cleanup_build_root(state_root / build_name)
    shutil.rmtree(state_root / "comparison", ignore_errors=True)
    try:
        state_root.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        shutil.rmtree(state_root, ignore_errors=True)
    _require(not state_root.exists(), f"run-owned residue remains: {state_root}")


def _one_clean_build(inputs: ProofInputs, build_name: str) -> dict[str, tuple[str, bytes]]:
    verify_exact_source(inputs.source_root, inputs.admitted_sha)
    build_root = inputs.state_root / build_name
    _require(not build_root.exists(), f"{build_name} state was not fresh")
    build_root.mkdir(parents=True, exist_ok=False)
    env = _isolated_environment(build_root)
    results: dict[str, tuple[str, bytes]] = {}
    try:
        for platform, architecture in REQUIRED_PLATFORMS:
            slug = platform.replace("/", "-")
            image_tag = f"localhost/ciw-repro-{build_name}-{slug}:candidate"
            layout = build_root / "layouts" / slug
            layout.parent.mkdir(parents=True, exist_ok=True)
            _run(
                build_command(inputs, build_root, platform, image_tag),
                cwd=inputs.context,
                environment=env,
            )
            _run(
                [
                    *_buildah_base(build_root),
                    "push",
                    "--format",
                    "oci",
                    image_tag,
                    f"oci:{layout}:candidate",
                ],
                cwd=inputs.context,
                environment=env,
            )
            digest, raw_config = raw_config_identity(layout, architecture)
            results[platform] = (digest, raw_config)
            _run(
                [*_buildah_base(build_root), "rmi", "--force", image_tag],
                cwd=inputs.context,
                environment=env,
                check=False,
            )
        _require(set(results) == {item[0] for item in REQUIRED_PLATFORMS}, "platform set mismatch")
        return results
    finally:
        _cleanup_build_root(build_root)


def _write_outputs(inputs: ProofInputs, identities: Mapping[str, str]) -> None:
    platform_json = json.dumps(dict(sorted(identities.items())), separators=(",", ":"), sort_keys=True)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write("result=success\n")
            handle.write(f"source_sha={inputs.admitted_sha}\n")
            handle.write(f"platform_digests_json={platform_json}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("## OCI reproducibility proof\n\n")
            handle.write(f"Exact source: `{inputs.admitted_sha}`\n\n")
            handle.write(f"Source date epoch: `{inputs.source_date_epoch}`\n\n")
            handle.write("| Platform | Raw config SHA-256 |\n|---|---|\n")
            for platform, digest in sorted(identities.items()):
                handle.write(f"| `{platform}` | `{digest}` |\n")


def run_proof(inputs: ProofInputs) -> dict[str, str]:
    cleanup_state(inputs.state_root)
    inputs.state_root.mkdir(parents=True, exist_ok=False)
    try:
        first = _one_clean_build(inputs, "build-a")
        _require(not (inputs.state_root / "build-a").exists(), "build A state survived into build B")
        verify_exact_source(inputs.source_root, inputs.admitted_sha)
        second = _one_clean_build(inputs, "build-b")
        identities = compare_builds(first, second)
        verify_exact_source(inputs.source_root, inputs.admitted_sha)
        _write_outputs(inputs, identities)
        return identities
    finally:
        cleanup_state(inputs.state_root)


def _state_root_from_environment(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    runner_temp = Path(env.get("RUNNER_TEMP", ""))
    _require(runner_temp.is_absolute(), "RUNNER_TEMP must be absolute")
    run_id = env.get("GITHUB_RUN_ID", "local")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    _require(re.fullmatch(r"[A-Za-z0-9._-]+", run_id) is not None, "invalid run id")
    _require(re.fullmatch(r"[A-Za-z0-9._-]+", attempt) is not None, "invalid run attempt")
    return runner_temp / f"ciw-oci-repro-{run_id}-{attempt}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "cleanup"))
    args = parser.parse_args(argv)
    try:
        if args.command == "cleanup":
            cleanup_state(_state_root_from_environment())
            return 0
        inputs = load_inputs()
        run_proof(inputs)
        return 0
    except ReproducibilityError as error:
        print(f"OCI reproducibility proof failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
