"""Isolated npm execution, static-output verification, and cleanup."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from .foundation_types import stable_identifier
from .node_contract import (
    bounded_path,
    file_sha256,
    load_lockfile,
    load_package_manifest,
    require,
    resolve_exact_node_version,
    verify_manifest_engines,
)
from .node_types import NodeValidationError, NodeValidationPlan, NodeValidationResult


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    code: str,
) -> subprocess.CompletedProcess[str]:
    """Run one contract-owned argv without a shell or output projection."""

    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NodeValidationError(code) from error
    if completed.returncode != 0:
        raise NodeValidationError(code)
    return completed


def git_output(root: Path, arguments: Sequence[str], code: str) -> str:
    return run_command(
        ["git", *arguments],
        cwd=root,
        environment={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "LANG": "C"},
        timeout_seconds=60,
        code=code,
    ).stdout.strip()


def verify_exact_source(source_root: Path, admitted_sha: str) -> None:
    require(source_root.is_dir() and (source_root / ".git").exists(), "dirty_tree")
    require(
        git_output(source_root, ["rev-parse", "HEAD"], "dirty_tree") == admitted_sha,
        "dirty_tree",
    )
    require(
        not git_output(
            source_root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
            "dirty_tree",
        ),
        "dirty_tree",
    )


def copy_source(source_root: Path, destination: Path) -> None:
    """Copy exact source into registered state while rejecting symlinks."""

    require(not destination.exists(), "cleanup_failed")
    for current, directories, files in os.walk(source_root, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source_root)
        for name in [*directories, *files]:
            require(not (current_path / name).is_symlink(), "invalid_input")
        target = destination / relative
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(current_path / name, target / name, follow_symlinks=False)


def _version(executable: str, argument: str, *, cwd: Path, environment: Mapping[str, str]) -> str:
    completed = run_command(
        [executable, argument],
        cwd=cwd,
        environment=environment,
        timeout_seconds=30,
        code="runtime_mismatch",
    )
    value = completed.stdout.strip()
    if executable == "node" and value.startswith("v"):
        value = value[1:]
    require(value and value.count(".") == 2 and all(part.isdigit() for part in value.split(".")), "runtime_mismatch")
    return value


def _execution_environment(
    plan: NodeValidationPlan,
    state_root: Path,
    inherited: Mapping[str, str],
) -> tuple[dict[str, str], tuple[Path, ...]]:
    node_root = state_root / "node-validation"
    paths = (
        node_root / "home",
        node_root / "tmp",
        node_root / "npm-cache",
        node_root / "npm-config",
        node_root / "xdg-cache",
        node_root / "xdg-config",
        node_root / "xdg-data",
    )
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    userconfig = node_root / "npmrc"
    userconfig.write_text(
        "audit=false\nfund=false\nupdate-notifier=false\ncache="
        + str(node_root / "npm-cache")
        + "\n",
        encoding="utf-8",
    )
    userconfig.chmod(stat.S_IRUSR | stat.S_IWUSR)
    environment = {
        "PATH": inherited.get("PATH", ""),
        "LANG": inherited.get("LANG", "C.UTF-8"),
        "LC_ALL": inherited.get("LC_ALL", "C.UTF-8"),
        "TZ": "UTC",
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "HOME": str(node_root / "home"),
        "TMPDIR": str(node_root / "tmp"),
        "XDG_CACHE_HOME": str(node_root / "xdg-cache"),
        "XDG_CONFIG_HOME": str(node_root / "xdg-config"),
        "XDG_DATA_HOME": str(node_root / "xdg-data"),
        "NPM_CONFIG_USERCONFIG": str(userconfig),
        "NPM_CONFIG_CACHE": str(node_root / "npm-cache"),
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    environment.update(plan.public_environment)
    return environment, paths


def _stage_code(stage: str) -> str:
    if stage in {"policy", "quality", "typecheck", "audit"}:
        return "quality_failed"
    if stage == "tests":
        return "tests_failed"
    if stage == "build":
        return "build_failed"
    if stage == "output-verification":
        return "output_verifier_failed"
    return "command_profile_rejected"


def _checked_in_regular(source_root: Path, relative: str, code: str) -> Path:
    path = bounded_path(source_root, relative)
    require(path.is_file() and not path.is_symlink(), code)
    tracked = git_output(source_root, ["ls-files", "--error-unmatch", relative], code)
    require(tracked == relative, code)
    return path


def _verify_command_hooks(source_root: Path, plan: NodeValidationPlan) -> None:
    if plan.script_path:
        _checked_in_regular(source_root, plan.script_path, "command_profile_rejected")
    if plan.output_verifier_path:
        _checked_in_regular(
            source_root,
            plan.output_verifier_path,
            "output_verifier_failed",
        )
    serialized = "\n".join("\0".join(command.argv) for command in plan.commands)
    for forbidden in (
        "npm\0install",
        "yarn",
        "pnpm",
        "bun",
        "corepack",
        "wrangler",
        "cloudflare",
        "docker",
        "buildah",
        "kubectl",
        "helm",
    ):
        require(forbidden not in serialized.casefold(), "command_profile_rejected")


def _allowed_generated_path(relative: str, plan: NodeValidationPlan, cleanup_names: Sequence[str]) -> bool:
    normalized = relative.strip("/")
    working_prefix = "" if plan.working_directory == "." else plan.working_directory + "/"
    candidates = {name.strip("/") for name in cleanup_names}
    if plan.static_output_directory:
        candidates.add(plan.static_output_directory.strip("/"))
    for candidate in candidates:
        for prefix in (candidate, working_prefix + candidate):
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return True
    parts = Path(normalized).parts
    return "__pycache__" in parts or normalized.endswith((".pyc", ".pyo"))


def _verify_copy_mutations(
    copy_root: Path,
    plan: NodeValidationPlan,
    cleanup_names: Sequence[str],
) -> None:
    require(not git_output(copy_root, ["diff", "--name-only"], "dirty_tree"), "dirty_tree")
    require(
        not git_output(copy_root, ["diff", "--cached", "--name-only"], "dirty_tree"),
        "dirty_tree",
    )
    status = git_output(
        copy_root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        "dirty_tree",
    )
    for row in status.splitlines():
        if not row:
            continue
        path = row[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        require(_allowed_generated_path(path, plan, cleanup_names), "dirty_tree")


def _remove_no_follow(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    require(path.is_dir(), "cleanup_failed")
    with os.scandir(path) as entries:
        for entry in entries:
            child = path / entry.name
            if entry.is_symlink():
                child.unlink()
            elif entry.is_dir(follow_symlinks=False):
                _remove_no_follow(child)
            else:
                child.unlink()
    path.rmdir()


def cleanup_generated(
    copy_root: Path,
    state_root: Path,
    plan: NodeValidationPlan,
    cleanup_names: Sequence[str],
) -> None:
    working = bounded_path(copy_root, plan.working_directory)
    targets: list[Path] = []
    for name in cleanup_names:
        targets.append(bounded_path(copy_root, name))
        targets.append(bounded_path(working, name))
    if plan.static_output_directory:
        targets.append(bounded_path(working, plan.static_output_directory))
    unique = sorted({path for path in targets}, key=lambda value: len(value.parts), reverse=True)
    for path in unique:
        require(copy_root == path or copy_root in path.parents, "cleanup_failed")
        _remove_no_follow(path)
    node_state = state_root / "node-validation"
    _remove_no_follow(node_state)
    require(not node_state.exists(), "cleanup_failed")


def verify_static_output(
    copy_root: Path,
    plan: NodeValidationPlan,
    limits: Mapping[str, Any],
) -> str | None:
    if plan.output_mode == "none":
        return None
    require(plan.static_output_directory is not None, "output_missing")
    working = bounded_path(copy_root, plan.working_directory)
    output = bounded_path(working, plan.static_output_directory)
    require(output.is_dir() and not output.is_symlink(), "output_missing")
    max_files = int(limits["max_files"])
    max_bytes = int(limits["max_bytes"])
    forbidden_names = {str(value).casefold() for value in limits["forbidden_names"]}
    forbidden_parts = {str(value).casefold() for value in limits["forbidden_path_parts"]}
    required_extensions = {str(value).casefold() for value in limits["required_extensions"]}
    files: list[tuple[str, str]] = []
    total_bytes = 0
    extensions: set[str] = set()
    for current, directories, names in os.walk(output, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            child = current_path / directory
            require(not child.is_symlink(), "output_malformed")
            relative_parts = {
                part.casefold() for part in child.relative_to(output).parts
            }
            require(not (relative_parts & forbidden_parts), "output_malformed")
        for name in names:
            path = current_path / name
            require(path.is_file() and not path.is_symlink(), "output_malformed")
            relative = path.relative_to(output).as_posix()
            parts = {part.casefold() for part in Path(relative).parts}
            require(name.casefold() not in forbidden_names, "output_malformed")
            require(not (parts & forbidden_parts), "output_malformed")
            size = path.stat().st_size
            total_bytes += size
            require(len(files) + 1 <= max_files and total_bytes <= max_bytes, "output_malformed")
            extensions.add(path.suffix.casefold())
            files.append((relative, file_sha256(path, "output_malformed")))
    require(files, "output_malformed")
    require(required_extensions <= extensions, "output_malformed")
    material = "".join(f"{relative}\0{digest}\n" for relative, digest in sorted(files))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def execute_node_plan(
    source_root: Path,
    state_root: Path,
    plan: NodeValidationPlan,
    contract: Mapping[str, Any],
    inherited_environment: Mapping[str, str],
) -> NodeValidationResult:
    """Execute one checked-in plan in copied state and clean every terminal path."""

    verify_exact_source(source_root, plan.admitted_sha)
    resolve_exact_node_version(source_root, plan)
    manifest = load_package_manifest(source_root, plan)
    load_lockfile(source_root, plan)
    manifest_hash = (
        file_sha256(bounded_path(source_root, plan.manifest_path), "lockfile_drift")
        if plan.manifest_path
        else None
    )
    lock_hash = (
        file_sha256(bounded_path(source_root, plan.lockfile_path), "lockfile_drift")
        if plan.lockfile_path
        else None
    )
    copy_root = state_root / "node-validation" / "source"
    copy_source(source_root, copy_root)
    working = bounded_path(copy_root, plan.working_directory)
    _verify_command_hooks(copy_root, plan)
    environment, _paths = _execution_environment(plan, state_root, inherited_environment)
    node_version = _version("node", "--version", cwd=working, environment=environment)
    npm_version = _version("npm", "--version", cwd=working, environment=environment)
    require(node_version == plan.node_version, "runtime_mismatch")
    verify_manifest_engines(manifest, node_version, npm_version)
    install_result = "skipped"
    build_result = "skipped"
    output_digest: str | None = None
    original_error: BaseException | None = None
    try:
        if plan.install_profile == "npm-ci":
            run_command(
                ["npm", "ci", "--no-audit", "--no-fund"],
                cwd=working,
                environment=environment,
                timeout_seconds=max(60, plan.timeout_minutes * 30),
                code="install_failed",
            )
            install_result = "success"
        else:
            require(plan.install_profile == "none", "unsupported_package_manager")
        for command in plan.commands:
            run_command(
                command.argv,
                cwd=working,
                environment=environment,
                timeout_seconds=plan.timeout_minutes * 60,
                code=_stage_code(command.stage),
            )
            if command.stage == "build":
                build_result = "success"
        output_digest = verify_static_output(copy_root, plan, contract["output_limits"])
        if plan.output_mode != "none":
            build_result = "success"
        if plan.manifest_path:
            require(
                file_sha256(bounded_path(copy_root, plan.manifest_path), "lockfile_drift")
                == manifest_hash,
                "lockfile_drift",
            )
        if plan.lockfile_path:
            require(
                file_sha256(bounded_path(copy_root, plan.lockfile_path), "lockfile_drift")
                == lock_hash,
                "lockfile_drift",
            )
        _verify_copy_mutations(copy_root, plan, contract["generated_cleanup_names"])
        verify_exact_source(source_root, plan.admitted_sha)
    except BaseException as error:
        original_error = error
    try:
        cleanup_generated(
            copy_root,
            state_root,
            plan,
            contract["generated_cleanup_names"],
        )
    except BaseException as cleanup_error:
        raise NodeValidationError("cleanup_failed") from cleanup_error
    verify_exact_source(source_root, plan.admitted_sha)
    if original_error is not None:
        raise original_error
    evidence_id = stable_identifier(
        "node",
        {
            "source_sha": plan.admitted_sha,
            "profile": plan.validation_profile,
            "command_profile": plan.command_profile,
            "node_version": node_version,
            "npm_version": npm_version,
            "output_digest": output_digest or "",
        },
        length=28,
    )
    return NodeValidationResult(
        node_version=node_version,
        npm_version=npm_version,
        validation_profile=plan.validation_profile,
        command_profile=plan.command_profile,
        install_result=install_result,
        test_count=sum(command.stage == "tests" for command in plan.commands),
        build_result=build_result,
        output_verified=plan.output_mode != "none",
        output_digest=output_digest,
        clean_tree=True,
        cleanup_result="verified",
        evidence_id=evidence_id,
    )
