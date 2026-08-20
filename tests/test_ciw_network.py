"""Focused coverage for the primitive-backed ``ciw network run`` adapter."""
from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from ci_workflows import ciw
from ci_workflows.ciw_network import configure_network, execute_network
from ci_workflows.ciw_types import CIWContext, CIWError
from ci_workflows.ciw_docs import load_command_contract
from ci_workflows.network_primitives import DownloadResult

ROOT = Path(__file__).resolve().parents[1]


class NetworkCIWTests(unittest.TestCase):
    def _args(self, *values: str) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        configure_network(parser)
        return parser.parse_args(list(values))

    def _context(self, dependencies: Path, generated: Path, **extra: str) -> CIWContext:
        environment = {
            "CI_DEPENDENCY_ROOT": str(dependencies),
            "CI_GENERATED_ROOT": str(generated),
            **extra,
        }
        return CIWContext(ROOT, environment, io.StringIO(), io.StringIO())

    def test_runtime_registry_exposes_one_network_command(self) -> None:
        runtime = ciw.runtime_command_index()
        self.assertIn("network run", runtime)
        spec = runtime["network run"]
        self.assertEqual(spec.qualified_handler, "ci_workflows.ciw.handle_network")
        contract = load_command_contract(ROOT)
        row = next(
            item
            for item in contract["commands"]
            if item["domain"] == "network" and item["operation"] == "run"
        )
        self.assertEqual(row["handler"], spec.qualified_handler)
        ciw.validate_runtime_contract(ROOT)

    def test_download_delegates_to_primitive_and_exposes_same_job_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependencies = root / "dependencies"
            generated = root / "generated"
            dependencies.mkdir()
            generated.mkdir()
            expected = DownloadResult(
                requested_url="https://example.invalid/tool.zip",
                final_url="https://example.invalid/tool.zip",
                status=200,
                attempts=1,
                redirects=0,
                relative_path="tool.zip",
                size=7,
                sha256="a" * 64,
                content_type="application/zip",
            )
            with mock.patch(
                "ci_workflows.ciw_network.download_file",
                return_value=expected,
            ) as download:
                result = execute_network(
                    self._args(
                        "--operation",
                        "download",
                        "--url",
                        "https://example.invalid/tool.zip",
                        "--relative-path",
                        "tool.zip",
                        "--expected-sha256",
                        "a" * 64,
                        "--expected-size",
                        "7",
                        "--expected-content-type",
                        "application/zip",
                        "--maximum-bytes",
                        "8",
                    ),
                    self._context(dependencies, generated),
                )

            self.assertEqual(result.outputs["result"], "success")
            self.assertEqual(result.outputs["local_path"], str(dependencies / "tool.zip"))
            payload = json.loads(result.outputs["network_result_json"])
            self.assertEqual(payload["operation"], "download")
            self.assertEqual(payload["sha256"], "a" * 64)
            self.assertEqual(payload["size"], 7)
            download.assert_called_once_with(
                "https://example.invalid/tool.zip",
                destination_root=dependencies,
                relative_path="tool.zip",
                environment={
                    "CI_DEPENDENCY_ROOT": str(dependencies),
                    "CI_GENERATED_ROOT": str(generated),
                },
                expected_sha256="a" * 64,
                expected_size=7,
                expected_content_type="application/zip",
                maximum_bytes=8,
            )

    def test_verify_uses_registered_dependency_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependencies = root / "dependencies"
            generated = root / "generated"
            dependencies.mkdir()
            generated.mkdir()
            target = dependencies / "payload.bin"
            target.write_bytes(b"payload")
            result = execute_network(
                self._args(
                    "--operation",
                    "verify",
                    "--relative-path",
                    "payload.bin",
                    "--expected-size",
                    "7",
                ),
                self._context(dependencies, generated),
            )
            payload = json.loads(result.outputs["network_result_json"])
            self.assertEqual(payload["operation"], "verify")
            self.assertEqual(payload["size"], 7)
            self.assertEqual(result.outputs["local_path"], str(target))

    def test_extract_runs_real_zip_primitive_into_generated_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependencies = root / "dependencies"
            generated = root / "generated"
            dependencies.mkdir()
            generated.mkdir()
            archive = dependencies / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("bin/tool", b"tool-bytes")
            result = execute_network(
                self._args(
                    "--operation",
                    "extract",
                    "--relative-path",
                    "bundle.zip",
                    "--archive-format",
                    "zip",
                    "--relative-destination",
                    "bundle",
                ),
                self._context(dependencies, generated),
            )
            payload = json.loads(result.outputs["network_result_json"])
            self.assertEqual(payload["operation"], "extract")
            self.assertEqual(payload["file_count"], 1)
            self.assertEqual(payload["destination"], "bundle")
            self.assertEqual(result.outputs["local_path"], str(generated / "bundle"))
            self.assertEqual((generated / "bundle/bin/tool").read_bytes(), b"tool-bytes")

    def test_invalid_environment_size_fails_with_bounded_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependencies = root / "dependencies"
            generated = root / "generated"
            dependencies.mkdir()
            generated.mkdir()
            context = self._context(
                dependencies,
                generated,
                INPUT_URL="https://example.invalid/file",
                INPUT_RELATIVE_PATH="file",
                INPUT_EXPECTED_SIZE="seven",
            )
            with self.assertRaises(CIWError) as failure:
                execute_network(self._args("--operation", "download"), context)
            self.assertEqual(failure.exception.code, "expected_size_invalid")


if __name__ == "__main__":
    unittest.main()
