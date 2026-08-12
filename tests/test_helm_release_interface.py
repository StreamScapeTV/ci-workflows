from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_release import (
    apply_release_image_bindings,
    load_release_bindings,
    parse_required_image_references,
    remote_chart_manifest_digest,
)
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests/fixtures/helm-validation"
BACKEND_REF = (
    "git.faruqi.dev/mimranfaruqi/iptv-backend@sha256:" + "c" * 64
)


class HelmReleaseInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release_contract = load_release_bindings(ROOT)

    def test_required_image_set_is_exact_sorted_and_immutable(self) -> None:
        refs = parse_required_image_references(
            json.dumps([BACKEND_REF]),
            "iptv-backend-chart",
            self.release_contract,
        )
        self.assertEqual(refs, (BACKEND_REF,))
        self.assertEqual(
            parse_required_image_references(
                "[]",
                "flux-github-actions-runner-chart",
                self.release_contract,
            ),
            (),
        )
        for invalid in (
            "",
            "not-json",
            json.dumps(["git.faruqi.dev/mimranfaruqi/iptv-backend:latest"]),
            json.dumps([BACKEND_REF, BACKEND_REF]),
            json.dumps(
                [
                    "git.faruqi.dev/mimranfaruqi/other@sha256:"
                    + "d" * 64
                ]
            ),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(HelmValidationError):
                    parse_required_image_references(
                        invalid,
                        "iptv-backend-chart",
                        self.release_contract,
                    )

    def test_backend_binding_changes_only_isolated_values_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(FIXTURE_ROOT / "backend", source)
            chart = source / "charts/iptv-backend"
            copy = root / "copy"
            shutil.copytree(chart, copy)
            original = (chart / "values.yaml").read_bytes()
            product = self.release_contract["products"]["iptv-backend-chart"]
            apply_release_image_bindings(copy, product, (BACKEND_REF,))
            updated = (copy / "values.yaml").read_text(encoding="utf-8")
            self.assertIn("sha256:" + "c" * 64, updated)
            self.assertEqual((chart / "values.yaml").read_bytes(), original)

    def test_agent_state_without_digest_value_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart = root / "chart"
            shutil.copytree(
                FIXTURE_ROOT / "agent-state/charts/agent-state",
                chart,
            )
            reference = (
                "git.faruqi.dev/mimranfaruqi/agent-state@sha256:"
                + "d" * 64
            )
            product = self.release_contract["products"]["agent-state-chart"]
            with self.assertRaisesRegex(
                HelmValidationError,
                "image_binding_invalid",
            ):
                apply_release_image_bindings(chart, product, (reference,))

    def test_remote_manifest_digest_is_raw_oci_identity_and_checks_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            state = root / "state"
            state.mkdir()
            auth = state / "helm-validation/config/registry/config.json"
            auth.parent.mkdir(parents=True)
            auth.write_text('{"auths":{}}', encoding="utf-8")
            package_sha = "e" * 64
            manifest = json.dumps(
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
            calls: list[list[str]] = []

            def fake_run(
                argv,
                *,
                cwd,
                environment,
                timeout,
                code,
                stdin=None,
                check=True,
            ):
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, manifest, "")

            with patch("ci_workflows.helm_release._run", side_effect=fake_run):
                digest = remote_chart_manifest_digest(
                    source,
                    state,
                    "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                    "1.2.3",
                    package_sha,
                    {"PATH": "/usr/bin", "HOME": str(root)},
                )
            self.assertEqual(
                digest,
                "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(calls[0][:3], ["skopeo", "inspect", "--raw"])
            self.assertNotIn("token", " ".join(calls[0]).casefold())

            bad = manifest.replace(package_sha, "0" * 64)
            with patch(
                "ci_workflows.helm_release._run",
                return_value=subprocess.CompletedProcess([], 0, bad, ""),
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

    def test_publish_workflow_orders_tag_revalidation_before_credentials(self) -> None:
        workflow = (
            ROOT / ".github/workflows/reusable-helm-publish.yml"
        ).read_text(encoding="utf-8")
        action = (
            ROOT / "actions/publish-helm/action.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("required_image_references_json:", workflow)
        self.assertIn("required_image_references_json:", action)
        self.assertEqual(workflow.count("actions/resolve-release-tag"), 2)
        revalidate = workflow.index(
            "Revalidate exact release tag immediately before registry write"
        )
        credentials = workflow.index("${{ secrets.registry_username }}")
        self.assertLess(revalidate, credentials)
        self.assertIn("helm_release.py", action)
        publication = (
            ROOT / "contracts/helm-publication.json"
        ).read_text(encoding="utf-8")
        self.assertIn('"chart_digest": "remote-oci-manifest-sha256"', publication)
        self.assertIn(
            '"tag_authority": "resolve-and-revalidate-before-write"',
            publication,
        )


if __name__ == "__main__":
    unittest.main()
