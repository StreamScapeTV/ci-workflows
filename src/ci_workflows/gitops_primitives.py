"""Product-neutral GitOps, YAML, JSON, Kustomize and client validation primitives."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import yaml

from .packaging_primitives import PackagingError, helm_template
from .runtime_primitives import (
    ProcessResult,
    RuntimePrimitiveError,
    finalize_temporary_paths,
    run_process,
)

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_SAFE_TOOL = re.compile(r"^[A-Za-z0-9_.+-]+$")
_FLUX_KINDS = frozenset(
    {
        "Bucket",
        "GitRepository",
        "HelmChart",
        "HelmRelease",
        "HelmRepository",
        "Kustomization",
        "OCIRepository",
    }
)
_CLUSTER_ENVIRONMENT = frozenset(
    {
        "KUBECONFIG",
        "KUBERNETES_MASTER",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_SERVICE_PORT_HTTPS",
        "SOPS_AGE_KEY",
        "SOPS_AGE_KEY_FILE",
        "SOPS_GPG_EXEC",
        "FLUX_TOKEN",
    }
)


class GitOpsPrimitiveError(RuntimeError):
    """Fail closed with one stable non-secret GitOps primitive code."""

    def __init__(
        self,
        code: str,
        operation: str,
        *,
        returncode: int | None = None,
    ) -> None:
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("GitOps primitive error code must be a safe identifier")
        self.code = code
        self.operation = operation
        self.returncode = returncode
        message = f"{code}: {operation}"
        if returncode is not None:
            message += f" exited with status {returncode}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ResourceMetadata:
    source: str
    document_index: int
    api_version: str | None
    kind: str | None
    name: str | None
    namespace: str | None


@dataclass(frozen=True, slots=True)
class DocumentValidationResult:
    path: Path
    format: str
    document_count: int
    resources: tuple[ResourceMetadata, ...]


@dataclass(frozen=True, slots=True)
class DirectoryValidationResult:
    root: Path
    files: tuple[DocumentValidationResult, ...]
    document_count: int
    resources: tuple[ResourceMetadata, ...]


@dataclass(frozen=True, slots=True)
class RenderResult:
    operation: str
    rendered: str
    resources: tuple[ResourceMetadata, ...]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    operation: str
    resources: tuple[ResourceMetadata, ...]
    stdout: str


@dataclass(frozen=True, slots=True)
class SourceInspectionResult:
    files: tuple[DocumentValidationResult, ...]
    resources: tuple[ResourceMetadata, ...]


class CommandRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        stdin: str = "",
        timeout_seconds: float | None = None,
    ) -> ProcessResult: ...


class RuntimeCommandRunner:
    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        stdin: str = "",
        timeout_seconds: float | None = None,
    ) -> ProcessResult:
        return run_process(
            arguments,
            cwd=cwd,
            environment=environment,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
        )


def _fail(
    code: str,
    operation: str,
    *,
    returncode: int | None = None,
) -> None:
    raise GitOpsPrimitiveError(code, operation, returncode=returncode)


def _text(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        _fail("argument_invalid", field)
    return value


def _environment(value: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if value is None else value
    result: dict[str, str] = {}
    for name, item in source.items():
        if (
            not isinstance(name, str)
            or not name
            or any(character in name for character in ("\x00", "\r", "\n", "="))
            or not isinstance(item, str)
            or "\x00" in item
        ):
            _fail("environment_invalid", "environment")
        if name not in _CLUSTER_ENVIRONMENT:
            result[name] = item
    return result


def _real_file(path: Path, operation: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        _fail("path_invalid", operation)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise GitOpsPrimitiveError("path_invalid", operation) from error
    if not resolved.is_file():
        _fail("path_invalid", operation)
    return resolved


def _real_directory(path: Path, operation: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        _fail("path_invalid", operation)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise GitOpsPrimitiveError("path_invalid", operation) from error
    if not resolved.is_dir():
        _fail("path_invalid", operation)
    return resolved


def _tool(value: str, allowed: frozenset[str], operation: str) -> str:
    tool = _text(value, "tool")
    if _SAFE_TOOL.fullmatch(tool) is None or tool not in allowed:
        _fail("tool_unsupported", operation)
    return tool


def _run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None,
    operation: str,
    stdin: str = "",
    timeout_seconds: float = 300,
    runner: CommandRunner | None = None,
) -> ProcessResult:
    command_runner = runner or RuntimeCommandRunner()
    try:
        result = command_runner.run(
            tuple(arguments),
            cwd=_real_directory(cwd, operation),
            environment=_environment(environment),
            stdin=stdin,
            timeout_seconds=timeout_seconds,
        )
    except RuntimePrimitiveError as error:
        raise GitOpsPrimitiveError("process_failed", operation) from error
    if result.timed_out:
        _fail("process_timeout", operation)
    if result.returncode != 0:
        _fail("process_failed", operation, returncode=result.returncode)
    return result


def _resource_metadata(
    value: Any,
    *,
    source: str,
    document_index: int,
) -> ResourceMetadata | None:
    if not isinstance(value, Mapping):
        return None
    api_version = value.get("apiVersion")
    kind = value.get("kind")
    metadata = value.get("metadata")
    if api_version is not None and not isinstance(api_version, str):
        _fail("document_invalid", "resource metadata")
    if kind is not None and not isinstance(kind, str):
        _fail("document_invalid", "resource metadata")
    name: str | None = None
    namespace: str | None = None
    if metadata is not None:
        if not isinstance(metadata, Mapping):
            _fail("document_invalid", "resource metadata")
        raw_name = metadata.get("name")
        raw_namespace = metadata.get("namespace")
        if raw_name is not None and not isinstance(raw_name, str):
            _fail("document_invalid", "resource metadata")
        if raw_namespace is not None and not isinstance(raw_namespace, str):
            _fail("document_invalid", "resource metadata")
        name = raw_name
        namespace = raw_namespace
    if api_version is None and kind is None and name is None and namespace is None:
        return None
    return ResourceMetadata(
        source=source,
        document_index=document_index,
        api_version=api_version,
        kind=kind,
        name=name,
        namespace=namespace,
    )


def _parse_yaml_text(text: str, *, source: str) -> tuple[Any, ...]:
    if not isinstance(text, str) or "\x00" in text:
        _fail("document_invalid", "yaml")
    try:
        values = tuple(yaml.safe_load_all(text))
    except yaml.YAMLError as error:
        raise GitOpsPrimitiveError("yaml_invalid", "parse yaml") from error
    return tuple(value for value in values if value is not None)


def _resources(values: Sequence[Any], *, source: str) -> tuple[ResourceMetadata, ...]:
    resources: list[ResourceMetadata] = []
    for index, value in enumerate(values, start=1):
        metadata = _resource_metadata(value, source=source, document_index=index)
        if metadata is not None:
            resources.append(metadata)
    return tuple(resources)


def validate_yaml_file(path: Path) -> DocumentValidationResult:
    """Parse one real YAML file and return document/resource metadata."""

    resolved = _real_file(path, "validate yaml file")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise GitOpsPrimitiveError("document_read_failed", "validate yaml file") from error
    values = _parse_yaml_text(text, source=str(resolved))
    return DocumentValidationResult(
        path=resolved,
        format="yaml",
        document_count=len(values),
        resources=_resources(values, source=str(resolved)),
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_json_file(path: Path) -> DocumentValidationResult:
    """Parse one strict JSON file and return resource metadata when applicable."""

    resolved = _real_file(path, "validate json file")
    try:
        text = resolved.read_text(encoding="utf-8")
        value = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise GitOpsPrimitiveError("json_invalid", "validate json file") from error
    values = (value,)
    return DocumentValidationResult(
        path=resolved,
        format="json",
        document_count=1,
        resources=_resources(values, source=str(resolved)),
    )


def validate_config_directory(
    root: Path,
    *,
    recursive: bool = True,
    require_files: bool = True,
) -> DirectoryValidationResult:
    """Validate every YAML/JSON file in one caller-owned directory."""

    resolved = _real_directory(root, "validate config directory")
    pattern = "**/*" if recursive else "*"
    candidates = sorted(
        (
            path
            for path in resolved.glob(pattern)
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() in {".json", ".yaml", ".yml"}
        ),
        key=lambda path: path.as_posix(),
    )
    if require_files and not candidates:
        _fail("config_files_missing", "validate config directory")
    files: list[DocumentValidationResult] = []
    for path in candidates:
        files.append(
            validate_json_file(path)
            if path.suffix.casefold() == ".json"
            else validate_yaml_file(path)
        )
    resources = tuple(resource for file in files for resource in file.resources)
    return DirectoryValidationResult(
        root=resolved,
        files=tuple(files),
        document_count=sum(file.document_count for file in files),
        resources=resources,
    )


def render_kustomize(
    root: Path,
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "kustomize",
    runner: CommandRunner | None = None,
) -> RenderResult:
    """Run a local Kustomize build and parse the rendered resource metadata."""

    resolved = _real_directory(root, "kustomize build")
    if not any(
        (resolved / name).is_file() and not (resolved / name).is_symlink()
        for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")
    ):
        _fail("kustomization_missing", "kustomize build")
    executable = _tool(tool, frozenset({"kustomize"}), "kustomize build")
    result = _run(
        [executable, "build", str(resolved)],
        cwd=resolved,
        environment=environment,
        operation="kustomize build",
        runner=runner,
    )
    values = _parse_yaml_text(result.stdout, source=f"kustomize:{resolved}")
    return RenderResult(
        operation="kustomize build",
        rendered=result.stdout,
        resources=_resources(values, source=f"kustomize:{resolved}"),
    )


def render_helm_chart(
    chart: Path,
    *,
    values: Sequence[Path] = (),
    release_name: str = "release",
    namespace: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> RenderResult:
    """Render a chart through the shared packaging primitive and inspect resources."""

    resolved = _real_directory(chart, "helm render")
    try:
        rendered = helm_template(
            resolved,
            values=tuple(values),
            release_name=release_name,
            namespace=namespace,
            environment=_environment(environment),
            tool="helm",
        )
    except PackagingError as error:
        raise GitOpsPrimitiveError("helm_render_failed", "helm render") from error
    parsed = _parse_yaml_text(rendered, source=f"helm:{resolved}")
    return RenderResult(
        operation="helm render",
        rendered=rendered,
        resources=_resources(parsed, source=f"helm:{resolved}"),
    )


def validate_kubernetes_client_dry_run(
    rendered: str,
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    tool: str = "kubectl",
    runner: CommandRunner | None = None,
) -> ValidationResult:
    """Run kubectl client dry-run without kubeconfig or service-account authority."""

    executable = _tool(tool, frozenset({"kubectl"}), "kubectl client dry-run")
    values = _parse_yaml_text(rendered, source="kubectl:stdin")
    result = _run(
        [
            executable,
            "--kubeconfig=/dev/null",
            "apply",
            "--dry-run=client",
            "--validate=false",
            "-f",
            "-",
        ],
        cwd=cwd,
        environment=environment,
        operation="kubectl client dry-run",
        stdin=rendered,
        runner=runner,
    )
    return ValidationResult(
        operation="kubectl client dry-run",
        resources=_resources(values, source="kubectl:stdin"),
        stdout=result.stdout,
    )


def validate_kubernetes_schema(
    rendered: str,
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    tool: str = "kubeconform",
    schema_locations: Sequence[str] = (),
    strict: bool = True,
    ignore_missing_schemas: bool = False,
    runner: CommandRunner | None = None,
) -> ValidationResult:
    """Validate rendered Kubernetes YAML with caller-supplied schema tooling."""

    executable = _tool(tool, frozenset({"kubeconform"}), "kubernetes schema validation")
    arguments = [executable]
    if strict:
        arguments.append("-strict")
    arguments.append("-summary")
    if ignore_missing_schemas:
        arguments.append("-ignore-missing-schemas")
    for location in schema_locations:
        arguments.extend(["-schema-location", _text(location, "schema location")])
    arguments.append("-")
    values = _parse_yaml_text(rendered, source="kubeconform:stdin")
    result = _run(
        arguments,
        cwd=cwd,
        environment=environment,
        operation="kubernetes schema validation",
        stdin=rendered,
        runner=runner,
    )
    return ValidationResult(
        operation="kubernetes schema validation",
        resources=_resources(values, source="kubeconform:stdin"),
        stdout=result.stdout,
    )


def _local_directory_argument(value: str, *, cwd: Path, operation: str) -> Path:
    root = _real_directory(cwd, operation)
    candidate = Path(_text(value, "source path"))
    candidate = candidate if candidate.is_absolute() else root / candidate
    resolved = _real_directory(candidate, operation)
    if resolved != root and root not in resolved.parents:
        _fail("path_outside_root", operation)
    return resolved


def run_read_only_source_validation(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    stdin: str = "",
    runner: CommandRunner | None = None,
) -> ValidationResult:
    """Run one bounded caller-selected read-only source validation command.

    Supported shapes are exactly:
    ``kustomize build <local-dir>``, ``helm lint <local-chart>``,
    ``helm template <release> <local-chart>``, or ``kubeconform`` (stdin).
    """

    if isinstance(arguments, (str, bytes)) or not arguments:
        _fail("command_invalid", "source validation")
    argv = tuple(_text(value, "command argument") for value in arguments)
    tool = argv[0]
    normalized: tuple[str, ...]
    input_text = stdin
    if tool == "kustomize":
        if len(argv) != 3 or argv[1] != "build" or stdin:
            _fail("command_not_read_only", "source validation")
        source = _local_directory_argument(argv[2], cwd=cwd, operation="source validation")
        normalized = ("kustomize", "build", str(source))
    elif tool == "helm" and len(argv) >= 2 and argv[1] == "lint":
        if len(argv) != 3 or stdin:
            _fail("command_not_read_only", "source validation")
        source = _local_directory_argument(argv[2], cwd=cwd, operation="source validation")
        normalized = ("helm", "lint", str(source))
    elif tool == "helm" and len(argv) >= 2 and argv[1] == "template":
        if len(argv) != 4 or stdin:
            _fail("command_not_read_only", "source validation")
        release = _text(argv[2], "release name")
        source = _local_directory_argument(argv[3], cwd=cwd, operation="source validation")
        normalized = ("helm", "template", release, str(source))
    elif tool == "kubeconform":
        if len(argv) != 1 or not stdin:
            _fail("command_not_read_only", "source validation")
        normalized = ("kubeconform", "-strict", "-summary", "-")
    elif tool in {"kustomize", "helm", "kubeconform"}:
        _fail("command_not_read_only", "source validation")
    else:
        _fail("tool_unsupported", "source validation")
    result = _run(
        normalized,
        cwd=cwd,
        environment=environment,
        operation="source validation",
        stdin=input_text,
        runner=runner,
    )
    resources: tuple[ResourceMetadata, ...] = ()
    parse_output = tool == "kustomize" or (tool == "helm" and normalized[1] == "template")
    if parse_output:
        values = _parse_yaml_text(result.stdout, source=f"{tool}:stdout")
        resources = _resources(values, source=f"{tool}:stdout")
    return ValidationResult(
        operation="source validation",
        resources=resources,
        stdout=result.stdout,
    )


def inspect_gitops_sources(paths: Sequence[Path]) -> SourceInspectionResult:
    """Inspect caller-selected YAML/JSON sources and return Flux/Kustomization objects."""

    if isinstance(paths, (str, bytes)) or not paths:
        _fail("source_files_missing", "inspect GitOps sources")
    files: list[DocumentValidationResult] = []
    resources: list[ResourceMetadata] = []
    for path in paths:
        candidate = Path(path)
        suffix = candidate.suffix.casefold()
        if suffix == ".json":
            result = validate_json_file(candidate)
        elif suffix in {".yaml", ".yml"}:
            result = validate_yaml_file(candidate)
        else:
            _fail("source_format_unsupported", "inspect GitOps sources")
        files.append(result)
        resources.extend(
            resource for resource in result.resources if resource.kind in _FLUX_KINDS
        )
    return SourceInspectionResult(files=tuple(files), resources=tuple(resources))


def cleanup_gitops_state(paths: Sequence[Path], *, root: Path) -> int:
    """Idempotently remove bounded temporary render state without deleting the root."""

    try:
        return finalize_temporary_paths(tuple(paths), root=root)
    except RuntimePrimitiveError as error:
        raise GitOpsPrimitiveError("cleanup_failed", "cleanup GitOps state") from error
