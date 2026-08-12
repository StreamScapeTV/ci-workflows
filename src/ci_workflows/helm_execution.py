"""Hermetic Helm validation, deterministic packaging, and OCI read-back."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

import yaml

from .helm_contract import (
    SEMVER,
    HelmPlan,
    HelmValidationError,
    require,
    validate_chart_layout,
)
from .helm_runtime import HELM_VERSION
from .helm_types import HelmPublicationResult, HelmValidationResult

_SECRET = re.compile(
    r"(?i)(authorization|password|secret|token)\s*[:=]\s*[^\s${}]+"
)
_TOKEN = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----)"
)
_JUNK = {".git", ".ds_store", "__pycache__", ".env", ".npmrc"}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kubeconfig"}
_SERVICE_ACCOUNT_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


def sanitize(value: str, roots: Sequence[Path] = ()) -> str:
    for root in roots:
        value = value.replace(str(root), "<state>")
    value = re.sub(r"(?i)https?://[^\s/@]+:[^\s/@]+@", "https://<redacted>@", value)
    value = _SECRET.sub(lambda item: f"{item.group(1)}=<redacted>", value)
    return "\n".join(_TOKEN.sub("<redacted>", value).splitlines()[-120:])


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
    code: str,
    stdin: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    require(
        bool(argv) and all(isinstance(value, str) and value for value in argv),
        "invalid_input",
    )
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HelmValidationError(code) from error
    if check and result.returncode:
        raise HelmValidationError(code)
    return result


def _remove_no_follow(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    require(path.is_dir(), "cleanup_failed")
    for item in os.scandir(path):
        child = path / item.name
        if item.is_symlink() or item.is_file(follow_symlinks=False):
            child.unlink()
        else:
            _remove_no_follow(child)
    path.rmdir()


def cleanup_helm_state(state_root: Path) -> None:
    _remove_no_follow(state_root / "helm-validation")


def verify_no_helm_residue(state_root: Path) -> None:
    residue = state_root / "helm-validation"
    require(not residue.exists() and not residue.is_symlink(), "cleanup_failed")


def _runtime_environment(
    inherited: Mapping[str, str],
    state_root: Path,
) -> dict[str, str]:
    require(
        state_root.is_absolute() and state_root.is_dir() and not state_root.is_symlink(),
        "invalid_input",
    )
    path = inherited.get("PATH", "")
    home = inherited.get("HOME", "")
    require(bool(path) and bool(home) and Path(home).is_absolute(), "invalid_input")
    root = state_root / "helm-validation"
    directories = {
        "HELM_CACHE_HOME": root / "cache",
        "HELM_CONFIG_HOME": root / "config",
        "HELM_DATA_HOME": root / "data",
        "HOME": root / "home",
        "TMPDIR": root / "tmp",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        "PATH": path,
        "HOME": str(directories["HOME"]),
        "HELM_CACHE_HOME": str(directories["HELM_CACHE_HOME"]),
        "HELM_CONFIG_HOME": str(directories["HELM_CONFIG_HOME"]),
        "HELM_DATA_HOME": str(directories["HELM_DATA_HOME"]),
        "TMPDIR": str(directories["TMPDIR"]),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }


def verify_helm_toolchain(
    source_root: Path,
    environment: Mapping[str, str],
) -> None:
    output = _run(
        ["helm", "version", "--template", "{{.Version}}"],
        cwd=source_root,
        environment=environment,
        timeout=30,
        code="toolchain_mismatch",
    ).stdout.strip()
    require(output == HELM_VERSION, "toolchain_mismatch")


def verify_exact_source(
    source_root: Path,
    admitted_sha: str,
    environment: Mapping[str, str],
) -> None:
    require(
        source_root.is_dir()
        and not source_root.is_symlink()
        and (source_root / ".git").exists(),
        "source_mismatch",
    )
    head = _run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_root,
        environment=environment,
        timeout=30,
        code="source_mismatch",
    ).stdout.strip()
    require(head == admitted_sha, "source_mismatch")
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=source_root,
        environment=environment,
        timeout=30,
        code="dirty_tree",
    ).stdout.strip()
    require(not status, "dirty_tree")


def _archive_member_name(name: str, chart_name: str) -> str:
    candidate = name[2:] if name.startswith("./") else name
    path = PurePosixPath(candidate)
    parts = path.parts
    require(
        bool(parts)
        and not path.is_absolute()
        and parts[0] == chart_name
        and all(part not in {"", ".", ".."} for part in parts),
        "archive_invalid",
    )
    require(
        not any(part.casefold() in _JUNK for part in parts),
        "archive_invalid",
    )
    require(
        not Path(parts[-1]).suffix.casefold() in _SENSITIVE_SUFFIXES,
        "archive_invalid",
    )
    return "/".join(parts)


def normalize_chart_archive(
    source: Path,
    destination: Path,
    chart_name: str,
) -> str:
    """Rewrite one ordinary chart archive to stable order, modes, and mtimes."""

    require(source.is_file() and not source.is_symlink(), "archive_invalid")
    members: list[tuple[str, bool, bytes]] = []
    expanded_size = 0
    try:
        with tarfile.open(source, "r:gz") as archive:
            for member in archive.getmembers():
                require(len(members) < 1_024, "archive_invalid")
                name = _archive_member_name(member.name, chart_name)
                require(member.isfile() or member.isdir(), "archive_invalid")
                if member.isdir():
                    members.append((name.rstrip("/") + "/", True, b""))
                    continue
                extracted = archive.extractfile(member)
                require(extracted is not None, "archive_invalid")
                content = extracted.read()
                require(len(content) <= 8 * 1024 * 1024, "archive_invalid")
                expanded_size += len(content)
                require(expanded_size <= 64 * 1024 * 1024, "archive_invalid")
                decoded = content.decode("utf-8", errors="replace")
                if _TOKEN.search(decoded) or _SECRET.search(decoded):
                    raise HelmValidationError("archive_secret_detected")
                members.append((name, False, content))
    except (OSError, tarfile.TarError) as error:
        raise HelmValidationError("archive_invalid") from error
    require(
        members
        and any(name == f"{chart_name}/Chart.yaml" for name, _, _ in members),
        "archive_invalid",
    )
    require(
        len({name for name, _, _ in members}) == len(members),
        "archive_invalid",
    )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with destination.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for name, directory, content in sorted(members):
                        info = tarfile.TarInfo(name)
                        info.mtime = 0
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.mode = 0o755 if directory else 0o644
                        if directory:
                            info.type = tarfile.DIRTYPE
                            archive.addfile(info)
                        else:
                            info.size = len(content)
                            archive.addfile(info, io.BytesIO(content))
    except OSError as error:
        raise HelmValidationError("archive_invalid") from error
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def _image_reference_assertions(
    rendered: str,
    expected: Sequence[str],
) -> None:
    rendered_images: list[str] = []
    for line in rendered.splitlines():
        if re.match(r"^\s*(?:-\s*)?image:\s*", line):
            value = line.split(":", 1)[1].strip().strip("\"'")
            require(
                re.search(r"@sha256:[0-9a-f]{64}$", value) is not None,
                "image_reference_mismatch",
            )
            rendered_images.append(value)
    for reference in expected:
        require(reference in rendered, "image_reference_mismatch")
    if expected:
        require(bool(rendered_images), "image_reference_mismatch")


def _chart_version(chart_root: Path) -> str:
    try:
        metadata = yaml.safe_load((chart_root / "Chart.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise HelmValidationError("chart_metadata_invalid") from error
    require(isinstance(metadata, Mapping), "chart_metadata_invalid")
    version = metadata.get("version")
    require(
        isinstance(version, str) and SEMVER.fullmatch(version) is not None,
        "chart_metadata_invalid",
    )
    return version


def _copy_chart_for_build(
    chart_root: Path,
    values_path: Path,
    state_root: Path,
    chart_name: str,
) -> tuple[Path, Path]:
    work_root = state_root / "helm-validation" / "work"
    work_chart = work_root / chart_name
    require(not work_chart.exists() and not work_chart.is_symlink(), "workspace_failed")
    try:
        shutil.copytree(chart_root, work_chart, symlinks=False)
    except OSError as error:
        raise HelmValidationError("workspace_failed") from error
    relative_values = values_path.relative_to(chart_root)
    work_values = work_chart / relative_values
    require(work_values.is_file() and not work_values.is_symlink(), "workspace_failed")
    return work_chart, work_values


def validate_and_package(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    admitted_sha: str,
    inherited: Mapping[str, str],
) -> HelmValidationResult:
    environment = _runtime_environment(inherited, state_root)
    verify_exact_source(source_root, admitted_sha, environment)
    chart_root, values_path = validate_chart_layout(source_root, plan)
    verify_helm_toolchain(source_root, environment)
    source_version = _chart_version(chart_root)
    work_chart, work_values = _copy_chart_for_build(
        chart_root,
        values_path,
        state_root,
        plan.product.chart_name,
    )
    if plan.product.locked_dependencies:
        _run(
            ["helm", "dependency", "build", str(work_chart)],
            cwd=source_root,
            environment=environment,
            timeout=120,
            code="dependency_build_failed",
        )
    _run(
        ["helm", "lint", "--strict", str(work_chart), "--values", str(work_values)],
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="lint_failed",
    )
    rendered = _run(
        [
            "helm",
            "template",
            plan.product.chart_name,
            str(work_chart),
            "--include-crds",
            "--values",
            str(work_values),
        ],
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="template_failed",
    ).stdout
    _image_reference_assertions(rendered, plan.product.required_image_references)

    package_version = plan.release_version or source_version
    output_root = state_root / "helm-validation" / "package"
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    package_args = [
        "helm",
        "package",
        str(work_chart),
        "--destination",
        str(output_root),
    ]
    if plan.release_version is not None:
        package_args.extend(
            ["--version", plan.release_version, "--app-version", plan.release_version]
        )
    _run(
        package_args,
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="package_failed",
    )
    candidate = output_root / f"{plan.product.chart_name}-{package_version}.tgz"
    require(
        candidate.is_file() and not candidate.is_symlink(),
        "package_failed",
    )
    normalized = output_root / "normalized.tgz"
    package_sha256 = normalize_chart_archive(
        candidate,
        normalized,
        plan.product.chart_name,
    )
    candidate.unlink()
    verify_exact_source(source_root, admitted_sha, environment)
    summary = json.dumps(
        {
            "chart_name": plan.product.chart_name,
            "package_sha256": package_sha256,
            "release_version": package_version,
            "status": "success",
            "values_profile": plan.values_profile,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return HelmValidationResult(
        chart_digest=f"sha256:{package_sha256}",
        package_sha256=package_sha256,
        summary=summary,
        archive_path=normalized,
    )


def _registry_host(repository: str) -> str:
    require(repository.startswith("oci://"), "registry_rejected")
    remainder = repository.removeprefix("oci://")
    host = remainder.split("/", 1)[0]
    require(host == "git.faruqi.dev", "registry_rejected")
    return host


def _verify_no_kubernetes_authority(inherited: Mapping[str, str]) -> None:
    require(
        not inherited.get("KUBECONFIG", "").strip(),
        "kubernetes_authority_rejected",
    )
    require(
        not _SERVICE_ACCOUNT_TOKEN.exists(),
        "kubernetes_authority_rejected",
    )


def publish_and_read_back(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    validation: HelmValidationResult,
    inherited: Mapping[str, str],
) -> HelmPublicationResult:
    """Push once or prove byte-identical prior publication, then pull it back."""

    require(
        plan.release_version is not None
        and SEMVER.fullmatch(plan.release_version) is not None,
        "release_version_mismatch",
    )
    _verify_no_kubernetes_authority(inherited)
    username = inherited.get("INPUT_REGISTRY_USERNAME", "")
    token = inherited.get("INPUT_REGISTRY_TOKEN", "")
    require(bool(username) and bool(token), "registry_auth_missing")
    environment = _runtime_environment(inherited, state_root)
    host = _registry_host(plan.product.registry_repository)
    _run(
        [
            "helm",
            "registry",
            "login",
            host,
            "--username",
            username,
            "--password-stdin",
        ],
        cwd=source_root,
        environment=environment,
        timeout=60,
        code="registry_auth_failed",
        stdin=f"{token}\n",
    )
    remote_root = state_root / "helm-validation" / "read-back"
    remote_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    chart_reference = (
        f"{plan.product.registry_repository}/{plan.product.chart_name}"
    )
    pull_args = [
        "helm",
        "pull",
        chart_reference,
        "--version",
        plan.release_version,
        "--destination",
        str(remote_root),
    ]
    prior = _run(
        pull_args,
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="remote_read_back_failed",
        check=False,
    )
    published = prior.returncode != 0
    if published:
        lookup = sanitize(
            prior.stdout + prior.stderr,
            (source_root, state_root),
        ).casefold()
        require(
            "manifest unknown" in lookup
            or "not found" in lookup
            or "not exist" in lookup,
            "registry_lookup_failed",
        )
    remote_archive = (
        remote_root / f"{plan.product.chart_name}-{plan.release_version}.tgz"
    )
    if published:
        _run(
            [
                "helm",
                "push",
                str(validation.archive_path),
                plan.product.registry_repository,
            ],
            cwd=source_root,
            environment=environment,
            timeout=120,
            code="publication_failed",
        )
        _run(
            pull_args,
            cwd=source_root,
            environment=environment,
            timeout=120,
            code="remote_read_back_failed",
        )
    require(
        remote_archive.is_file() and not remote_archive.is_symlink(),
        "remote_read_back_failed",
    )
    readback = remote_root / "normalized.tgz"
    remote_sha256 = normalize_chart_archive(
        remote_archive,
        readback,
        plan.product.chart_name,
    )
    require(
        remote_sha256 == validation.package_sha256,
        "immutable_conflict",
    )
    reference = f"{chart_reference}:{plan.release_version}"
    immutable = json.dumps(
        {
            "chart": reference,
            "chart_digest": validation.chart_digest,
            "package_sha256": validation.package_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return HelmPublicationResult(
        chart_digest=validation.chart_digest,
        immutable_references_json=immutable,
        package_sha256=validation.package_sha256,
        published=published,
    )
