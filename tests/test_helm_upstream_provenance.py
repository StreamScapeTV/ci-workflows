from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ci_workflows.helm_contract import load_helm_contract, request_from_environment
from ci_workflows.helm_dependency_policy import resolve_validation_plan
from ci_workflows.helm_types import HelmValidationError
from ci_workflows.helm_upstream_policy import load_upstream_policy


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests/fixtures/helm-validation"
SHA = "a" * 40
ARC_COMMIT = "9bb16ae49d0ce585d8e682aa7e2668a6e832d5d8"


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
        cls.upstream_policy = load_upstream_policy(ROOT)

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
        return resolve_validation_plan(
            root / "source",
            self.contract,
            request,
            contract_root=ROOT,
        )

    def test_complete_fixture_is_admitted_under_real_origin_policy(self) -> None:
        # Chart-content and private-mirror digests remain deliberately synthetic
        # fixture evidence. Source origin, tag commit, version, license, and
        # mirror destinations are real centrally verified policy identities.
        with self.copied_flux() as directory:
            plan = self.resolve_flux(Path(directory))
            self.assertEqual(plan.product.chart_name, "github-actions-runner")
        rows = self.upstream_policy["products"]["flux-github-actions-runner-chart"]
        self.assertTrue(rows)
        self.assertTrue(
            all(
                row["upstream_repository"]
                == "https://github.com/actions/actions-runner-controller"
                and row["upstream_tag"] == "0.14.2"
                and row["upstream_commit"] == ARC_COMMIT
                and row["license"] == "Apache-2.0"
                for row in rows
            )
        )

    def test_missing_provenance_fails_closed(self) -> None:
        with self.copied_flux() as directory:
            root = Path(directory)
            manifest = self.load_manifest(root)
            manifest["upstream_assets"] = []
            self.write_manifest(root, manifest)
            with self.assertRaisesRegex(
                HelmValidationError,
                "upstream_policy_mismatch",
            ):
                self.resolve_flux(root)

    def test_stable_origin_repository_tag_commit_license_and_mirror_are_central(self) -> None:
        mutations = (
            ("upstream_repository", "https://github.com/example/fork"),
            ("upstream_tag", "0.14.3"),
            ("upstream_commit", "1" * 40),
            ("license", "MIT"),
            (
                "mirror_repository",
                "oci://git.faruqi.dev/mimranfaruqi/other/gha-runner-scale-set",
            ),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                with self.copied_flux() as directory:
                    root = Path(directory)
                    manifest = self.load_manifest(root)
                    manifest["upstream_assets"][0][field] = value
                    self.write_manifest(root, manifest)
                    with self.assertRaisesRegex(
                        HelmValidationError,
                        "upstream_policy_mismatch",
                    ):
                        self.resolve_flux(root)

    def test_digest_parity_remains_producer_owned_and_exact(self) -> None:
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

    def test_digest_and_patch_shape_fail_closed(self) -> None:
        mutations = (
            ("upstream_chart_digest", "sha256:1234", "upstream_provenance_invalid"),
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
                    "upstream_repository": "https://github.com/actions/actions-runner-controller",
                    "upstream_tag": "0.14.2",
                    "upstream_commit": ARC_COMMIT,
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
                "upstream_policy_mismatch",
            ):
                resolve_validation_plan(
                    root / "source",
                    self.contract,
                    request,
                    contract_root=ROOT,
                )


if __name__ == "__main__":
    unittest.main()
