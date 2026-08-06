"""Pinned tool, version, checksum, digest, and locked-asset verification."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .foundation_types import (
    FoundationError,
    atomic_write_text,
    bounded_int,
    load_contract,
    require,
    safe_id,
    safe_relative_path,
    sha256_hex,
    stable_identifier,
)

TOOL_CONTRACT = "contracts/tool-lock.json"


@dataclass(frozen=True)
class ToolEvidence:
    tool_id: str
    executable: str
    version: str


@dataclass(frozen=True)
class ToolSetEvidence:
    tool_set: str
    tools: tuple[ToolEvidence, ...]
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        from .foundation_types import canonical_json

        values = {item.tool_id: item.version for item in self.tools}
        return {
            "tool_set": self.tool_set,
            "toolchain_json": canonical_json(values),
            "toolchain_id": self.evidence_id,
            "verified": "true",
        }


@dataclass(frozen=True)
class InstalledAsset:
    asset_id: str
    filename: str
    sha256: str
    relative_path: str

    def output_values(self) -> dict[str, str]:
        return {
            "asset_id": self.asset_id,
            "asset_filename": self.filename,
            "asset_sha256": self.sha256,
            "asset_relative_path": self.relative_path,
            "verified": "true",
        }


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError as error:
        raise FoundationError("tool_version_invalid") from error


def _run_version(executable: str, arguments: Sequence[str]) -> str:
    path = shutil.which(executable)
    require(path is not None, "required_tool_missing")
    try:
        completed = subprocess.run(
            [path, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except OSError as error:
        raise FoundationError("tool_version_unavailable") from error
    require(completed.returncode == 0, "tool_version_unavailable")
    return completed.stdout.strip()


def verify_tool(tool_id: str, *, contract_root: Path) -> ToolEvidence:
    contract = load_contract(contract_root, TOOL_CONTRACT)
    tool_id = safe_id(tool_id, "unknown_tool_id")
    tools = contract.get("tools")
    require(isinstance(tools, dict) and tool_id in tools, "unknown_tool_id")
    entry = tools[tool_id]
    require(isinstance(entry, dict), "tool_contract_invalid")
    executable = entry.get("executable")
    version_args = entry.get("version_args")
    version_pattern = entry.get("version_pattern")
    minimum_version = entry.get("minimum_version")
    require(
        isinstance(executable, str)
        and isinstance(version_args, list)
        and all(isinstance(value, str) for value in version_args)
        and isinstance(version_pattern, str)
        and isinstance(minimum_version, str),
        "tool_contract_invalid",
    )
    output = _run_version(executable, version_args)
    match = re.search(version_pattern, output)
    require(match is not None and match.lastindex == 1, "tool_version_unrecognized")
    version = match.group(1)
    require(_version_tuple(version) >= _version_tuple(minimum_version), "tool_version_too_old")
    return ToolEvidence(tool_id=tool_id, executable=executable, version=version)


def verify_tool_set(tool_set: str, *, contract_root: Path) -> ToolSetEvidence:
    contract = load_contract(contract_root, TOOL_CONTRACT)
    tool_set = safe_id(tool_set, "unknown_tool_set")
    tool_sets = contract.get("tool_sets")
    require(isinstance(tool_sets, dict) and tool_set in tool_sets, "unknown_tool_set")
    identifiers = tool_sets[tool_set]
    require(
        isinstance(identifiers, list)
        and identifiers
        and all(isinstance(value, str) for value in identifiers),
        "tool_contract_invalid",
    )
    evidence = tuple(verify_tool(identifier, contract_root=contract_root) for identifier in identifiers)
    evidence_id = stable_identifier(
        "toolchain",
        {item.tool_id: item.version for item in evidence},
    )
    return ToolSetEvidence(tool_set=tool_set, tools=evidence, evidence_id=evidence_id)


def checksum_file(path: Path, algorithm: str = "sha256") -> str:
    require(path.is_file() and not path.is_symlink(), "checksum_file_unavailable")
    require(algorithm in hashlib.algorithms_guaranteed, "unsupported_checksum_algorithm")
    digest = hashlib.new(algorithm)
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise FoundationError("checksum_file_unavailable") from error
    return digest.hexdigest()


def verify_checksum(path: Path, *, algorithm: str, expected: str) -> str:
    require(isinstance(expected, str) and expected, "checksum_required")
    actual = checksum_file(path, algorithm)
    require(actual == expected.lower(), "checksum_mismatch")
    return actual


def verify_digest(path: Path, expected_digest: str) -> str:
    require(isinstance(expected_digest, str) and ":" in expected_digest, "digest_required")
    algorithm, expected = expected_digest.split(":", 1)
    require(algorithm == "sha256", "unsupported_digest_algorithm")
    expected = sha256_hex(expected, "digest_required")
    actual = verify_checksum(path, algorithm=algorithm, expected=expected)
    return f"{algorithm}:{actual}"


def _asset_entry(contract: Mapping[str, Any], asset_id: str) -> Mapping[str, Any]:
    assets = contract.get("assets")
    require(isinstance(assets, dict) and asset_id in assets, "unknown_locked_asset")
    entry = assets[asset_id]
    require(isinstance(entry, dict), "tool_contract_invalid")
    return entry


def install_locked_asset(
    asset_id: str,
    *,
    destination_root: Path,
    contract_root: Path,
) -> InstalledAsset:
    """Download one contract-selected immutable asset and verify its digest."""

    contract = load_contract(contract_root, TOOL_CONTRACT)
    asset_id = safe_id(asset_id, "unknown_locked_asset")
    entry = _asset_entry(contract, asset_id)
    policy = contract.get("download_policy")
    require(isinstance(policy, dict), "tool_contract_invalid")
    url = entry.get("url")
    filename = entry.get("filename")
    expected = entry.get("sha256")
    require(isinstance(url, str) and isinstance(filename, str) and isinstance(expected, str), "tool_contract_invalid")
    filename = safe_relative_path(filename, "invalid_locked_asset_filename")
    require("/" not in filename, "invalid_locked_asset_filename")
    expected = sha256_hex(expected, "tool_contract_invalid")
    parsed = urllib.parse.urlparse(url)
    require(parsed.scheme == policy.get("allowed_scheme") and bool(parsed.hostname), "locked_asset_url_forbidden")
    maximum = bounded_int(
        policy.get("maximum_bytes"),
        minimum=1,
        maximum=1024 * 1024 * 1024,
        instruction="tool_contract_invalid",
    )
    destination_root = destination_root.resolve()
    require(destination_root.is_dir() and not destination_root.is_symlink(), "tool_destination_unavailable")
    target = destination_root / filename
    require(not target.exists(), "locked_asset_already_exists")
    temporary = destination_root / f".{filename}.partial"
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            final = urllib.parse.urlparse(response.geturl())
            if policy.get("redirect_host_must_match"):
                require(final.scheme == parsed.scheme and final.hostname == parsed.hostname, "locked_asset_redirect_forbidden")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                require(size <= maximum, "locked_asset_too_large")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest()
        require(actual == expected, "checksum_mismatch")
        os.chmod(temporary, 0o700)
        temporary.replace(target)
    except FoundationError:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as error:
        temporary.unlink(missing_ok=True)
        raise FoundationError("locked_asset_download_failed") from error
    return InstalledAsset(
        asset_id=asset_id,
        filename=filename,
        sha256=actual,
        relative_path=filename,
    )
