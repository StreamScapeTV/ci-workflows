from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.oci_contract import (  # noqa: E402
    load_contract,
    metadata_labels,
    render_engine_mapping,
    request_from_mapping,
    resolve_plan,
    validate_generated_mapping,
)
from ci_workflows.oci_execution import (  # noqa: E402
    inspect_layout,
    stage_context,
    validate_dockerfile_bases,
    verify_no_secret_leakage,
)
from ci_workflows.oci_types import OciBuildError, OciTarget  # noqa: E402

SHA = "a" * 40


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_blob(layout: Path, data: bytes) -> str:
    value = digest(data)
    path = layout / "blobs" / "sha256" / value.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return value


def synthetic_layout(root: Path, *, platform: str = "linux/amd64", labels=None, secret: str = "") -> Path:
    layout = root / "layout"
    layout.mkdir(parents=True)
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}', encoding="utf-8")
    os_name, architecture, *variant = platform.split("/")
    config_payload = {
        "architecture": architecture,
        "os": os_name,
        "config": {
            "User": "65532:65532",
            "Entrypoint": ["/hello"],
            "Cmd": None,
            "Labels": labels or {},
            "Env": [secret] if secret else [],
        },
        "rootfs": {"type": "layers", "diff_ids": []},
        "history": [],
    }
    config_bytes = json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode()
    config_digest = write_blob(layout, config_bytes)
    layer_bytes = b"layer-content"
    layer_digest = write_blob(layout, layer_bytes)
    manifest_payload = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": config_digest,
            "size": len(config_bytes),
        },
        "layers": [{
            "mediaType": "application/vnd.oci.image.layer.v1.tar",
            "digest": layer_digest,
            "size": len(layer_bytes),
        }],
    }
    manifest_bytes = json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")).encode()
    manifest_digest = write_blob(layout, manifest_bytes)
    platform_payload = {"os": os_name, "architecture": architecture}
    if variant:
        platform_payload["variant"] = variant[0]
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": manifest_digest,
            "size": len(manifest_bytes),
            "platform": platform_payload,
        }],
    }
    (layout / "index.json").write_text(json.dumps(index, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return layout


class OciBuildTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def test_contract_and_generated_mapping_are_exact_and_deterministic(self) -> None:
        validate_generated_mapping(ROOT, self.contract)
        actual = json.loads((ROOT / "generated/oci-engine-mapping.json").read_text())
        self.assertEqual(actual, render_engine_mapping(self.contract))
        self.assertEqual("oci.build", actual["workflow_api"])
        self.assertEqual(
            ["linux", "amd64", "buildah", "tiny"],
            actual["products"]["ciw-oci-smoke"]["runs_on"],
        )
        self.assertEqual(
            ["linux", "amd64", "buildah", "high"],
            actual["products"]["flux-runner-images"]["runs_on"],
        )

    def test_request_is_product_only_and_rejects_engine_runner_registry_and_callback(self) -> None:
        base = {
            "repository": "StreamScapeTV/ci-workflows",
            "admitted_sha": SHA,
            "product_id": "ciw-oci-smoke",
            "release_version": "1.2.3",
            "platform_set": "linux-amd64",
            "artifact_exception_id": None,
        }
        request = request_from_mapping(base, {"GITHUB_EVENT_NAME": "push"})
        plan = resolve_plan(ROOT, request)
        self.assertEqual("buildah-v1", plan.builder_id)
        self.assertEqual(("linux", "amd64", "buildah", "tiny"), plan.runs_on)
        for field in (
            "engine", "builder", "docker", "buildah", "buildkit", "podman",
            "socket", "storage_driver", "registry_command", "runner",
            "runner_labels", "command", "arguments", "callback", "publish",
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(OciBuildError, "forbidden_input"):
                    request_from_mapping({**base, field: "attacker"}, {"GITHUB_EVENT_NAME": "push"})

    def test_fork_source_cannot_reach_privileged_buildah_capacity(self) -> None:
        event = Path(self._testMethodName + "-event.json")
        try:
            event.write_text(json.dumps({"pull_request": {"head": {"repo": {"full_name": "attacker/fork"}}}}))
            request = request_from_mapping(
                {"repository": "StreamScapeTV/ci-workflows", "admitted_sha": SHA, "product_id": "ciw-oci-smoke"},
                {
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_EVENT_PATH": str(event),
                    "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
                },
            )
            self.assertEqual("untrusted-fork", request.source_trust)
            with self.assertRaisesRegex(OciBuildError, "unsupported_consumer"):
                resolve_plan(ROOT, request)
        finally:
            event.unlink(missing_ok=True)

    def test_unsupported_consumers_and_web_mobile_apple_products_fail_closed(self) -> None:
        for repository, product in (
            ("StreamScapeTV/StreamScapeWeb", "streamscape-web-image"),
            ("StreamScapeTV/iptv-android", "android-app-image"),
            ("StreamScapeTV/directus-front", "flutter-app-image"),
            ("StreamScapeTV/iptv-apple", "apple-app-image"),
        ):
            request = request_from_mapping(
                {"repository": repository, "admitted_sha": SHA, "product_id": product},
                {"GITHUB_EVENT_NAME": "push"},
            )
            with self.assertRaisesRegex(OciBuildError, "unsupported_product"):
                resolve_plan(ROOT, request)
        wrong = request_from_mapping(
            {"repository": "StreamScapeTV/flux", "admitted_sha": SHA, "product_id": "ciw-oci-smoke"},
            {"GITHUB_EVENT_NAME": "push"},
        )
        with self.assertRaisesRegex(OciBuildError, "unsupported_consumer"):
            resolve_plan(ROOT, wrong)

    def test_flux_plan_uses_independent_high_builder_and_only_handoff_data(self) -> None:
        request = request_from_mapping(
            {"repository": "StreamScapeTV/flux", "admitted_sha": SHA, "product_id": "flux-runner-images"},
            {"GITHUB_EVENT_NAME": "push"},
        )
        plan = resolve_plan(ROOT, request)
        self.assertTrue(plan.flux_asset)
        self.assertEqual("buildah-high", plan.runner_profile)
        self.assertEqual("flux-runner-images-canary", plan.canary_id)
        self.assertEqual("flux-policy:runner-images/current-known-good", plan.previous_known_good)
        self.assertEqual("flux-runner-images-rollback", plan.rollback_id)
        self.assertNotIn("cluster", json.dumps(plan.planning_outputs()).lower())
        self.assertNotIn("kube", json.dumps(plan.planning_outputs()).lower())

    def test_platform_set_may_confirm_but_not_override_contract(self) -> None:
        request = request_from_mapping(
            {
                "repository": "StreamScapeTV/ci-workflows",
                "admitted_sha": SHA,
                "product_id": "ciw-oci-smoke",
                "platform_set": "linux-amd64",
            },
            {"GITHUB_EVENT_NAME": "push"},
        )
        self.assertEqual(("linux/amd64",), resolve_plan(ROOT, request).targets[0].platforms)
        mismatched = request_from_mapping(
            {
                "repository": "StreamScapeTV/ci-workflows",
                "admitted_sha": SHA,
                "product_id": "ciw-oci-smoke",
                "platform_set": "linux-multi-arch",
            },
            {"GITHUB_EVENT_NAME": "push"},
        )
        with self.assertRaisesRegex(OciBuildError, "platform_override_forbidden"):
            resolve_plan(ROOT, mismatched)

    def test_created_label_is_normalized_rfc3339_utc(self) -> None:
        request = request_from_mapping(
            {"repository": "StreamScapeTV/ci-workflows", "admitted_sha": SHA, "product_id": "ciw-oci-smoke"},
            {"GITHUB_EVENT_NAME": "push"},
        )
        plan = resolve_plan(ROOT, request)
        labels = metadata_labels(self.contract, plan, plan.targets[0], 1)
        self.assertEqual("1970-01-01T00:00:01Z", labels["org.opencontainers.image.created"])

    def test_base_identity_requires_scratch_or_exact_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            good = root / "good"
            good.write_text("FROM scratch\n", encoding="utf-8")
            self.assertEqual(("scratch",), validate_dockerfile_bases(good))
            pinned = root / "pinned"
            pinned.write_text("FROM example.invalid/base@sha256:" + "b" * 64 + "\n", encoding="utf-8")
            self.assertEqual(1, len(validate_dockerfile_bases(pinned)))
            for source in ("FROM python:3.12\n", "ARG BASE\nFROM $BASE\n", "FROM example.invalid/base@sha256:bad\n"):
                bad = root / hashlib.sha256(source.encode()).hexdigest()
                bad.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(OciBuildError, "base_identity_mutable"):
                    validate_dockerfile_bases(bad)

    def test_context_staging_is_tracked_clean_and_no_follow(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "Containerfile").write_text("FROM scratch\n", encoding="utf-8")
            (root / "payload").write_text("payload\n", encoding="utf-8")
            (root / "verify.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (root / "verify.sh").chmod(0o755)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            target = OciTarget(
                "fixture", ".", "Containerfile", None, ("linux/amd64",), "verify.sh",
                None, (), (), (), (), (), (), {}, (),
            )
            staged = stage_context(root, target, Path(temp) / "stage")
            self.assertEqual("payload\n", (staged / "payload").read_text())
            alias = Path(temp) / "source-alias"
            alias.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(OciBuildError, "invalid_path"):
                stage_context(alias, target, Path(temp) / "stage-alias")
            (root / "untracked").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(OciBuildError, "dirty_context"):
                stage_context(root, target, Path(temp) / "stage2")

    def test_layout_inspection_binds_platform_config_layers_and_labels(self) -> None:
        request = request_from_mapping(
            {"repository": "StreamScapeTV/ci-workflows", "admitted_sha": SHA, "product_id": "ciw-oci-smoke"},
            {"GITHUB_EVENT_NAME": "push"},
        )
        target = resolve_plan(ROOT, request).targets[0]
        labels = {
            "dev.streamscape.product": "contract-smoke",
            "org.opencontainers.image.created": "1",
            "org.opencontainers.image.description": "x",
            "org.opencontainers.image.licenses": "MIT",
            "org.opencontainers.image.revision": SHA,
            "org.opencontainers.image.source": "https://github.com/StreamScapeTV/ci-workflows",
            "org.opencontainers.image.title": "x",
            "org.opencontainers.image.version": "1.0.0",
        }
        with tempfile.TemporaryDirectory() as temp:
            layout = synthetic_layout(Path(temp), labels=labels)
            result = inspect_layout(layout, target, labels)
            self.assertEqual("contract-smoke", result.target_id)
            self.assertEqual("linux/amd64", result.platform_results[0].platform)
            self.assertRegex(result.index_digest, r"^sha256:[0-9a-f]{64}$")
            config_blob = layout / "blobs" / "sha256" / result.platform_results[0].config_digest.removeprefix("sha256:")
            config_blob.write_bytes(config_blob.read_bytes() + b"drift")
            with self.assertRaisesRegex(OciBuildError, "oci_digest_mismatch"):
                inspect_layout(layout, target, labels)
            rebuilt = synthetic_layout(Path(temp) / "media", labels=labels)
            index = json.loads((rebuilt / "index.json").read_text())
            index["mediaType"] = "application/vnd.docker.distribution.manifest.list.v2+json"
            (rebuilt / "index.json").write_text(json.dumps(index))
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                inspect_layout(rebuilt, target, labels)

    def test_secret_content_and_digest_are_rejected_from_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = root / "secret"
            secret.write_text("private-build-secret", encoding="utf-8")
            secret.chmod(0o600)
            layout = synthetic_layout(root, labels={}, secret="private-build-secret")
            with self.assertRaisesRegex(OciBuildError, "secret_leakage"):
                verify_no_secret_leakage(layout, {"build-secret": secret})
            secret.chmod(0o644)
            with self.assertRaisesRegex(OciBuildError, "secret_permissions_invalid"):
                verify_no_secret_leakage(layout, {"build-secret": secret})


if __name__ == "__main__":
    unittest.main()
