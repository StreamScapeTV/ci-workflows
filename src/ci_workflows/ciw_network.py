"""Thin typed CIW adapter for verified HTTP downloads and safe extraction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .ciw_types import CIWContext, CIWError, CIWResult, input_value, project_error
from .network_primitives import NetworkPrimitiveError, download_file, extract_archive, verify_file

_DOMAIN = "network"
_OPERATIONS = ("download", "verify", "extract")


def configure_network(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation", choices=_OPERATIONS, required=True)
    parser.add_argument("--url")
    parser.add_argument("--relative-path")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-size", type=int)
    parser.add_argument("--expected-content-type")
    parser.add_argument("--maximum-bytes", type=int)
    parser.add_argument("--archive-format", choices=("zip", "tar"))
    parser.add_argument("--relative-destination")


def _value(args: argparse.Namespace, context: CIWContext, name: str, default: str = "") -> str:
    value = getattr(args, name, None)
    if value is not None:
        return str(value).strip()
    return input_value(context.environment, name, default)


def _text(value: object, code: str, *, allow_empty: bool = False, maximum: int = 8192) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > maximum
        or any(token in value for token in ("\x00", "\r", "\n"))
        or (not allow_empty and not value)
    ):
        raise CIWError(_DOMAIN, code)
    return value


def _fixed_root(context: CIWContext, name: str) -> Path:
    raw = context.environment.get(name, "")
    candidate = Path(raw)
    if not raw or not candidate.is_absolute() or candidate.is_symlink():
        raise CIWError(_DOMAIN, f"{name.lower()}_required")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CIWError(_DOMAIN, f"{name.lower()}_invalid") from error
    if not resolved.is_dir():
        raise CIWError(_DOMAIN, f"{name.lower()}_invalid")
    return resolved


def _relative_file(root: Path, raw: str, code: str) -> Path:
    text = _text(raw, code, maximum=4096)
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts or "\\" in text:
        raise CIWError(_DOMAIN, code)
    try:
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as error:
        raise CIWError(_DOMAIN, code) from error
    if not path.is_file() or path.is_symlink():
        raise CIWError(_DOMAIN, code)
    return path


def _integer(args: argparse.Namespace, context: CIWContext, name: str, default: int, maximum: int) -> int:
    value = getattr(args, name, None)
    if value is None:
        raw = _value(args, context, name, str(default))
        if not raw.isdigit():
            raise CIWError(_DOMAIN, f"{name}_invalid")
        value = int(raw)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise CIWError(_DOMAIN, f"{name}_invalid")
    return value


def _result(operation: str, *, local_path: Path, **payload: Any) -> CIWResult:
    return CIWResult(
        _DOMAIN,
        "run",
        outputs={
            "result": "success",
            "local_path": str(local_path),
            "network_result_json": json.dumps(
                {"operation": operation, **payload},
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    )


def execute_network(args: argparse.Namespace, context: CIWContext) -> CIWResult:
    dependencies = _fixed_root(context, "CI_DEPENDENCY_ROOT")
    operation = args.operation
    try:
        if operation == "download":
            url = _text(_value(args, context, "url"), "url_required")
            relative = _text(
                _value(args, context, "relative_path"),
                "relative_path_required",
                maximum=4096,
            )
            expected_sha256 = _value(args, context, "expected_sha256") or None
            expected_size = args.expected_size
            if expected_size is None:
                raw_size = _value(args, context, "expected_size")
                expected_size = int(raw_size) if raw_size.isdigit() else None
                if raw_size and expected_size is None:
                    raise CIWError(_DOMAIN, "expected_size_invalid")
            maximum = _integer(
                args,
                context,
                "maximum_bytes",
                512 * 1024 * 1024,
                8 * 1024 * 1024 * 1024,
            )
            result = download_file(
                url,
                destination_root=dependencies,
                relative_path=relative,
                environment=dict(context.environment),
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                expected_content_type=_value(args, context, "expected_content_type") or None,
                maximum_bytes=maximum,
            )
            return _result(
                "download",
                local_path=dependencies / result.relative_path,
                relative_path=result.relative_path,
                size=result.size,
                sha256=result.sha256,
                status=result.status,
                attempts=result.attempts,
                redirects=result.redirects,
                content_type=result.content_type,
            )
        if operation == "verify":
            path = _relative_file(
                dependencies,
                _value(args, context, "relative_path"),
                "relative_path_invalid",
            )
            expected_size = args.expected_size
            if expected_size is None:
                raw_size = _value(args, context, "expected_size")
                expected_size = int(raw_size) if raw_size.isdigit() else None
                if raw_size and expected_size is None:
                    raise CIWError(_DOMAIN, "expected_size_invalid")
            result = verify_file(
                path,
                expected_sha256=_value(args, context, "expected_sha256") or None,
                expected_size=expected_size,
            )
            return _result(
                "verify",
                local_path=path,
                relative_path=path.relative_to(dependencies).as_posix(),
                size=result.size,
                sha256=result.sha256,
            )
        if operation == "extract":
            archive = _relative_file(
                dependencies,
                _value(args, context, "relative_path"),
                "relative_path_invalid",
            )
            generated = _fixed_root(context, "CI_GENERATED_ROOT")
            result = extract_archive(
                archive,
                archive_format=_text(
                    _value(args, context, "archive_format"),
                    "archive_format_required",
                ),
                destination_root=generated,
                relative_destination=_text(
                    _value(args, context, "relative_destination"),
                    "relative_destination_required",
                    maximum=4096,
                ),
            )
            return _result(
                "extract",
                local_path=generated / result.destination,
                destination=result.destination,
                file_count=result.file_count,
                directory_count=result.directory_count,
                total_bytes=result.total_bytes,
                archive_sha256=result.archive_sha256,
                manifest_sha256=result.manifest_sha256,
            )
        raise CIWError(_DOMAIN, "operation_invalid")
    except (CIWError, NetworkPrimitiveError) as error:
        raise project_error(error, domain=_DOMAIN) from error
