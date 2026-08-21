#!/usr/bin/env python3
"""Materialize the fixed, checksum-verified build inputs for runner-general."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent
TOOLCHAIN = ROOT / "toolchain.lock.json"
DESTINATION = ROOT / ".ciw-build-inputs"
_CHUNK_BYTES = 1024 * 1024
_TIMEOUT_SECONDS = 60
_USER_AGENT = "StreamScapeTV-ci-workflows-runner-image/1"


class InputPreparationError(RuntimeError):
    """A bounded input preparation failure."""


@dataclass(frozen=True, slots=True)
class Asset:
    filename: str
    url: str
    sha256: str
    maximum_bytes: int


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputPreparationError("duplicate toolchain key")
        result[key] = value
    return result


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputPreparationError(f"{name} must be an object")
    return value


def _required(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InputPreparationError(f"{key} must be a non-empty canonical string")
    return value


def _asset_digest(assets: dict[str, Any], key: str) -> str:
    value = _required(assets, key)
    prefix = "sha256:"
    if not value.startswith(prefix):
        raise InputPreparationError(f"{key} must use sha256")
    digest = value[len(prefix) :]
    if len(digest) != 64:
        raise InputPreparationError(f"{key} has invalid sha256 length")
    try:
        int(digest, 16)
    except ValueError as error:
        raise InputPreparationError(f"{key} has invalid sha256") from error
    return digest.lower()


def _load_toolchain() -> dict[str, Any]:
    if TOOLCHAIN.is_symlink() or not TOOLCHAIN.is_file():
        raise InputPreparationError("toolchain lock must be a regular file")
    try:
        payload = json.loads(
            TOOLCHAIN.read_text(encoding="utf-8"),
            object_pairs_hook=_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InputPreparationError("toolchain lock is unreadable") from error
    contract = _mapping(payload, "toolchain lock")
    if contract.get("schema_version") != 1:
        raise InputPreparationError("unsupported toolchain schema")
    if contract.get("product_id") != "runner-general":
        raise InputPreparationError("unexpected toolchain product")
    if contract.get("platform") != "linux/amd64":
        raise InputPreparationError("unexpected toolchain platform")
    return contract


def _inputs(contract: dict[str, Any]) -> tuple[Asset, ...]:
    toolchain = _mapping(contract.get("toolchain"), "toolchain")
    probes = _mapping(contract.get("compatibility_probes"), "compatibility_probes")
    assets = _mapping(contract.get("external_assets"), "external_assets")

    runner = _required(toolchain, "actions_runner")
    node_compat = _required(probes, "setup_node_linux_x64")
    node_release = _required(probes, "setup_node_linux_x64_release")
    zstd = _required(toolchain, "zstd")
    cmake = _required(toolchain, "cmake")
    jq = _required(toolchain, "jq")
    yq = _required(toolchain, "yq")
    kubectl = _required(toolchain, "kubectl")
    helm = _required(toolchain, "helm")
    kustomize = _required(toolchain, "kustomize")

    return (
        Asset(
            f"actions-runner-linux-x64-{runner}.tar.gz",
            f"https://github.com/actions/runner/releases/download/v{runner}/"
            f"actions-runner-linux-x64-{runner}.tar.gz",
            _asset_digest(assets, "actions_runner"),
            268_435_456,
        ),
        Asset(
            f"node-{node_compat}-linux-x64.tar.gz",
            f"https://github.com/actions/node-versions/releases/download/"
            f"{node_compat}-{node_release}/node-{node_compat}-linux-x64.tar.gz",
            _asset_digest(assets, "node_26_compat"),
            83_886_080,
        ),
        Asset(
            f"zstd-{zstd}.tar.gz",
            f"https://github.com/facebook/zstd/releases/download/v{zstd}/"
            f"zstd-{zstd}.tar.gz",
            _asset_digest(assets, "zstd_source"),
            4_194_304,
        ),
        Asset(
            f"cmake-{cmake}-linux-x86_64.tar.gz",
            f"https://github.com/Kitware/CMake/releases/download/v{cmake}/"
            f"cmake-{cmake}-linux-x86_64.tar.gz",
            _asset_digest(assets, "cmake"),
            83_886_080,
        ),
        Asset(
            f"jq-{jq}-linux-amd64",
            f"https://github.com/jqlang/jq/releases/download/jq-{jq}/jq-linux-amd64",
            _asset_digest(assets, "jq"),
            4_194_304,
        ),
        Asset(
            f"yq-{yq}-linux-amd64",
            f"https://github.com/mikefarah/yq/releases/download/v{yq}/yq_linux_amd64",
            _asset_digest(assets, "yq"),
            20_971_520,
        ),
        Asset(
            f"kubectl-{kubectl}-linux-amd64",
            f"https://dl.k8s.io/release/v{kubectl}/bin/linux/amd64/kubectl",
            _asset_digest(assets, "kubectl"),
            104_857_600,
        ),
        Asset(
            f"helm-{helm}-linux-amd64.tar.gz",
            f"https://get.helm.sh/helm-v{helm}-linux-amd64.tar.gz",
            _asset_digest(assets, "helm"),
            67_108_864,
        ),
        Asset(
            f"kustomize-{kustomize}-linux-amd64.tar.gz",
            f"https://github.com/kubernetes-sigs/kustomize/releases/download/"
            f"kustomize%2Fv{kustomize}/kustomize_v{kustomize}_linux_amd64.tar.gz",
            _asset_digest(assets, "kustomize"),
            16_777_216,
        ),
    )


def _download(asset: Asset) -> None:
    if Path(asset.filename).name != asset.filename or asset.filename in {"", ".", ".."}:
        raise InputPreparationError("asset filename is unsafe")
    if not asset.url.startswith("https://"):
        raise InputPreparationError("asset URL must use HTTPS")
    if asset.maximum_bytes <= 0:
        raise InputPreparationError("asset size bound is invalid")

    target = DESTINATION / asset.filename
    partial = DESTINATION / f".{asset.filename}.partial"
    if target.exists() or target.is_symlink() or partial.exists() or partial.is_symlink():
        raise InputPreparationError("asset destination already exists")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(partial, flags, 0o600)
    digest = hashlib.sha256()
    total = 0
    request = urllib.request.Request(
        asset.url,
        headers={"User-Agent": _USER_AGENT},
        method="GET",
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        declared_bytes = int(declared)
                    except ValueError as error:
                        raise InputPreparationError("asset content length is invalid") from error
                    if declared_bytes <= 0 or declared_bytes > asset.maximum_bytes:
                        raise InputPreparationError("asset content length exceeds bound")
                while True:
                    block = response.read(_CHUNK_BYTES)
                    if not block:
                        break
                    total += len(block)
                    if total > asset.maximum_bytes:
                        raise InputPreparationError("asset exceeds size bound")
                    digest.update(block)
                    output.write(block)
            output.flush()
            os.fsync(output.fileno())

        if total == 0:
            raise InputPreparationError("asset is empty")
        if digest.hexdigest() != asset.sha256:
            raise InputPreparationError("asset checksum mismatch")
        os.replace(partial, target)
        target.chmod(0o600)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def prepare() -> tuple[str, ...]:
    contract = _load_toolchain()
    assets = _inputs(contract)
    filenames = tuple(asset.filename for asset in assets)
    if len(filenames) != len(set(filenames)):
        raise InputPreparationError("duplicate asset filename")
    if DESTINATION.exists() or DESTINATION.is_symlink():
        raise InputPreparationError("build input directory already exists")

    DESTINATION.mkdir(mode=0o700)
    try:
        for asset in assets:
            _download(asset)
    except BaseException:
        if DESTINATION.is_symlink():
            DESTINATION.unlink(missing_ok=True)
        elif DESTINATION.exists():
            shutil.rmtree(DESTINATION)
        raise
    return filenames


def main() -> int:
    try:
        filenames = prepare()
    except (InputPreparationError, OSError, UnicodeError, urllib.error.URLError):
        print("runner-general input preparation failed", file=sys.stderr)
        return 1
    print(json.dumps({"inputs": list(filenames), "status": "prepared"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
