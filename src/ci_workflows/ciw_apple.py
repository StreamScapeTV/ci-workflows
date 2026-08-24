"""Bounded ``ciw apple validate`` adapter and standalone compatibility CLI."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from . import apple as apple_validation
from . import apple_execution
from . import apple_multistage
from . import apple_plan_guard
from .apple_contract_fragments import load_apple_contract
from .apple_simulator_script import SimulatorLeaseArgumentRunner

try:
    from . import runners
    from .ciw_types import CIWContext, CIWResult
    from .workspace import resolve_state_root
except ImportError:  # pragma: no cover - standalone fixture use
    CIWContext = object  # type: ignore[assignment,misc]
    CIWResult = object  # type: ignore[assignment,misc]


_DIAGNOSTIC_MAX_LINES = 80
_DIAGNOSTIC_MAX_CHARS = 12 * 1024
_DIAGNOSTIC_COMMANDS = {"bash", "python3", "swift", "xcodebuild"}
_XCRESULT_MAX_BYTES = 256 * 1024
_XCRESULT_MAX_ERRORS = 128
_XCRESULT_TIMEOUT_SECONDS = 15
_URL = re.compile(
    r"(?i)(?:(?:https?|ssh|file)://[^\s]+|git@[^\s:]+:[^\s]+)"
)
_AUTHORIZATION = re.compile(r"(?im)^(\s*authorization)\s*[:=].*$")
_ENV_ASSIGNMENT = re.compile(r"(?m)^([A-Z][A-Z0-9_]{1,63})=.*$")
_TOKEN = re.compile(
    r"(?i)\b(?:github_pat_[A-Za-z0-9_]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"bearer\s+[A-Za-z0-9._~+/=-]{8,})\b"
)
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
_CONCRETE_ERROR = re.compile(
    r"(?im)^.*(?<!:)\b(?:fatal\s+)?error:\s*(?!:)\S.*$"
)


def _stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _bounded_failure_diagnostic(text: str, roots: Sequence[Path]) -> str:
    """Return one bounded fail-closed diagnostic safe for ordinary job logs."""

    sanitized = apple_execution.sanitize(text, roots)
    sanitized = _URL.sub("<url>", sanitized)
    sanitized = _AUTHORIZATION.sub(r"\1=<redacted>", sanitized)
    sanitized = _ENV_ASSIGNMENT.sub(r"\1=<redacted>", sanitized)
    sanitized = _TOKEN.sub("<redacted>", sanitized)
    sanitized = _JWT.sub("<redacted>", sanitized)
    bounded = "\n".join(sanitized.splitlines()[-_DIAGNOSTIC_MAX_LINES:]).strip()
    if len(bounded) > _DIAGNOSTIC_MAX_CHARS:
        bounded = bounded[-_DIAGNOSTIC_MAX_CHARS:]
        if "\n" in bounded:
            bounded = bounded.split("\n", 1)[1]
    return bounded


def _has_concrete_error(text: str) -> bool:
    """Return whether ordinary command output already carries an actionable error."""

    return _CONCRETE_ERROR.search(text) is not None


def _owned_result_bundle(argv: Sequence[str], state_root: Path | None) -> Path | None:
    """Return the exact Central-owned result bundle selected by xcodebuild."""

    if not argv or argv[0] != "xcodebuild" or state_root is None:
        return None
    positions = [index for index, value in enumerate(argv) if value == "-resultBundlePath"]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        return None
    candidate = Path(argv[positions[0] + 1])
    if not candidate.is_absolute() or candidate.suffix != ".xcresult":
        return None
    expected_root = state_root / "apple-validation" / "result-bundles"
    try:
        if expected_root.is_symlink() or not expected_root.is_dir():
            return None
        root = expected_root.resolve(strict=True)
        if candidate.is_symlink() or not candidate.is_dir():
            return None
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if root not in resolved.parents:
        return None
    return resolved


def _read_xcresult_build_results(bundle: Path) -> str:
    """Read bounded Xcode build-result JSON from one already-owned result bundle."""

    try:
        completed = subprocess.run(
            (
                "xcrun",
                "xcresulttool",
                "get",
                "build-results",
                "--path",
                str(bundle),
                "--format",
                "json",
            ),
            cwd=bundle.parent,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_XCRESULT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    output = completed.stdout or ""
    if completed.returncode != 0 or len(output.encode("utf-8")) > _XCRESULT_MAX_BYTES:
        return ""
    return output


def _xcresult_source_location(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme != "file":
        return ""
    name = Path(unquote(parsed.path)).name
    if (
        not name
        or len(name) > 255
        or any(character in name for character in ("\x00", "\n", "\r"))
    ):
        return ""
    fragment = parse_qs(parsed.fragment, keep_blank_values=False)
    location = name
    line = fragment.get("StartingLineNumber", [""])[0]
    column = fragment.get("StartingColumnNumber", [""])[0]
    if isinstance(line, str) and line.isdigit() and 1 <= int(line) <= 10_000_000:
        location += f":{int(line)}"
        if isinstance(column, str) and column.isdigit() and 1 <= int(column) <= 100_000:
            location += f":{int(column)}"
    return location


def _xcresult_compiler_diagnostic(raw: str, roots: Sequence[Path]) -> str:
    """Project one deterministic compiler error from bounded xcresult JSON."""

    if not raw or len(raw.encode("utf-8")) > _XCRESULT_MAX_BYTES:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list) or len(errors) > _XCRESULT_MAX_ERRORS:
        return ""
    for row in errors:
        if not isinstance(row, dict):
            continue
        issue_type = row.get("issueType")
        message = row.get("message")
        if (
            not isinstance(issue_type, str)
            or not isinstance(message, str)
            or not issue_type.strip()
            or not message.strip()
            or len(issue_type) > 256
            or len(message) > 8192
        ):
            continue
        kind = issue_type.casefold()
        if not any(token in kind for token in ("compiler", "swift", "clang")):
            continue
        location = _xcresult_source_location(row.get("sourceURL"))
        prefix = issue_type.strip()
        projected = f"{prefix}: {message.strip()}"
        if location:
            projected = f"{prefix}: {location}: {message.strip()}"
        return _bounded_failure_diagnostic(projected, roots)
    return ""


class _FailureDiagnosticRunner:
    """Emit only failing command diagnostics before Apple cleanup removes state."""

    def __init__(
        self,
        delegate: apple_execution.CommandRunner,
        roots: Sequence[Path],
        *,
        state_root: Path | None = None,
    ) -> None:
        self._delegate = delegate
        self._roots = tuple(dict.fromkeys(Path(root) for root in roots))
        self._state_root = Path(state_root) if state_root is not None else None

    @staticmethod
    def _eligible(argv: Sequence[str]) -> bool:
        return bool(argv) and argv[0] in _DIAGNOSTIC_COMMANDS

    def _emit(self, summary: str, text: str, cwd: Path) -> None:
        print(f"CIW Apple command failure: {summary}.", file=sys.stderr)
        diagnostic = _bounded_failure_diagnostic(
            text,
            (*self._roots, cwd),
        )
        if not diagnostic:
            return
        print("CIW Apple bounded sanitized diagnostic:", file=sys.stderr)
        # Prefix every payload line so compiler output cannot become a GitHub
        # workflow command such as ::error:: or ::add-mask::.
        for line in diagnostic.splitlines():
            print(f"| {line}", file=sys.stderr)

    def _failure_text(
        self,
        argv: Sequence[str],
        ordinary: str,
        cwd: Path,
    ) -> str:
        if argv[0] != "xcodebuild" or _has_concrete_error(ordinary):
            return ordinary
        bundle = _owned_result_bundle(argv, self._state_root)
        if bundle is None:
            return ordinary
        raw = _read_xcresult_build_results(bundle)
        diagnostic = _xcresult_compiler_diagnostic(
            raw,
            (*self._roots, cwd, bundle.parent),
        )
        return diagnostic or ordinary

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> apple_execution.CommandOutcome:
        eligible = self._eligible(argv)
        try:
            outcome = self._delegate.run(
                argv,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            if eligible:
                self._emit(
                    f"timed out after {timeout_seconds} seconds",
                    _stream_text(error.stdout) + _stream_text(error.stderr),
                    cwd,
                )
            raise
        except apple_validation.AppleValidationError as error:
            if eligible and error.code == "command_failed":
                self._emit("could not be launched", "", cwd)
            raise
        except OSError:
            if eligible:
                self._emit("could not be launched", "", cwd)
            raise
        if eligible and outcome.returncode != 0:
            ordinary = outcome.stdout + outcome.stderr
            self._emit(
                f"exited with status {outcome.returncode}",
                self._failure_text(argv, ordinary, cwd),
                cwd,
            )
        return outcome


def _diagnostic_runner(
    source: Path,
    state: Path,
) -> _FailureDiagnosticRunner:
    return _FailureDiagnosticRunner(
        SimulatorLeaseArgumentRunner(),
        (source, state, state.parent),
        state_root=state,
    )


def configure_apple_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--phase",
        choices=("plan", "execute", "cleanup", "residue"),
        default="execute",
    )
    parser.add_argument("--source-root", default="source")


def _workflow_state_root(root: Path, environment: Mapping[str, str]) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", root / ".validation-state"))
    declared = environment.get(
        "CI_WORKFLOW_ROOT",
        str(runner_temp / "ciw-apple"),
    )
    state_id = environment.get("CI_WORKFLOW_STATE_ID", "apple-validation")
    try:
        resolver = resolve_state_root  # type: ignore[name-defined]
    except NameError:
        path = Path(declared).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return resolver(
        runner_temp=runner_temp,
        state_id=state_id,
        declared_root=declared,
        contract_root=root,
    )


def _resolved_state_root(root: Path, environment: Mapping[str, str]) -> Path:
    path = _workflow_state_root(root, environment)
    temporary = path / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    if not temporary.is_dir() or temporary.is_symlink():
        raise apple_validation.AppleValidationError("invalid_input")
    return temporary


def _standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "command",
        choices=("plan", "execute", "cleanup", "residue"),
    )
    parser.add_argument("--source-root", default="source")
    parser.add_argument("--output")
    return parser


def _write_outputs(values: Mapping[str, str], target: str | None) -> None:
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            for key, value in sorted(values.items()):
                handle.write(f"{key}={value}\n")
    print(json.dumps(dict(values), sort_keys=True, separators=(",", ":")))


def _planning_outputs(
    root: Path,
    plan: apple_validation.AppleValidationPlan,
    request: apple_validation.AppleValidationRequest,
) -> dict[str, str]:
    try:
        resolved = runners.resolve_runner_profile(
            runners.load_runner_contract(root),
            workflow_api="validation.apple",
            source_trust=request.source_trust,
            requested_profile=plan.runner_profile.value,
        )
    except runners.RunnerContractError as error:
        raise apple_validation.AppleValidationError("runner_rejected") from error
    outputs = plan.planning_outputs()
    outputs["runs_on_json"] = resolved.as_dict()["runs_on_json"]
    return outputs


def _protected_planning_outputs(
    root: Path,
    plan: apple_multistage.ProtectedApplePlan,
) -> dict[str, str]:
    try:
        resolved = runners.resolve_runner_profile(
            runners.load_runner_contract(root),
            workflow_api="validation.apple",
            source_trust=plan.source_trust,
            requested_profile="apple",
        )
    except runners.RunnerContractError as error:
        raise apple_validation.AppleValidationError("runner_rejected") from error
    outputs = plan.planning_outputs()
    outputs["runs_on_json"] = resolved.as_dict()["runs_on_json"]
    return outputs


def _source_path(
    root: Path,
    source_root: str,
    environment: Mapping[str, str],
) -> Path:
    workspace = Path(environment.get("GITHUB_WORKSPACE", root)).resolve()
    relative = apple_validation.safe_relative(source_root)
    return apple_validation.bounded_path(workspace, relative)


def _run_plan(
    *,
    plan: apple_validation.AppleValidationPlan,
    source: Path | None,
    state: Path | None,
    environment: Mapping[str, str],
) -> apple_validation.AppleValidationPlan | apple_validation.AppleValidationResult:
    if source is None or state is None:
        return plan
    return apple_validation.execute_apple_plan(
        plan=plan,
        source_root=source,
        state_root=state,
        runner=_diagnostic_runner(source, state),
        environment=environment,
    )


def _source_trust(environment: Mapping[str, str]) -> str:
    explicit = environment.get("INPUT_SOURCE_TRUST", "").strip()
    if explicit:
        return explicit
    if environment.get("GITHUB_EVENT_NAME"):
        return apple_validation.source_trust_from_environment(environment)
    return "trusted-pr"


def _protected_plan(
    context: "CIWContext",
    contract: Mapping[str, object],
) -> apple_multistage.ProtectedApplePlan:
    environment = context.environment
    raw_plan = environment.get("INPUT_VALIDATION_PLAN_JSON", "")
    apple_plan_guard.validate_protected_full_plan_json(raw_plan)
    return apple_multistage.build_protected_full_plan(
        raw_plan,
        repository=environment.get("GITHUB_REPOSITORY", ""),
        admitted_sha=environment.get("INPUT_ADMITTED_SHA", ""),
        source_trust=_source_trust(environment),
        contract=contract,
        private_dependency_repository=environment.get(
            "INPUT_PRIVATE_DEPENDENCY_REPOSITORY",
            "",
        ),
        private_dependency_sha=environment.get("INPUT_PRIVATE_DEPENDENCY_SHA", ""),
        private_dependency_subdirectory=environment.get(
            "INPUT_PRIVATE_DEPENDENCY_SUBDIRECTORY",
            ".",
        ),
        private_dependency_id=environment.get("INPUT_PRIVATE_DEPENDENCY_ID", ""),
    )


def _private_dependency_execution_environment(
    environment: Mapping[str, str],
    plan: apple_multistage.ProtectedApplePlan,
    dependency: Path | None,
) -> dict[str, str]:
    """Bind one verified private repository URL to its credential-free local checkout."""
    result = dict(environment)
    if dependency is None:
        return result
    if not plan.private_dependency_used or not dependency.is_absolute() or dependency.is_symlink():
        raise apple_validation.AppleValidationError("private_dependency_path_invalid")

    repository_url = f"https://github.com/{plan.private_dependency_repository}"
    local_url = dependency.as_uri()
    rewrite_key = f"url.{local_url}.insteadOf"
    result.update(
        CI_PRIVATE_DEPENDENCY_PATH=str(dependency),
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0=rewrite_key,
        GIT_CONFIG_VALUE_0=f"{repository_url}.git",
        GIT_CONFIG_KEY_1=rewrite_key,
        GIT_CONFIG_VALUE_1=repository_url,
        GIT_TERMINAL_PROMPT="0",
    )
    return result


def _execute_protected_apple_validate(
    args: argparse.Namespace,
    context: "CIWContext",
    contract: Mapping[str, object],
) -> "CIWResult":
    plan = _protected_plan(context, contract)
    if args.phase == "plan":
        return CIWResult(
            "apple",
            "validate",
            outputs=_protected_planning_outputs(context.root, plan),
        )

    source = _source_path(context.root, args.source_root, context.environment)
    state = _resolved_state_root(context.root, context.environment)
    runner = _diagnostic_runner(source, state)
    if args.phase == "cleanup":
        apple_multistage.cleanup_protected_full(
            plan,
            source_root=source,
            state_root=state,
            runner=runner,
            environment=context.environment,
        )
        return CIWResult(
            "apple",
            "validate",
            outputs={"cleanup_result": "success", "failure_code": ""},
        )
    if args.phase == "residue":
        apple_multistage.assert_zero_protected_full_residue(
            plan,
            source_root=source,
            state_root=state,
            runner=runner,
            environment=context.environment,
        )
        return CIWResult(
            "apple",
            "validate",
            outputs={"cleanup_result": "success", "failure_code": ""},
        )

    workflow_state = _workflow_state_root(context.root, context.environment)
    dependency = apple_multistage.verify_private_dependency(
        plan,
        workflow_state_root=workflow_state,
        environment=context.environment,
    )
    execution_environment = _private_dependency_execution_environment(
        context.environment,
        plan,
        dependency,
    )
    outputs = apple_multistage.execute_protected_full(
        plan,
        source_root=source,
        state_root=state,
        runner=runner,
        environment=execution_environment,
    )
    return CIWResult("apple", "validate", outputs=outputs)


def standalone_main(argv: Sequence[str] | None = None) -> int:
    args = _standalone_parser().parse_args(argv)
    root = args.root.resolve()
    output = args.output or os.environ.get("GITHUB_OUTPUT")
    try:
        contract = load_apple_contract(root)
        request = apple_validation.request_from_environment(os.environ, contract)
        plan = apple_validation.resolve_plan(contract, request)
        source = _source_path(root, args.source_root, os.environ)
        state = None if args.command == "plan" else _resolved_state_root(root, os.environ)
        if args.command == "cleanup":
            assert state is not None
            apple_validation.cleanup_apple_state(source, state, plan)
            values = {"cleanup_result": "success", "failure_code": ""}
        elif args.command == "residue":
            assert state is not None
            apple_validation.assert_zero_apple_residue(source, state, plan)
            values = {"cleanup_result": "success", "failure_code": ""}
        else:
            result = _run_plan(
                plan=plan,
                source=None if args.command == "plan" else source,
                state=state,
                environment=os.environ,
            )
            values = (
                _planning_outputs(root, result, request)
                if isinstance(result, apple_validation.AppleValidationPlan)
                else result.output_values()
            )
        _write_outputs(values, output)
        return 0
    except apple_validation.AppleValidationError as error:
        _write_outputs(
            {
                "result": "failure",
                "cleanup_result": (
                    "failure" if error.cleanup_failed else "not-run"
                ),
                "failure_code": error.code,
            },
            output,
        )
        return 1


def execute_apple_validate(
    args: argparse.Namespace,
    context: "CIWContext",
) -> "CIWResult":
    contract = load_apple_contract(context.root)
    scope = context.environment.get("INPUT_VALIDATION_SCOPE", "legacy").strip() or "legacy"
    if scope == "protected-full":
        return _execute_protected_apple_validate(args, context, contract)
    if scope != "legacy":
        raise apple_validation.AppleValidationError("unsupported_profile")

    request = apple_validation.request_from_environment(
        context.environment,
        contract,
    )
    plan = apple_validation.resolve_plan(contract, request)
    source = _source_path(context.root, args.source_root, context.environment)
    state = (
        None
        if args.phase == "plan"
        else _resolved_state_root(context.root, context.environment)
    )
    if args.phase == "cleanup":
        assert state is not None
        apple_validation.cleanup_apple_state(source, state, plan)
        return CIWResult(
            "apple",
            "validate",
            outputs={"cleanup_result": "success", "failure_code": ""},
        )
    if args.phase == "residue":
        assert state is not None
        apple_validation.assert_zero_apple_residue(source, state, plan)
        return CIWResult(
            "apple",
            "validate",
            outputs={"cleanup_result": "success", "failure_code": ""},
        )
    result = _run_plan(
        plan=plan,
        source=None if args.phase == "plan" else source,
        state=state,
        environment=context.environment,
    )
    if isinstance(result, apple_validation.AppleValidationPlan):
        return CIWResult(
            "apple",
            "validate",
            outputs=_planning_outputs(context.root, result, request),
        )
    return CIWResult("apple", "validate", outputs=result.output_values())


def main(argv: Sequence[str] | None = None) -> int:
    return standalone_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
