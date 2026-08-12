from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ci_workflows.helm_contract import (
    load_helm_contract,
    request_from_environment,
    resolve_validation_plan,
)
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests/fixtures/helm-validation"
SHA = "a" * 40


def request_environment(product_id: str, repository: str, release_version: str) -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": repository,
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_PRODUCT_ID": product_id,
        "INPUT_RELEASE_VERSION": release_version,
        "INPUT_VALUES_PROFILE": "default",
        "INPUT_POLICY_PATH": "",
        "INPUT_ARTIFACT_EXCEPTION_ID": "",
        "INPUT_SOURCE_TRUST": "trusted-exact",
    }


class HelmUpstreamProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_helm_contract(ROOT)

    def copied_flux(self) -> tempfile.TemporaryDirectory[str]:
        directory = tempfile.TemporaryDirectory()
        shutil.copytree(FIXTURE_ROOT / "flux", Path(directory.name) / "source")
        return directory

    def manifest_path(self, root: Path) -> Path:
        return root / "source/.streamscape/helm-product.json"

    def load_manifest(self, root: Path) -> dict:
        return json.loads(self.manifest_path(root).read_text(encoding="utf-8"))

    def write_manifest(self, root: Path, manifest: dict) -> None:
        self.manifest_path(root).write_text(
            json.dumps(manifest, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    def resolve_flux(self, root: Path):
        request = request_from_environment(
            request_environment(
                "flux-github-actions-runner-chart",
                "StreamScapeTV/flux",
                "1.3.0",
            )
        )
        return resolve_validation_plan(root / "source", self.contract, request)

    def test_complete_synthetic_fixture_is_admitted(self) -> None:
        # These example.com source identities and repeated hashes are deliberately
        # synthetic. They exercise the contract and are not real ARC provenance.
        with self.copied_flux() as directory:
            plan = self.resolve_flux(Path(directory))
            self.assertEqual(plan.product.chart_name, "github-actions-runner")

    def test_missing_provenance_fails_closed(self) -> None:
        with self.copied_flux() as directory:
            root = Path(directory)
            manifest = self.load_manifest(root)
            manifest["upstream_assets"] = []
            self.write_manifest(root, manifest)
            with self.assertRaisesRegex(
                HelmValidationError,
                "upstream_provenance_incomplete",
            ):
                self.resolve_flux(root)

    def test_mirror_repository_and_digest_parity_are_exact(self) -> None:
        with self.copied_flux() as directory:
            root = Path(directory)
            manifest = self.load_manifest(root)
            manifest["upstream_assets"][0]["mirror_repository"] += "-other"
            self.write_manifest(root, manifest)
            with self.assertRaisesRegex(
                HelmValidationError,
                "upstream_provenance_mismatch",
            ):
                self.resolve_flux(root)

        with self.copied_flux() as directory:
            root = Path(directory)
            manifest = self.load_manifest(root)
            manifest["upstream_assets"][0]["mirror_chart_digest"] = (
                "sha256:" + "4" * 64
            )
            self.write_manifest(root, manifest)
            with self.assertRaisesRegex(
                HelmValidationError,
                "upstream_provenance_mismatch",
            ):
                self.resolve_flux(root)

    def test_tag_must_match_wrapper_app_version(self) -> None:
        with self.copied_flux() as directory:
            root = Path(directory)
            manifest = self.load_manifest(root)
            for item in manifest["upstream_assets"]:
                item["upstream_tag"] = "0.14.3"
            self.write_manifest(root, manifest)
            with self.assertRaisesRegex(
                HelmValidationError,
                "upstream_provenance_mismatch",
            ):
                self.resolve_flux(root)

    def test_commit_digest_license_and_patch_shape_fail_closed(self) -> None:
        mutations = (
            ("upstream_commit", "not-a-commit", "upstream_provenance_invalid"),
            ("upstream_chart_digest", "sha256:1234", "upstream_provenance_invalid"),
            ("license", "not a license", "upstream_provenance_invalid"),
            ("patches", ["unreviewed.patch"], "upstream_patches_unsupported"),
        )
        for field, value, code in mutations:
            with self.subTest(field=field):
                with self.copied_flux() as directory:
                    root = Path(directory)
                    manifest = self.load_manifest(root)
                    manifest["upstream_assets"][0][field] = value
                    self.write_manifest(root, manifest)
                    with self.assertRaisesRegex(HelmValidationError, code):
                        self.resolve_flux(root)

    def test_application_chart_cannot_smuggle_upstream_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE_ROOT / "backend", root / "source")
            manifest_path = root / "source/.streamscape/helm-product.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["upstream_assets"] = [
                {
                    "name": "unexpected",
                    "upstream_repository": "https://github.com/example/unexpected",
                    "upstream_tag": "1.0.0",
                    "upstream_commit": "1" * 40,
                    "upstream_chart_digest": "sha256:" + "2" * 64,
                    "license": "Apache-2.0",
                    "mirror_repository": "oci://git.faruqi.dev/example/unexpected",
                    "mirror_chart_digest": "sha256:" + "2" * 64,
                    "patches": [],
                }
            ]
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            request = request_from_environment(
                request_environment(
                    "iptv-backend-chart",
                    "StreamScapeTV/iptv-backend",
                    "1.2.3",
                )
            )
            with self.assertRaisesRegex(
                HelmValidationError,
                "upstream_provenance_invalid",
            ):
                resolve_validation_plan(root / "source", self.contract, request)


if __name__ == "__main__":
    unittest.main()
