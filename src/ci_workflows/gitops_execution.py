"""Hermetic execution engine for source-only GitOps validation."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from .gitops_contract import bounded_path, file_sha256, safe_relative
from .gitops_types import (
    GitOpsPlan,
    GitOpsResult,
    GitOpsTarget,
    GitOpsTargetKind,
    GitOpsToolPin,
    GitOpsValidationError,
    ObjectIdentity,
    compact_json,
)

_MARKER = ".gitops-validation-state.json"
_TOKEN_LIKE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
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
    _require(_TOKEN_LIKE.search(text) is None, code, "sensitive output rejected")
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
    _require(bool(argv) and all(isinstance(value, str) and value for value in argv), code)
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


def initialize_gitops_state(state_root: Path) -> None:
    """Create one marker-bound runtime root without following a symlink."""

    _require(state_root.is_absolute(), "cleanup_failed")
    _require(not state_root.is_symlink(), "cleanup_failed")
    state_root.mkdir(parents=True, exist_ok=True)
    _require(state_root.is_dir() and not state_root.is_symlink(), "cleanup_failed")
    marker = _state_marker(state_root)
    payload = {"schema_version": 1, "root": str(state_root.resolve())}
    marker.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _validate_marker(state_root: Path) -> None:
    marker = _state_marker(state_root)
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GitOpsValidationError("cleanup_failed") from error
    _require(
        payload == {"schema_version": 1, "root": str(state_root.resolve())},
        "cleanup_failed",
    )


def _remove_no_follow(path: Path) -> None:
    """Delete a registered tree with lstat and without traversing symlinks."""

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
    _require(state_root.is_absolute() and not state_root.is_symlink(), "cleanup_failed")
    _validate_marker(state_root)
    try:
        _remove_no_follow(state_root)
    except OSError as error:
        raise GitOpsValidationError("cleanup_failed") from error
    _require(not state_root.exists() and not state_root.is_symlink(), "cleanup_failed")


def assert_zero_gitops_residue(state_root: Path) -> None:
    _require(not state_root.exists() and not state_root.is_symlink(), "cleanup_failed")


def _download(pin: GitOpsToolPin, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        pin.url,
        headers={"User-Agent": "StreamScapeTV-ci-workflows-gitops/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            final = urlparse(response.geturl())
            _require(
                final.scheme == "https" and final.hostname in pin.allowed_hosts,
                "tool_download_failed",
                pin.name,
            )
            digest = hashlib.sha256()
            size = 0
            with destination.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    _require(size <= pin.max_bytes, "tool_download_failed", pin.name)
                    digest.update(chunk)
                    output.write(chunk)
    except (OSError, urllib.error.URLError, GitOpsValidationError) as error:
        if isinstance(error, GitOpsValidationError):
            raise
        raise GitOpsValidationError("tool_download_failed", pin.name) from error
    _require(digest.hexdigest() == pin.sha256, "tool_digest_mismatch", pin.name)


def _safe_tar_member(archive: Path, pin: GitOpsToolPin, destination: Path) -> Path:
    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            _require(0 < len(members) <= 128, "tool_archive_rejected", pin.name)
            seen: set[str] = set()
            selected: tarfile.TarInfo | None = None
            total = 0
            for member in members:
                name = member.name.replace("\\", "/")
                pure = PurePosixPath(name)
                _require(
                    name not in seen
                    and not pure.is_absolute()
                    and all(part not in {"", ".", ".."} for part in pure.parts),
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
                _require(total <= pin.max_bytes * 2, "tool_archive_rejected", pin.name)
                if name == pin.archive_member:
                    selected = member
            _require(selected is not None and selected.isfile(), "tool_archive_rejected", pin.name)
            stream = handle.extractfile(selected)
            _require(stream is not None, "tool_archive_rejected", pin.name)
            data = stream.read(pin.max_bytes + 1)
            _require(len(data) <= pin.max_bytes, "tool_archive_rejected", pin.name)
    except (OSError, tarfile.TarError) as error:
        raise GitOpsValidationError("tool_archive_rejected", pin.name) from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    destination.chmod(0o755)
    return destination


def _safe_wheel(archive: Path, pin: GitOpsToolPin, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as handle:
            members = handle.infolist()
            _require(0 < len(members) <= 512, "tool_archive_rejected", pin.name)
            seen: set[str] = set()
            total = 0
            for member in members:
                name = member.filename.replace("\\", "/")
                pure = PurePosixPath(name)
                _require(
                    name not in seen
                    and not pure.is_absolute()
                    and all(part not in {"", ".", ".."} for part in pure.parts),
                    "tool_archive_rejected",
                    pin.name,
                )
                seen.add(name)
                mode = (member.external_attr >> 16) & 0o170000
                _require(mode not in {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO}, "tool_archive_rejected", pin.name)
                total += member.file_size
                _require(total <= pin.max_bytes * 2, "tool_archive_rejected", pin.name)
            _require(pin.archive_member in seen, "tool_archive_rejected", pin.name)
            for member in members:
                if member.is_dir():
                    continue
                target = destination.joinpath**PurePosixPath(member.filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(handle.read(member))
    except (OSError, zipfile.BadZipFile) as error:
        raise GitOpsValidationError("tool_archive_rejected", pin.name) from error


def _load_pinned_yaml(root: Path, expected_version: str) -> Any:
    root = root.resolve()
    original = list(sys.path)
    old_modules = {name: sys.modules.pop(name) for name in list(sys.modules) if name == "yaml" or name.startswith("yaml.") or name == "_yaml"}
    try:
        sys.path.insert(0, str(root))
       yaml = importlib.import_module("yaml")
        _require(getattr(yaml, "__version__", "") == expected_version, "tool_identity_mismatch", "pyyaml")
        module_path = Path(yaml.__file__).resolve()
        _require(root in module_path.parents, "tool_identity_mismatch", "pyyaml")
        return yaml
    except Exception:
        for name in list(sys.modules):
            if name == "yaml" or name.startswith("yaml.") or name == "_yaml":
                sys.modules.pop(name, None)
        sys.modules.update(old_modules)
        raise
    finally:
        sys.path[:] = original


def prepare_gitops_tools(plan: GitOpsPlan, state_root: Path) -> GitOpsTools:
    """Download, verify, safely install, and identify every exact pinned tool."""

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
            yaml_module = _load_pinned_yaml(package_root, pin.version)
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
        _require(re.search(pin.version_pattern, output) is not None, "tool_identity_mismatch", pin.name)
        binaries[pin.name] = binary
        versions[pin.name] = pin.version
    _require(yaml_module is not None, "tool_identity_mismatch", "pyyaml")
    return GitOpsTools(yaml=yaml_module, binaries=binaries, versions=versions)


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


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if PurePosixPath(relative).parts and PurePosixPath(relative).parts[0] == ".git":
            continue
        info = path.lstat()
        _require(not stat.S_ISLNK(info.st_mode), "path_symlink_rejected", relative)
        if path.is_dir():
            continue
        _require(path.is_file(), "invalid_path", relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _source_snapshot(source_root: Path, admitted_sha: str) -> str:
    git = source_root / ".git"
    _require(git.exists(), "source_mismatch", "git metadata missing")
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=source_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise GitOpsValidationError("source_mismatch") from error
    _require(head == admitted_sha, "source_mismatch")
    _require(status == "", "source_dirty")
    return _tree_digest(source_root)


def _changed_paths(source_root: Path, base_sha: str, admitted_sha: str) -> tuple[str, ...]:
    try:
        output = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base_sha}...{admitted_sha}"],
            cwd=source_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise GitOpsValidationError("change_base_invalid") from error
    return tuple(sorted({line.strip() for line in output.splitlines() if line.strip()}))


def _target_matches(target: GitOpsTarget, changed: Sequence[str]) -> bool:
    prefix = target.root.rstrip("/") + "/"
    return any(path == target.root or path.startswith(prefix) for path in changed)


def _selected_targets(plan: GitOpsPlan, source_root: Path) -> tuple[GitOpsTarget, ...]:
    if plan.request.validation_profile.value != "changed-tree":
        return plan.targets
    assert plan.request.change_base_sha is not None
    changed = _changed_paths(source_root, plan.request.change_base_sha, plan.request.admitted_sha)
    return tuple(target for target in plan.targets if _target_matches(target, changed))


def _unique_loader(yaml: Any) -> Any:
    class UniqueLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise GitOpsValidationError("yaml_invalid", "duplicate key")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    return UniqueLoader


def _yaml_documents(path: Path, yaml: Any) -> list[Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise GitOpsValidationError("yaml_invalid") from error
    _require(len(raw) <= 4_000_000, "yaml_invalid", path.name)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitOpsValidationError("yaml_invalid") from error
    _require("\t" not in text, "yaml_style_failed", path.name)
    _require(not text or text.endswith("\n"), "yaml_style_failed", path.name)
    for line in text.splitlines():
        _require(line.rstrip() == line, "yaml_style_failed", path.name)
    try:
        documents = list(yaml.load_all(text, Loader=_unique_loader(yaml)))
    except GitOpsValidationError:
        raise
    except Exception as error:
        raise GitOpsValidationError("yaml_invalid", path.name) from error
    return [document for document in documents if document is not None]


def _schema_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _validate_schema(value: Any, schema: Mapping[str, Any], location: str = "$") -> None:
    allowed = {"type", "required", "properties", "additionalProperties", "items", "enum", "pattern", "minLength", "minimum"}
    _require(set(schema) <= allowed, "schema_invalid", location)
    expected = schema.get("type")
    if expected is not None:
        if isinstance(expected, list):
            _require(_schema_type(value) in expected, "schema_invalid", location)
        else:
            _require(_schema_type(value) == expected, "schema_invalid", location)
    if "enum" in schema:
        _require(value in schema["enum"], "schema_invalid", location)
    if isinstance(value, str):
        if "minLength" in schema:
            _require(len(value) >= int(schema["minLength"]), "schema_invalid", location)
        if "pattern" in schema:
            _require(re.search(str(schema["pattern"]), value) is not None, "schema_invalid", location)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        _require(value >= schema["minimum"], "schema_invalid", location)
    if isinstance(value, list) and "items" in schema:
        item_schema = schema["items"]
        _require(isinstance(item_schema, dict), "schema_invalid", location)
        for index, item in enumerate(value):
            _validate_schema(item, item_schema, f"{location}[{index}]")
    if isinstance(value, dict):
        required = schema.get("required", [])
        _require(isinstance(required, list), "schema_invalid", location)
        for key in required:
            _require(key in value, "schema_invalid", f"{location}.{key}")
        properties = schema.get("properties", {})
        _require(isinstance(properties, dict), "schema_invalid", location)
        for key, child in properties.items():
            _require(isinstance(child, dict), "schema_invalid", location)
            if key in value:
                _validate_schema(value[key], child, f"{location}.{key}")
        if schema.get("additionalProperties") is False:
            _require(set(value) <= set(properties), "schema_invalid", location)


def _load_schema(source_root: Path, relative: str, yaml: Any) -> Mapping[str, Any]:
    path = bounded_path(source_root, relative, must_exist=True, kind="file")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GitOpsValidationError("schema_invalid") from error
    _require(isinstance(schema, dict), "schema_invalid")
    return schema


def _object_identity(value: Any) -> ObjectIdentity | None:
    if not isinstance(value, dict):
        return None
    api_version = value.get("apiVersion")
    kind = value.get("kind")
    metadata = value.get("metadata")
    if not isinstance(api_version, str) or not isinstance(kind, str) or not isinstance(metadata, dict):
        return None
    name = metadata.get("name")
    namespace = metadata.get("namespace", "")
    _require(isinstance(name, str) and bool(name), "yaml_invalid", "metadata.name")
    _require(isinstance(namespace, str), "yaml_invalid", "metadata.namespace")
    return ObjectIdentity(api_version, kind, namespace, name)


def _canonical_documents(documents: Iterable[Any]) -> bytes:
    normalized = sorted(
        (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for value in documents),
    )
    return ("\n".join(normalized) + ("\n" if normalized else "")).encode("utf-8")


def _validate_sops(document: Any, relative: str) -> None:
    _require(isinstance(document, dict), "sops_plaintext_rejected", relative)
    sops = document.get("sops")
    _require(isinstance(sops, dict), "sops_plaintext_rejected", relative)
    _require(isinstance(sops.get("mac"), str) and str(sops["mac"]).startswith("ENC["), "sops_plaintext_rejected", relative)
    _require(isinstance(sops.get("version"), str), "sops_plaintext_rejected", relative)
    for section in ("data", "stringData"):
        values = document.get(section, {})
        _require(isinstance(values, dict), "sops_plaintext_rejected", relative)
        for value in values.values():
            _require(isinstance(value, str) and value.startswith("ENC[AES256_GCM,"), "sops_plaintext_rejected", relative)
    text = json.dumps(document, sort_keys=True)
    _require("PRIVATE KEY" not in text and "sops decrypt" not in text.lower(), "sops_plaintext_rejected", relative)


def _glob_files(root: Path, patterns: Sequence[str]) -> tuple[Path, ...]:
    found: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_dir():
                continue
            _require(path.is_file() and not path.is_symlink(), "path_symlink_rejected", path.name)
            _require(root.resolve() in path.resolve().parents, "path_escape_rejected")
            found.add(path)
    return tuple(sorted(found, key=lambda item: item.relative_to(root).as_posix()))


def _yaml_target(target: GitOpsTarget, source_root: Path, yaml: Any) -> tuple[list[Any], int]:
    root = bounded_path(source_root, target.root, must_exist=True, kind="directory")
    files = _glob_files(root, target.include)
    _require(files, "yaml_invalid", target.target_id)
    schema = _load_schema(source_root, target.schema_path, yaml) if target.schema_path else None
    documents: list[Any] = []
    sops_paths = {
        bounded_path(source_root, relative, must_exist=True, kind="file").resolve()
        for relative in target.sops_files
    }
    for path in files:
        loaded = _yaml_documents(path, yaml)
        for document in loaded:
            if schema is not None:
                _validate_schema(document, schema)
            if path.resolve() in sops_paths:
                _validate_sops(document, path.relative_to(source_root).as_posix())
        documents.extend(loaded)
    _require(sops_paths <= {path.resolve() for path in files}, "sops_plaintext_rejected")
    return documents, len(files)


def _nested_value(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            _fail("required_value_missing", dotted)
        current = current[part]
    _require(current not in {None, ""}, "required_value_missing", dotted)
    return current


def _canonical_dependency_digest(dependencies: Any) -> str:
    encoded = json.dumps(dependencies, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _helm_target(
    target: GitOpsTarget,
    source_root: Path,
    state_root: Path,
    tools: GitOpsTools,
) -> tuple[list[Any], int]:
    yaml = tools.yaml
    helm = tools.binaries.get("helm")
    _require(helm is not None, "tool_identity_mismatch", "helm")
    root = bounded_path(source_root, target.root, must_exist=True, kind="directory")
    chart_path = root / "Chart.yaml"
    _require(chart_path.is_file() and not chart_path.is_symlink(), "helm_lock_invalid")
    charts = _yaml_documents(chart_path, yaml)
    _require(len(charts) == 1 and isinstance(charts[0], dict), "helm_lock_invalid")
    chart = charts[0]
    dependencies = chart.get("dependencies", [])
    _require(isinstance(dependencies, list), "helm_lock_invalid")
    lock_path = root / "Chart.lock"
    if dependencies:
        _require(lock_path.is_file() and not lock_path.is_symlink(), "helm_lock_invalid")
        locks = _yaml_documents(lock_path, yaml)
        _require(len(locks) == 1 and isinstance(locks[0], dict), "helm_lock_invalid")
        lock = locks[0]
        _require(lock.get("dependencies") == dependencies, "helm_lock_invalid")
        _require(lock.get("digest") == _canonical_dependency_digest(dependencies), "helm_lock_invalid")
    else:
        _require(not target.vendored_dependencies, "helm_lock_invalid")
    declared = {(htr(row.get("name")), str(row.get("version"))) for row in dependencies if isinstance(row, dict)}
    _require(len(declared) == len(dependencies), "helm_lock_invalid")
    for row in dependencies:
        version = str(row.get("version", ""))
        repository = str(row.get("repository", ""))
        _require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None, "helm_lock_invalid")
        _require(repository.startswith("file://") and ".." not in PurePosixPath(repository[7:]).parts, "helm_lock_invalid")
    for dependency in target.vendored_dependencies:
        _require((dependency.name, dependency.version) in declared, "helm_lock_invalid")
        path = bounded_path(source_root, dependency.path, must_exist=True, kind="directory")
        _require(_tree_digest(path) == dependency.tree_sha256, "helm_lock_invalid", dependency.name)
        metadata = _yaml_documents(path / "Chart.yaml", yaml)
        _require(
            len(metadata) == 1
            and isinstance(metadata[0], dict)
            and metadata[0].get("name") == dependency.name
            and str(metadata[0].get("version")) == dependency.version,
            "helm_lock_invalid",
            dependency.name,
      )
    values: dict[str, Any] = {}
    value_args: list[str] = []
    for relative in target.values_files:
        path = bounded_path(source_root, relative, must_exist=True, kind="file")
        loaded = _yaml_documents(path, yaml)
        _require(len(loaded) == 1 and isinstance(loaded[0], dict), "helm_failed")
        values.update(loaded[0])
        value_args.extend(("--values", str(path)))
    for dotted in target.required_values:
        _nested_value(values, dotted)
    environment = _tool_environment(state_root)
    lint = (str(helm), "lint", str(root), "--strict", *value_args)
    _run(lint, cwd=source_root, environment=environment, timeout=120, max_output=512_000, code="helm_failed")
    template = (
        str(helm),
        "template",
        "gitops-validation",
        str(root),
        "--namespace",
        "gitops-validation",
        "--include-crds",
        *value_args,
    )
    first = _run(template, cwd=source_root, environment=environment, timeout=120, max_output=4_000_000, code="helm_failed")
    second = _run(template, cwd=source_root, environment=environment, timeout=120, max_output=4_000_000, code="helm_failed")
    _require(first == second, "render_drift", target.target_id)
    try:
        documents = [value for value in yaml.load_all(first, Loader=_unique_loader(yaml)) if value is not None]
    except Exception as error:
        raise GitOpsValidationError("helm_failed") from error
    _compare_expected(target, source_root, documents, yaml)
    return documents, 2 + len(target.values_files) + len(target.vendored_dependencies)


def _kustomization_path(root: Path) -> Path:
    candidates = [root / name for name in ("kustomization.yaml", "kustomization.yml", "Kustomization") if (root / name).exists()]
    _require(len(candidates) == 1, "kustomize_invalid", root.name)
    _require(candidates[0].is_file() and not candidates[0].is_symlink(), "kustomize_invalid")
    return candidates[0]


def _validate_kustomization(root: Path, yaml: Any, visited: set[Path]) -> None:
    root = root.resolve()
    _require(root not in visited, "kustomize_invalid", "cycle")
    visited.add(root)
    path = _kustomization_path(root)
    documents = _yaml_documents(path, yaml)
    _require(len(documents) == 1 and isinstance(documents[0], dict), "kustomize_invalid")
    data = documents[0]
    forbidden = {"helmCharts", "generators", "transformers", "plugins", "exec"}
    _require(not (set(data) & forbidden), "kustomize_invalid", "plugin or Helm escape")
    references: list[str] = []
    for field in ("resources", "bases", "components", "patchesStrategicMerge"):
        value = data.get(field, [])
        _require(isinstance(value, list), "kustomize_invalid", field)
        references.extend(item for item in value if isinstance(item, str))
        _require(len(references) >= len(value), "kustomize_invalid", field)
    patches = data.get("patches", [])
    _require(isinstance(patches, list), "kustomize_invalid")
    for patch in patches:
        if isinstance(patch, dict) and isinstance(patch.get("path"), str):
            references.append(patch["path"])
        elif isinstance(patch, str):
            references.append(patch)
        else:
            _fail("kustomize_invalid", "patch")
    for reference in references:
        _require(
            "://" not in reference
            and not reference.startswith("git::")
            and not PurePosixPath(reference).is_absolute()
            and ".." not in PurePosixPath(reference).parts
            and "\\" not in reference,
            "kustomize_invalid",
            reference,
        )
        candidate = root.joinpath(*PurePosixPath(reference).parts)
        current = root
        for part in PurePosixPath(reference).parts:
            current /= part
            _require(not current.is_symlink(), "path_symlink_rejected", reference)
        resolved = candidate.resolve(strict=False)
        _require(root == resolved or root in resolved.parents, "path_escape_rejected", reference)
        _require(resolved.exists(), "kustomize_invalid", reference)
        if resolved.is_dir():
            _validate_kustomization(resolved, yaml, visited)


def _kustomize_target(
    target: GitOpsTarget,
    source_root: Path,
    state_root: Path,
    tools: GitOpsTools,
) -> tuple[list[Any], int]:
    yaml = tools.yaml
    binary = tools.binaries.get("kustomize")
    _require(binary is not None, "tool_identity_mismatch", "kustomize")
    root = bounded_path(source_root, target.root, must_exist=True, kind="directory")
    _validate_kustomization(root, yaml, set())
    environment = _tool_environment(state_root)
    command = (str(binary), "build", str(root), "--load-restrictor=LoadRestrictionsRootOnly")
    first = _run(command, cwd=source_root, environment=environment, timeout=120, max_output=4_000_000, code="kustomize_failed")
    second = _run(command, cwd=source_root, environment=environment, timeout=120, max_output=4_000_000, code="kustomize_failed")
    _require(first == second, "render_drift", target.target_id)
    try:
        documents = [value for value in yaml.load_all(first, Loader=_unique_loader(yaml)) if value is not None]
    except Exception as error:
        raise GitOpsValidationError("kustomize_failed") from error
    _compare_expected(target, source_root, documents, yaml)
    files = _glob_files(root, target.include)
    return documents, len(files)


def _compare_expected(target: GitOpsTarget, source_root: Path, documents: Sequence[Any], yaml: Any) -> None:
    if target.expected_render_path is None:
        return
    expected_path = bounded_path(source_root, target.expected_render_path, must_exist=True, kind="file")
    expected = _yaml_documents(expected_path, yaml)
    _require(_canonical_documents(expected) == _canonical_documents(documents), "render_drift", target.target_id)


def _policy(
    plan: GitOpsPlan,
    source_root: Path,
    state_root: Path,
) -> str:
    policy = plan.policy_script
    if policy is None:
        return "skipped"
    _require(plan.request.validation_profile.value in policy.allowed_profiles, "policy_profile_rejected")
    script = bounded_path(source_root, policy.path, must_exist=True, kind="file")
    _require(file_sha256(script) == policy.sha256, "policy_profile_rejected")
    environment = _tool_environment(state_root)
    environment.update(
        {
            "GITOPS_VALIDATION_PROFILE": plan.request.validation_profile.value,
            "GITOPS_ADMITTED_SHA": plan.request.admitted_sha,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    _require(not (set(environment) & set(_FORBIDDEN_POLICY_ENV)), "policy_failed")
    argv = (sys.executable if policy.argv[0] == "python3" else policy.argv[0], str(script))
    _run(
        argv,
        cwd=source_root,
        environment=environment,
        timeout=policy.timeout_seconds,
        max_output=policy.max_output_bytes,
        code="policy_failed",
    )
    return "success"


def _execute(
    plan: GitOpsPlan,
    source_root: Path,
    state_root: Path,
    tools: GitOpsTools,
) -> GitOpsResult:
    before = _source_snapshot(source_root, plan.request.admitted_sha)
    targets = _selected_targets(plan, source_root)
    owners: dict[ObjectIdentity, str] = {}
    all_documents: list[Any] = []
    validated_files = 0
    rendered_objects = 0
    for target in targets:
        if target.kind is GitOpsTargetKind.YAML:
            documents, count = _yaml_target(target, source_root, tools.yaml)
        elif target.kind is GitOpsTargetKind.HELM:
            documents, count = _helm_target(target, source_root, state_root, tools)
        else:
            documents, count = _kustomize_target(target, source_root, state_root, tools)
        validated_files += count
        for document in documents:
            identity = _object_identity(document)
            if identity is not None:
                previous = owners.get(identity)
                _require(
                    previous is None or previous == target.target_id,
                    "duplicate_object_ownership",
                    identity.label,
                )
                owners[identity] = target.target_id
                rendered_objects += 1
        all_documents.extend(documents)
    policy_result = _policy(plan, source_root, state_root)
    after = _source_snapshot(source_root, plan.request.admitted_sha)
    _require(before == after, "source_mutated")
    render_digest = hashlib.sha256(_canonical_documents(all_documents)).hexdigest()
    evidence = hashlib.sha256(
        compact_json(
            {
                "source": plan.request.admitted_sha,
                "consumer": plan.request.consumer_contract,
                "profile": plan.request.validation_profile.value,
                "targets": [target.target_id for target in targets],
                "render": render_digest,
                "tools": dict(tools.versions),
            }
        ).encode("utf-8")
    ).hexdigest()[:32]
    return GitOpsResult(
        plan=plan,
        rendered_objects=rendered_objects,
        validated_files=validated_files,
        selected_targets=tuple(target.target_id for target in targets),
        render_digest=render_digest,
        policy_result=policy_result,
        clean_tree=True,
        cleanup_result="not-run",
        evidence_id=f"gitops-{evidence}",
        tool_versions=dict(tools.versions),
    )


def execute_gitops_plan(
    plan: GitOpsPlan,
    source_root: Path,
    state_root: Path,
    *,
    tools: GitOpsTools | None = None,
) -> GitOpsResult:
    """Execute one exact plan and preserve primary plus cleanup failure."""

    owns_tools = tools is None
    if owns_tools:
        runtime = prepare_gitops_tools(plan, state_root)
    else:
        initialize_gitops_state(state_root)
        runtime = tools
    assert runtime is not None
    try:
        return _execute(plan, source_root, state_root, runtime)
    except BaseException as primary:
        try:
            cleanup_gitops_state(state_root)
        except BaseException as cleanup:
            primary_code = getattr(primary, "code", type(primary).__name__)
            cleanup_code = getattr(cleanup, "code", type(cleanup).__name__)
            raise GitOpsValidationError(
                "primary_and_cleanup_failed",
                f"primary={primary_code};cleanup={cleanup_code}",
            ) from primary
        raise
