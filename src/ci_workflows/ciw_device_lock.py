"""Thin command adapter for the production device-lock contract."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from .ciw_types import CIWContext, CIWError, CIWResult
from .device_lock import (
    DeviceLockError,
    PosixDeviceLockBackend,
    request_from_environment,
)

PHASES = ("acquire", "verify", "release", "residue")


def configure_device_lock(parser: argparse.ArgumentParser) -> None:
    """Register the one bounded device-lock phase selector."""

    parser.add_argument("--phase", choices=PHASES, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="device-lock")
    parser.add_argument("phase", choices=PHASES)
    return parser


def _write_outputs(values: Mapping[str, str], target: str | None) -> None:
    if target:
        path = Path(target)
        with path.open("a", encoding="utf-8") as handle:
            for key, value in values.items():
                if not key.replace("_", "").isalnum() or not key[0].isalpha():
                    raise DeviceLockError("output_rejected")
                if "\r" in value or "\n" in value:
                    raise DeviceLockError("output_rejected")
                handle.write(f"{key}={value}\n")


def execute_phase(
    phase: str,
    *,
    contract_root: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    request, _owner = request_from_environment(
        contract_root=contract_root,
        environment=environment,
    )
    backend = PosixDeviceLockBackend(
        contract_root=contract_root,
        environment=environment,
    )
    encoded_receipt = str(environment.get("CIW_LOCK_RESOURCE_RECEIPT", ""))

    if phase == "acquire":
        receipt = backend.acquire(request)
        return {
            "result": "acquired",
            "resource_lock_receipt": receipt.encode(),
            "receipt_id": receipt.receipt_id,
            "resource_key_hash": receipt.resource_key_hash,
            "owner_identity_hash": receipt.owner_identity_hash,
            "expires_at_epoch": str(receipt.expires_at_epoch),
            "release_evidence": "",
            "cleanup_evidence": "",
            "idempotent": "false",
            "failure_code": "",
        }

    if phase == "verify":
        try:
            minimum_remaining = int(
                str(environment.get("CIW_LOCK_MINIMUM_REMAINING_SECONDS", "1"))
            )
        except ValueError as error:
            raise DeviceLockError("lease_rejected") from error
        receipt = backend.verify(
            encoded_receipt,
            request,
            minimum_remaining_seconds=minimum_remaining,
        )
        return {
            "result": "verified",
            "resource_lock_receipt": encoded_receipt,
            "receipt_id": receipt.receipt_id,
            "resource_key_hash": receipt.resource_key_hash,
            "owner_identity_hash": receipt.owner_identity_hash,
            "expires_at_epoch": str(receipt.expires_at_epoch),
            "release_evidence": "",
            "cleanup_evidence": "",
            "idempotent": "false",
            "failure_code": "",
        }

    if phase == "release":
        released = backend.release(encoded_receipt, request)
        return {
            "result": "released",
            "resource_lock_receipt": "",
            "receipt_id": released.receipt_id,
            "resource_key_hash": released.resource_key_hash,
            "owner_identity_hash": released.owner_identity_hash,
            "expires_at_epoch": "",
            "release_evidence": released.release_evidence,
            "cleanup_evidence": released.cleanup_evidence,
            "idempotent": str(released.idempotent).lower(),
            "failure_code": "",
        }

    if phase == "residue":
        released = backend.assert_released(encoded_receipt, request)
        return {
            "result": "clean",
            "resource_lock_receipt": "",
            "receipt_id": released.receipt_id,
            "resource_key_hash": released.resource_key_hash,
            "owner_identity_hash": released.owner_identity_hash,
            "expires_at_epoch": "",
            "release_evidence": released.release_evidence,
            "cleanup_evidence": released.cleanup_evidence,
            "idempotent": "true",
            "failure_code": "",
        }

    raise DeviceLockError("invalid_input")


def execute_device_lock(args: argparse.Namespace, context: CIWContext) -> CIWResult:
    """Execute one registered ``ciw device lock`` phase."""

    try:
        values = execute_phase(
            args.phase,
            contract_root=context.root,
            environment=context.environment,
        )
    except DeviceLockError as error:
        raise CIWError("device", error.code) from None
    return CIWResult("device", "lock", outputs=values)


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    values = dict(os.environ) if environment is None else dict(environment)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    root = Path(__file__).resolve().parents[2]
    try:
        result = execute_phase(args.phase, contract_root=root, environment=values)
        _write_outputs(result, values.get("GITHUB_OUTPUT"))
        print(result["result"], file=out)
        return 0
    except DeviceLockError as error:
        failure = {
            "result": "failure",
            "resource_lock_receipt": "",
            "receipt_id": "",
            "resource_key_hash": "",
            "owner_identity_hash": "",
            "expires_at_epoch": "",
            "release_evidence": "",
            "cleanup_evidence": "",
            "idempotent": "false",
            "failure_code": error.code,
        }
        try:
            _write_outputs(failure, values.get("GITHUB_OUTPUT"))
        except DeviceLockError:
            pass
        print(f"device-lock:{error.code}", file=err)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
