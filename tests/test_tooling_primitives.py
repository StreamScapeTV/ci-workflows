from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.foundation_types import FoundationError
from ci_workflows.tooling import (
    install_locked_asset,
    verify_checksum,
    verify_digest,
    verify_tool_set,
)

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, content: bytes, url: str) -> None:
        self.content = content
        self.url = url
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.content):
            return b""
        if size < 0:
            size = len(self.content) - self.offset
        chunk = self.content[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class ToolingPrimitiveTests(unittest.TestCase):
    def test_baseline_tool_set_is_contract_selected_and_versioned(self) -> None:
        evidence = verify_tool_set("baseline", contract_root=ROOT)
        versions = {item.tool_id: item.version for item in evidence.tools}
        self.assertEqual(set(versions), {"python", "git", "bash"})
        self.assertTrue(evidence.evidence_id.startswith("toolchain-"))
        self.assertEqual(evidence.output_values()["verified"], "true")

    def test_checksum_and_digest_verification_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset"
            path.write_bytes(b"locked content\n")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(
                verify_checksum(path, algorithm="sha256", expected=expected),
                expected,
            )
            self.assertEqual(
                verify_digest(path, f"sha256:{expected}"),
                f"sha256:{expected}",
            )
            with self.assertRaises(FoundationError) as caught:
                verify_checksum(path, algorithm="sha256", expected="0" * 64)
            self.assertEqual(caught.exception.instruction, "checksum_mismatch")
            with self.assertRaises(FoundationError) as caught:
                verify_digest(path, f"sha512:{'0' * 128}")
            self.assertEqual(caught.exception.instruction, "unsupported_digest_algorithm")

    def contract_root(self, base: Path, *, url: str, content: bytes) -> Path:
        contracts = base / "contracts"
        contracts.mkdir()
        payload = json.loads((ROOT / "contracts/tool-lock.json").read_text())
        payload["assets"] = {
            "fixture-tool": {
                "url": url,
                "filename": "fixture-tool",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        }
        (contracts / "tool-lock.json").write_text(json.dumps(payload), encoding="utf-8")
        return base

    def test_locked_asset_is_verified_before_atomic_install(self) -> None:
        content = b"verified executable\n"
        url = "https://downloads.example.test/fixture-tool"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            contract_root = self.contract_root(base, url=url, content=content)
            destination = base / "destination"
            destination.mkdir()
            with mock.patch(
                "ci_workflows.tooling.urllib.request.urlopen",
                return_value=_Response(content, url),
            ):
                installed = install_locked_asset(
                    "fixture-tool",
                    destination_root=destination,
                    contract_root=contract_root,
                )
            target = destination / installed.filename
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(installed.sha256, hashlib.sha256(content).hexdigest())
            self.assertFalse((destination / ".fixture-tool.partial").exists())

    def test_locked_asset_rejects_redirect_and_checksum_mismatch_without_residue(self) -> None:
        content = b"expected\n"
        url = "https://downloads.example.test/fixture-tool"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            contract_root = self.contract_root(base, url=url, content=content)
            destination = base / "destination"
            destination.mkdir()
            with mock.patch(
                "ci_workflows.tooling.urllib.request.urlopen",
                return_value=_Response(content, "https://malicious.example/fixture-tool"),
            ):
                with self.assertRaises(FoundationError) as caught:
                    install_locked_asset(
                        "fixture-tool",
                        destination_root=destination,
                        contract_root=contract_root,
                    )
            self.assertEqual(caught.exception.instruction, "locked_asset_redirect_forbidden")
            self.assertEqual(list(destination.iterdir()), [])

            with mock.patch(
                "ci_workflows.tooling.urllib.request.urlopen",
                return_value=_Response(b"changed\n", url),
            ):
                with self.assertRaises(FoundationError) as caught:
                    install_locked_asset(
                        "fixture-tool",
                        destination_root=destination,
                        contract_root=contract_root,
                    )
            self.assertEqual(caught.exception.instruction, "checksum_mismatch")
            self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
