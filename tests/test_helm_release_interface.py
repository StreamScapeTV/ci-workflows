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
    parse_oci_publication_evidence,
    remote_chart_manifest_digest,
)
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests/fixtures/helm-validation"
SHA = "a" * 40
VERSION = "1.2.3"
BACKEND_REPOSITORY = "ghcr.io/streamscapetv/iptv-backend"
BACKEND_SOURCE_REPOSITORY = "git.faruqi.dev/mimranfaruqi/iptv-backend"
BACKEND_DIGEST = "sha256:" + "c" * 64
BACKEND_REF = f"{BACKEND_REPOSITORY}@{BACKEND_DIGEST}"


def backend_oci_evidence() -> tuple[str, str]:
    image_digest = json.dumps(
        {"iptv-backend": BACKEND_DIGEST},
        sort_keys=True,
        separators=(",", ":"),
    )
    immutable = json.dumps(
        {
            "release": {"source_sha": SHA, "version": VERSION},
            "targets": {
                "iptv-backend": {
                    "manifest_digest": BACKEND_DIGEST,
                    "repository": BACKEND_REPOSITORY,
                    "source_sha": f"{BACKEND_REPOSITORY}:sha-{SHA}",
                    "version": f"{BACKEND_REPOSITORY}:{VERSION}",
                }
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return image_digest, immutable


class HelmReleaseInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release_contract = load_release_bindings(ROOT)

    def test_direct_oci_outputs_derive_exact_backend_reference(self) -> None:
        image_digest, immutable = backend_oci_evidence()
        refs = parse_oci_publication_evidence(
            image_digest,
            immutable,
            "iptv-backend-chart",
            self.release_contract,
            SHA,
            VERSION,
        )
        self.assertEqual(refs, (BACKEND_REF,))

    def test_no_image_binding_requires_oci_outputs_to_be_omitted(self) -> None:
        self.assertEqual(
            parse_oci_publication_evidence(
                "",
                "",
                "flux-github-actions-runner-chart",
                self.release_contract,
                SHA,
                VERSION,
            ),
            (),
        )
        image_digest, immutable = backend_oci_evidence()
        for first, second in ((image_digest, ""), ("", immutable), (image_digest, immutable)):
            with self.subTest(first=bool(first), second=bool(second)):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "unexpected_oci_publication_evidence",
                ):
                    parse_oci_publication_evidence(
                        first,
                        second,
                        "flux-github-actions-runner-chart",
                        self.release_contract,
                        SHA,
                        VERSION,
                    )

    def test_bound_product_requires_both_direct_oci_outputs(self) -> None:
        image_digest, immutable = backend_oci_evidence()
        for first, second in (("", ""), (image_digest, ""), ("", immutable)):
            with self.subTest(first=bool(first), second=bool(second)):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "oci_publication_evidence_required",
                ):
                    parse_oci_publication_evidence(
                        first,
                        second,
                        "iptv-backend-chart",
                        self.release_contract,
                        SHA,
                        VERSION,
                    )

    def test_oci_evidence_source_version_target_repository_and_digest_parity(self) -> None:
        image_digest, immutable = backend_oci_evidence()
        base = json.loads(immutable)
        mutations = []

        wrong_source = json.loads(immutable)
        wrong_source["release"]["source_sha"] = "b" * 40
        mutations.append((image_digest, wrong_source))

        wrong_version = json.loads(immutable)
        wrong_version["release"]["version"] = "9.9.9"
        mutations.append((image_digest, wrong_version))

        wrong_target = json.loads(immutable)
        wrong_target["targets"] = {"other": wrong_target["targets"]["iptv-backend"]}
        mutations.append((image_digest, wrong_target))

        wrong_repository = json.loads(immutable)
        wrong_repository["targets"]["iptv-backend"]["repository"] = "ghcr.io/streamscapetv/other"
        mutations.append((image_digest, wrong_repository))

        wrong_version_ref = json.loads(immutable)
        wrong_version_ref["targets"]["iptv-backend"]["version"] = f"{BACKEND_REPOSITORY}:9.9.9"
        mutations.append((image_digest, wrong_version_ref))

        wrong_source_ref = json.loads(immutable)
        wrong_source_ref["targets"]["iptv-backend"]["source_sha"] = f"{BACKEND_REPOSITORY}:sha-{'b' * 40}"
        mutations.append((image_digest, wrong_source_ref))

        wrong_manifest = json.loads(immutable)
        wrong_manifest["targets"]["iptv-backend"]["manifest_digest"] = "sha256:" + "d" * 64
        mutations.append((image_digest, wrong_manifest))

        wrong_digest_map = json.dumps(
            {"iptv-backend": "sha256:" + "d" * 64},
            separators=(",", ":"),
        )
        mutations.append((wrong_digest_map, base))

        extra_digest = json.dumps(
            {"iptv-backend": BACKEND_DIGEST, "other": BACKEND_DIGEST},
            separators=(",", ":"),
        )
        mutations.append((extra_digest, base))

        for digest_value, immutable_value in mutations:
            with self.subTest(immutable=immutable_value):
                raw = (
                    immutable_value
                    if isinstance(immutable_value, str)
                    else json.dumps(immutable_value, separators=(",", ":"))
                )
                with self.assertRaises(HelmValidationError):
                    parse_oci_publication_evidence(
                        digest_value,
                        raw,
                        "iptv-backend-chart",
                        self.release_contract,
                        SHA,
                        VERSION,
                    )

    def test_backend_binding_changes_only_isolated_values_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(FIXTURE_ROOT / "backend", source)
            chart = source / "charts/iptv-backend"
            copied = root / "copy"
            shutil.copytree(chart, copied)
            original = (chart / "values.yaml").read_bytes()
            product = self.release_contract["products"]["iptv-backend-chart"]
            apply_release_image_bindings(copied, product, (BACKEND_REF,))
            updated = (copied / "values.yaml").read_text(encoding="utf-8")
            self.assertIn(BACKEND_REPOSITORY, updated)
            self.assertNotIn(BACKEND_SOURCE_REPOSITORY, updated)
            self.assertIn(BACKEND_DIGEST, updated)
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
                "ghcr.io/streamscapetv/agent-state@sha256:" + "d" * 64
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
                    VERSION,
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
                        VERSION,
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
        for name in ("image_digest", "immutable_references_json"):
            self.assertIn(f"{name}:", workflow)
            self.assertIn(f"{name}:", action)
        self.assertNotIn("required_image_references_json", workflow)
        self.assertNotIn("required_image_references_json", action)
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
        self.assertIn('"image_digest"', publication)
        self.assertIn('"immutable_references_json"', publication)


if __name__ == "__main__":
    unittest.main()
