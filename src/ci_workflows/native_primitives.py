"""Product-neutral native configure, build, archive, inspection, and cleanup primitives."""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .runtime_primitives import (
    ProcessResult,
    RuntimePrimitiveError,
    finalize_temporary_paths,
    run_process,
)

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_DEFINITION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_TARGET = re.compile(r"^[A-Za-z0-9_.:/+@=-]{1,255}$")
_MAX_ARGUMENTS = 256
_MAX_ARGUMENT_BYTES = 64 * 1024
_MAX_ARCHIVE_MEMBERS = 4096
_MAX_OUTPUTS = 256
_DEFAULT_TIMEOUT_SECONDS = 60 * 60
_MAX_JOBS = 256

ProcessRunner = Callable[..., ProcessResult]


class NativePrimitiveError(RuntimeError):
    """Fail closed with one stable, non-secret native primitive code."""

    def __init__(self, code: str) -> None:
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("native primitive error code must be a safe identifier")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ConfigureStep:
    """One shell-free configure/autoconf-style process step."""

    tool: str
    arguments: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True, slots=True)
class NativeCommandResult:
    """Stable successful native command result without environment or host identity."""

    operation: str
    arguments: tuple[str, ...]
    cwd: str
    returncode: int


@dataclass(frozen=True, slots=True)
class NativeOutput:
    """One inspected native output file."""

    path: str
    kind: str
    size_bytes: int
    sha256: str
    executable: bool


@dataclass(frozen=True, slots=True)
class NativeArchive:
    """One deterministic archive result."""

    path: str
    format: str
    size_bytes: int
    sha256: str
    members: tuple[str, ...]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise NativePrimitiveError(code)


def _text(value: object, *, code: str, allow_empty: bool = False) -> str:
    _require(isinstance(value, str), code)
    text = str(value)
    _require(
        (allow_empty or bool(text))
        and "\x00" not in text
        and "\r" not in text
        and "\n" not in text
        and len(text.encode("utf-8")) <= 4096,
        code,
    )
    return text


def _arguments(values: Sequence[str], *, code: str) -> tuple[str, ...]:
    _require(
        isinstance(values, Sequence) and not isinstance(values, (str, bytes)),
        code,
    )
    result = tuple(_text(value, code=code) for value in values)
    _require(len(result) <= _MAX_ARGUMENTS, code)
    _require(
        sum(len(value.encode("utf-8")) for value in result) <= _MAX_ARGUMENT_BYTES,
        code,
    )
    return result


def _tool(value: str, *, code: str = "native_tool_invalid") -> str:
    return _text(value, code=code)


def _existing_directory(path: Path, *, code: str) -> Path:
    candidate = Path(path)
    _require(
        candidate.is_absolute() and not candidate.is_symlink() and candidate.is_dir(),
        code,
    )
    try:
        return candidate.resolve(strict=True)
    except OSError as error:
        raise NativePrimitiveError(code) from error


def _future_directory(path: Path, *, code: str) -> Path:
    candidate = Path(path)
    _require(candidate.is_absolute() and not candidate.is_symlink(), code)
    if candidate.exists():
        _require(candidate.is_dir(), code)
        try:
            return candidate.resolve(strict=True)
        except OSError as error:
            raise NativePrimitiveError(code) from error
    parent = candidate.parent
    _require(parent.is_absolute() and not parent.is_symlink() and parent.is_dir(), code)
    try:
        parent_real = parent.resolve(strict=True)
    except OSError as error:
        raise NativePrimitiveError(code) from error
    return parent_real / candidate.name


def _runner_environment(environment: Mapping[str, str]) -> Mapping[str, str]:
    _require(isinstance(environment, Mapping), "native_environment_invalid")
    for name, value in environment.items():
        _require(
            isinstance(name, str)
            and isinstance(value, str)
            and "\x00" not in name
            and "\x00" not in value,
            "native_environment_invalid",
        )
    return environment


def _timeout(value: float | int) -> float:
    _require(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and 0 < float(value) <= 24 * 60 * 60,
        "native_timeout_invalid",
    )
    return float(value)


def _run(
    operation: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | int,
    runner: ProcessRunner,
) -> NativeCommandResult:
    operation = _text(operation, code="native_operation_invalid")
    arguments = _arguments(argv, code="native_arguments_invalid")
    working_directory = _existing_directory(cwd, code="native_cwd_invalid")
    timeout = _timeout(timeout_seconds)
    _runner_environment(environment)
    try:
        result = runner(
            arguments,
            cwd=working_directory,
            environment=environment,
            timeout_seconds=timeout,
        )
    except NativePrimitiveError:
        raise
    except RuntimePrimitiveError as error:
        raise NativePrimitiveError("native_process_boundary_failed") from error
    except Exception as error:
        raise NativePrimitiveError("native_process_boundary_failed") from error
    _require(isinstance(result, ProcessResult), "native_process_result_invalid")
    if result.timed_out:
        raise NativePrimitiveError(f"{operation}_timeout")
    if result.returncode != 0:
        raise NativePrimitiveError(f"{operation}_failed")
    return NativeCommandResult(
        operation,
        arguments,
        str(working_directory),
        int(result.returncode),
    )


def run_configure_steps(
    steps: Sequence[ConfigureStep],
    *,
    environment: Mapping[str, str],
    timeout_seconds: float | int = _DEFAULT_TIMEOUT_SECONDS,
    runner: ProcessRunner = run_process,
) -> tuple[NativeCommandResult, ...]:
    """Run caller-owned configure/autoconf-style steps without invoking a shell."""

    _require(
        isinstance(steps, Sequence)
        and not isinstance(steps, (str, bytes))
        and 0 < len(steps) <= 32,
        "configure_steps_invalid",
    )
    results: list[NativeCommandResult] = []
    for step in steps:
        _require(isinstance(step, ConfigureStep), "configure_steps_invalid")
        tool = _tool(step.tool, code="configure_tool_invalid")
        arguments = _arguments(step.arguments, code="configure_arguments_invalid")
        results.append(
            _run(
                "configure",
                (tool, *arguments),
                cwd=step.cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
        )
    return tuple(results)


def cmake_configure(
    *,
    source_dir: Path,
    build_dir: Path,
    definitions: Mapping[str, str] | None = None,
    generator: str = "",
    options: Sequence[str] = (),
    environment: Mapping[str, str],
    cmake: str = "cmake",
    timeout_seconds: float | int = _DEFAULT_TIMEOUT_SECONDS,
    runner: ProcessRunner = run_process,
) -> NativeCommandResult:
    """Configure one CMake build tree from typed caller-owned paths and definitions."""

    source = _existing_directory(source_dir, code="cmake_source_invalid")
    build = _future_directory(build_dir, code="cmake_build_dir_invalid")
    values = {} if definitions is None else definitions
    _require(
        isinstance(values, Mapping) and len(values) <= 128,
        "cmake_definitions_invalid",
    )
    definition_arguments: list[str] = []
    for name in sorted(values):
        _require(
            isinstance(name, str) and _DEFINITION_NAME.fullmatch(name) is not None,
            "cmake_definitions_invalid",
        )
        value = _text(values[name], code="cmake_definitions_invalid", allow_empty=True)
        definition_arguments.append(f"-D{name}={value}")
    argv: list[str] = [_tool(cmake), "-S", str(source), "-B", str(build)]
    if generator:
        argv.extend(["-G", _text(generator, code="cmake_generator_invalid")])
    argv.extend(definition_arguments)
    argv.extend(_arguments(options, code="cmake_options_invalid"))
    return _run(
        "cmake_configure",
        argv,
        cwd=source,
        environment=environment,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


def _jobs(value: int) -> int:
    _require(type(value) is int and 1 <= value <= _MAX_JOBS, "native_jobs_invalid")
    return value


def _target(value: str, *, code: str) -> str:
    text = _text(value, code=code)
    _require(_TARGET.fullmatch(text) is not None and not text.startswith("-"), code)
    return text


def cmake_build(
    *,
    build_dir: Path,
    jobs: int,
    target: str = "",
    configuration: str = "",
    options: Sequence[str] = (),
    environment: Mapping[str, str],
    cmake: str = "cmake",
    timeout_seconds: float | int = _DEFAULT_TIMEOUT_SECONDS,
    runner: ProcessRunner = run_process,
) -> NativeCommandResult:
    """Build one CMake tree with bounded parallelism and optional target/configuration."""

    build = _existing_directory(build_dir, code="cmake_build_dir_invalid")
    argv: list[str] = [
        _tool(cmake),
        "--build",
        str(build),
        "--parallel",
        str(_jobs(jobs)),
    ]
    if target:
        argv.extend(["--target", _target(target, code="cmake_target_invalid")])
    if configuration:
        argv.extend(["--config", _text(configuration, code="cmake_configuration_invalid")])
    extra = _arguments(options, code="cmake_options_invalid")
    if extra:
        argv.extend(["--", *extra])
    return _run(
        "cmake_build",
        argv,
        cwd=build,
        environment=environment,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


def cmake_install(
    *,
    build_dir: Path,
    install_dir: Path,
    configuration: str = "",
    component: str = "",
    options: Sequence[str] = (),
    environment: Mapping[str, str],
    cmake: str = "cmake",
    timeout_seconds: float | int = _DEFAULT_TIMEOUT_SECONDS,
    runner: ProcessRunner = run_process,
) -> NativeCommandResult:
    """Install one CMake tree into a caller-owned install directory."""

    build = _existing_directory(build_dir, code="cmake_build_dir_invalid")
    install = _future_directory(install_dir, code="cmake_install_dir_invalid")
    argv: list[str] = [
        _tool(cmake),
        "--install",
        str(build),
        "--prefix",
        str(install),
    ]
    if configuration:
        argv.extend(["--config", _text(configuration, code="cmake_configuration_invalid")])
    if component:
        argv.extend(["--component", _text(component, code="cmake_component_invalid")])
    argv.extend(_arguments(options, code="cmake_options_invalid"))
    return _run(
        "cmake_install",
        argv,
        cwd=build,
        environment=environment,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


def _make_like(
    operation: str,
    tool: str,
    *,
    cwd: Path,
    targets: Sequence[str],
    jobs: int,
    options: Sequence[str],
    environment: Mapping[str, str],
    timeout_seconds: float | int,
    runner: ProcessRunner,
) -> NativeCommandResult:
    target_values = tuple(
        _target(value, code=f"{operation}_target_invalid") for value in targets
    )
    _require(target_values, f"{operation}_target_invalid")
    extra = _arguments(options, code=f"{operation}_options_invalid")
    _require(
        not any(
            value == "-j"
            or value.startswith("-j")
            or value.startswith("--jobs")
            for value in extra
        ),
        f"{operation}_options_invalid",
    )
    argv = (_tool(tool), f"-j{_jobs(jobs)}", *extra, *target_values)
    return _run(
        operation,
        argv,
        cwd=cwd,
        environment=environment,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


def run_make(
    *,
    cwd: Path,
    targets: Sequence[str],
    jobs: int,
    options: Sequence[str] = (),
    environment: Mapping[str, str],
    make: str = "make",
    timeout_seconds: float | int = _DEFAULT_TIMEOUT_SECONDS,
    runner: ProcessRunner = run_process,
) -> NativeCommandResult:
    """Run explicit Make targets with caller-owned options and bounded jobs."""

    return _make_like(
        "make",
        make,
        cwd=cwd,
        targets=targets,
        jobs=jobs,
        options=options,
        environment=environment,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


def run_ninja(
    *,
    cwd: Path,
    targets: Sequence[str],
    jobs: int,
    options: Sequence[str] = (),
    environment: Mapping[str, str],
    ninja: str = "ninja",
    timeout_seconds: float | int = _DEFAULT_TIMEOUT_SECONDS,
    runner: ProcessRunner = run_process,
) -> NativeCommandResult:
    """Run explicit Ninja targets with caller-owned options and bounded jobs."""

    return _make_like(
        "ninja",
        ninja,
        cwd=cwd,
        targets=targets,
        jobs=jobs,
        options=options,
        environment=environment,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


def sha256_file(path: Path) -> str:
    """Hash one regular non-symlink file."""

    candidate = Path(path)
    _require(
        candidate.is_absolute() and candidate.is_file() and not candidate.is_symlink(),
        "native_file_invalid",
    )
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise NativePrimitiveError("native_file_read_failed") from error
    return digest.hexdigest()


def _relative_member(value: str, *, code: str) -> Path:
    text = _text(value, code=code)
    path = Path(text)
    _require(
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts),
        code,
    )
    return path


def _contained_path(
    root: Path,
    relative: Path,
    *,
    code: str,
    symlink_code: str | None = None,
) -> Path:
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise NativePrimitiveError(symlink_code or code)
    _require(cursor.exists(), code)
    return cursor


def _archive_entries(root: Path, members: Sequence[str]) -> tuple[tuple[Path, str], ...]:
    base = _existing_directory(root, code="archive_root_invalid")
    _require(
        isinstance(members, Sequence)
        and not isinstance(members, (str, bytes))
        and 0 < len(members) <= _MAX_ARCHIVE_MEMBERS,
        "archive_members_invalid",
    )
    found: dict[str, Path] = {}

    def add(path: Path) -> None:
        relative = path.relative_to(base).as_posix()
        _require(relative and relative not in found, "archive_members_invalid")
        _require(not path.is_symlink(), "archive_symlink_rejected")
        if path.is_dir():
            found[relative] = path
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                add(child)
        else:
            _require(path.is_file(), "archive_member_invalid")
            found[relative] = path
        _require(len(found) <= _MAX_ARCHIVE_MEMBERS, "archive_members_invalid")

    roots_seen: set[str] = set()
    for value in members:
        relative = _relative_member(value, code="archive_members_invalid")
        key = relative.as_posix()
        _require(key not in roots_seen, "archive_members_invalid")
        roots_seen.add(key)
        add(
            _contained_path(
                base,
                relative,
                code="archive_member_invalid",
                symlink_code="archive_symlink_rejected",
            )
        )
    return tuple((found[name], name) for name in sorted(found))


def _normalized_tarinfo(path: Path, arcname: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(
        arcname + ("/" if path.is_dir() and not arcname.endswith("/") else "")
    )
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if path.is_dir() or os.access(path, os.X_OK) else 0o644
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        info.size = 0
    else:
        info.type = tarfile.REGTYPE
        info.size = path.stat().st_size
    return info


def create_deterministic_archive(
    *,
    root: Path,
    members: Sequence[str],
    output_path: Path,
    format: str = "tar.gz",
) -> NativeArchive:
    """Create a deterministic tar or tar.gz archive from bounded caller outputs."""

    archive_format = _text(format, code="archive_format_invalid")
    _require(archive_format in {"tar", "tar.gz"}, "archive_format_invalid")
    base = _existing_directory(root, code="archive_root_invalid")
    output = Path(output_path)
    _require(output.is_absolute() and not output.is_symlink(), "archive_output_invalid")
    parent = _existing_directory(output.parent, code="archive_output_invalid")
    output = parent / output.name
    try:
        output.relative_to(base)
    except ValueError:
        pass
    else:
        raise NativePrimitiveError("archive_output_inside_root")
    entries = _archive_entries(base, members)

    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=parent,
        )
        os.close(descriptor)
        temporary = Path(name)
        with temporary.open("wb") as raw:
            if archive_format == "tar.gz":
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=raw,
                    mtime=0,
                    compresslevel=9,
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed,
                        mode="w",
                        format=tarfile.GNU_FORMAT,
                    ) as archive:
                        for path, arcname in entries:
                            info = _normalized_tarinfo(path, arcname)
                            if path.is_dir():
                                archive.addfile(info)
                            else:
                                with path.open("rb") as source:
                                    archive.addfile(info, source)
            else:
                with tarfile.open(
                    fileobj=raw,
                    mode="w",
                    format=tarfile.GNU_FORMAT,
                ) as archive:
                    for path, arcname in entries:
                        info = _normalized_tarinfo(path, arcname)
                        if path.is_dir():
                            archive.addfile(info)
                        else:
                            with path.open("rb") as source:
                                archive.addfile(info, source)
        os.replace(temporary, output)
        temporary = None
    except NativePrimitiveError:
        raise
    except OSError as error:
        raise NativePrimitiveError("archive_create_failed") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    return NativeArchive(
        path=str(output),
        format=archive_format,
        size_bytes=output.stat().st_size,
        sha256=sha256_file(output),
        members=tuple(name for _, name in entries),
    )


def _output_kind(path: Path) -> str:
    name = path.name.casefold()
    if name.endswith((".a", ".lib")):
        return "static-library"
    if name.endswith((".dylib", ".dll", ".so")) or ".so." in name:
        return "shared-library"
    if os.access(path, os.X_OK):
        return "executable"
    return "file"


def inspect_native_outputs(
    *,
    root: Path,
    outputs: Sequence[str],
) -> tuple[NativeOutput, ...]:
    """Inspect expected native files/libraries without following symlinks."""

    base = _existing_directory(root, code="native_output_root_invalid")
    _require(
        isinstance(outputs, Sequence)
        and not isinstance(outputs, (str, bytes))
        and 0 < len(outputs) <= _MAX_OUTPUTS,
        "native_outputs_invalid",
    )
    seen: set[str] = set()
    result: list[NativeOutput] = []
    for value in outputs:
        relative = _relative_member(value, code="native_outputs_invalid")
        name = relative.as_posix()
        _require(name not in seen, "native_outputs_invalid")
        seen.add(name)
        path = _contained_path(base, relative, code="native_output_missing")
        _require(path.is_file() and not path.is_symlink(), "native_output_invalid")
        result.append(
            NativeOutput(
                path=name,
                kind=_output_kind(path),
                size_bytes=path.stat().st_size,
                sha256=sha256_file(path),
                executable=os.access(path, os.X_OK),
            )
        )
    return tuple(result)


def cleanup_native_state(*, root: Path, paths: Sequence[Path]) -> int:
    """Idempotently remove only caller-declared temporary native state beneath root."""

    _require(
        isinstance(paths, Sequence) and not isinstance(paths, (str, bytes)),
        "native_cleanup_invalid",
    )
    try:
        return finalize_temporary_paths(tuple(Path(path) for path in paths), root=root)
    except RuntimePrimitiveError as error:
        raise NativePrimitiveError("native_cleanup_failed") from error
