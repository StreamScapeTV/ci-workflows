"""Recursive deterministic Helm chart archive canonicalization."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .helm_contract import require
from .helm_types import HelmValidationError, HelmValidationResult


_MAX_DEPTH = 8
_MAX_MEMBERS = 2_048
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_EXPANDED_BYTES = 128 * 1024 * 1024
_JUNK = {".git", ".ds_store", "__pycache__", ".env", ".npmrc"}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kubeconfig"}
_SECRET = re.compile(
    r"(?i)(authorization|password|secret|token)\s*[:=]\s*[^\s${}]+"
)
_TOKEN = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----)"
)


def _member_name(name: str, root_name: str) -> str:
    candidate = name[2:] if name.startswith("./") else name
    path = PurePosixPath(candidate)
    parts = path.parts
    require(
        bool(parts)
        and not path.is_absolute()
        and parts[0] == root_name
        and all(part not in {"", ".", ".."} for part in parts),
        "archive_invalid",
    )
    require(
        not any(part.casefold() in _JUNK for part in parts),
        "archive_invalid",
    )
    require(
        Path(parts[-1]).suffix.casefold() not in _SENSITIVE_SUFFIXES,
        "archive_invalid",
    )
    return "/".join(parts)


def _infer_root(members: list[tarfile.TarInfo]) -> str:
    roots: set[str] = set()
    for member in members:
        candidate = member.name[2:] if member.name.startswith("./") else member.name
        path = PurePosixPath(candidate)
        require(
            bool(path.parts)
            and not path.is_absolute()
            and all(part not in {"", ".", ".."} for part in path.parts),
            "archive_invalid",
        )
        roots.add(path.parts[0])
    require(len(roots) == 1, "archive_invalid")
    root_name = next(iter(roots))
    require(
        bool(root_name)
        and len(root_name) <= 128
        and root_name not in _JUNK,
        "archive_invalid",
    )
    return root_name


def _scan_text(content: bytes) -> None:
    decoded = content.decode("utf-8", errors="replace")
    if _TOKEN.search(decoded) or _SECRET.search(decoded):
        raise HelmValidationError("archive_secret_detected")


def _write_archive(members: list[tuple[str, bool, bytes]]) -> bytes:
    raw = io.BytesIO()
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
    return raw.getvalue()


def canonicalize_chart_archive_bytes(
    content: bytes,
    *,
    expected_root: str | None = None,
    depth: int = 0,
    _budget: dict[str, int] | None = None,
) -> bytes:
    """Canonicalize an outer chart and nested subcharts under one total budget."""

    require(depth <= _MAX_DEPTH, "archive_invalid")
    require(0 < len(content) <= _MAX_EXPANDED_BYTES, "archive_invalid")
    budget = _budget if _budget is not None else {"members": 0, "expanded": 0}
    require(set(budget) == {"members", "expanded"}, "archive_invalid")
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            raw_members = archive.getmembers()
            require(bool(raw_members), "archive_invalid")
            budget["members"] += len(raw_members)
            require(budget["members"] <= _MAX_MEMBERS, "archive_invalid")
            root_name = expected_root or _infer_root(raw_members)
            members: list[tuple[str, bool, bytes]] = []
            seen: set[str] = set()
            chart_yaml = f"{root_name}/Chart.yaml"
            for member in raw_members:
                name = _member_name(member.name, root_name)
                require(name not in seen, "archive_invalid")
                seen.add(name)
                require(member.isfile() or member.isdir(), "archive_invalid")
                if member.isdir():
                    members.append((name.rstrip("/") + "/", True, b""))
                    continue
                extracted = archive.extractfile(member)
                require(extracted is not None, "archive_invalid")
                data = extracted.read(_MAX_FILE_BYTES + 1)
                require(len(data) <= _MAX_FILE_BYTES, "archive_invalid")
                budget["expanded"] += len(data)
                require(budget["expanded"] <= _MAX_EXPANDED_BYTES, "archive_invalid")
                relative = PurePosixPath(name).parts[1:]
                is_packaged_subchart = (
                    len(relative) >= 2
                    and relative[0] == "charts"
                    and relative[-1].endswith(".tgz")
                )
                if is_packaged_subchart:
                    data = canonicalize_chart_archive_bytes(
                        data,
                        expected_root=None,
                        depth=depth + 1,
                        _budget=budget,
                    )
                else:
                    _scan_text(data)
                members.append((name, False, data))
    except (OSError, tarfile.TarError) as error:
        raise HelmValidationError("archive_invalid") from error

    require(chart_yaml in seen, "archive_invalid")
    return _write_archive(members)


def canonicalize_chart_archive(source: Path, destination: Path, chart_name: str) -> str:
    require(source.is_file() and not source.is_symlink(), "archive_invalid")
    try:
        content = source.read_bytes()
        canonical = canonicalize_chart_archive_bytes(content, expected_root=chart_name)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.write_bytes(canonical)
    except OSError as error:
        raise HelmValidationError("archive_invalid") from error
    return hashlib.sha256(canonical).hexdigest()


def finalize_validation_archive(
    validation: HelmValidationResult,
    chart_name: str,
) -> HelmValidationResult:
    """Replace one preliminary package with its recursively canonical package."""

    source = validation.archive_path
    destination = source.with_name("canonical.tgz")
    require(destination != source, "archive_invalid")
    package_sha256 = canonicalize_chart_archive(source, destination, chart_name)
    try:
        source.unlink()
    except OSError as error:
        raise HelmValidationError("archive_invalid") from error

    try:
        summary: Any = json.loads(validation.summary)
    except json.JSONDecodeError as error:
        raise HelmValidationError("archive_invalid") from error
    require(isinstance(summary, Mapping), "archive_invalid")
    summary = dict(summary)
    summary["package_sha256"] = package_sha256
    return HelmValidationResult(
        chart_digest=f"sha256:{package_sha256}",
        package_sha256=package_sha256,
        summary=json.dumps(summary, sort_keys=True, separators=(",", ":")),
        archive_path=destination,
    )


__all__ = [
    "canonicalize_chart_archive",
    "canonicalize_chart_archive_bytes",
    "finalize_validation_archive",
]
