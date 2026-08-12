from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_manifest import remote_chart_manifest_digest
from ci_workflows.helm_types import HelmValidationError


class HelmManifestReadBackTests(unittest.TestCase):
    def setup_state(self, root: Path) -> tuple[Path, Path]:
        source = root / "source"
        source.mkdir()
        state = root / "state"
        state.mkdir()
        auth = state / "helm-validation/config/registry/config.json"
        auth.parent.mkdir(parents=True)
        auth.write_text('{"auths":{}}', encoding="utf-8")
        return source, state

    @staticmethod
    def manifest(package_sha: str) -> str:
        return json.dumps(
            {
                "schemaVersion": 2,
                "config": {
                    "mediaType": "application/vnd.cncf.helm.config.v1+json",
                    "digest": "sha256:" + "f" * 64,
                    "size": 100,
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
                        "digest": "sha256:" + package_sha,
                        "size": 200,
                    }
                ],
            },
            separators=(",", ":"),
        )

    def test_registry_reported_digest_and_raw_bytes_must_agree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state = self.setup_state(root)
            package_sha = "e" * 64
            manifest = self.manifest(package_sha)
            digest = "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                command = list(argv)
                calls.append(command)
                if "--format" in command:
                    return subprocess.CompletedProcess(command, 0, digest + "\n", "")
                return subprocess.CompletedProcess(command, 0, manifest, "")

            with patch("ci_workflows.helm_manifest._run", side_effect=fake_run):
                actual = remote_chart_manifest_digest(
                    source,
                    state,
                    "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                    "1.2.3",
                    package_sha,
                    {"PATH": "/usr/bin", "HOME": str(root)},
                )
            self.assertEqual(actual, digest)
            self.assertEqual(len(calls), 2)
            self.assertIn("--format", calls[0])
            self.assertNotIn("--raw", calls[0])
            self.assertIn("--raw", calls[1])

    def test_raw_manifest_digest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state = self.setup_state(root)
            package_sha = "e" * 64
            manifest = self.manifest(package_sha)
            wrong = "sha256:" + "0" * 64

            def fake_run(argv, **kwargs):
                command = list(argv)
                if "--format" in command:
                    return subprocess.CompletedProcess(command, 0, wrong, "")
                return subprocess.CompletedProcess(command, 0, manifest, "")

            with patch("ci_workflows.helm_manifest._run", side_effect=fake_run):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "remote_manifest_digest_mismatch",
                ):
                    remote_chart_manifest_digest(
                        source,
                        state,
                        "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                        "1.2.3",
                        package_sha,
                        {"PATH": "/usr/bin", "HOME": str(root)},
                    )

    def test_reported_digest_and_helm_layer_shape_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state = self.setup_state(root)
            package_sha = "e" * 64
            manifest = self.manifest(package_sha)
            digest = "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()

            with patch(
                "ci_workflows.helm_manifest._run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "not-a-digest", ""),
                ],
            ):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "remote_manifest_invalid",
                ):
                    remote_chart_manifest_digest(
                        source,
                        state,
                        "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                        "1.2.3",
                        package_sha,
                        {"PATH": "/usr/bin", "HOME": str(root)},
                    )

            bad_manifest = self.manifest("0" * 64)
            bad_digest = "sha256:" + hashlib.sha256(
                bad_manifest.encode("utf-8")
            ).hexdigest()
            with patch(
                "ci_workflows.helm_manifest._run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, bad_digest, ""),
                    subprocess.CompletedProcess([], 0, bad_manifest, ""),
                ],
            ):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "remote_manifest_invalid",
                ):
                    remote_chart_manifest_digest(
                        source,
                        state,
                        "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                        "1.2.3",
                        package_sha,
                        {"PATH": "/usr/bin", "HOME": str(root)},
                    )

    def test_release_adapter_routes_manifest_proof_through_new_module(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "scripts/ci/helm_release.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "from ci_workflows.helm_manifest import remote_chart_manifest_digest",
            script,
        )


if __name__ == "__main__":
    unittest.main()
