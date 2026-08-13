from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import oci_execution as build_execution
from ci_workflows import oci_publish as publication_runtime
from ci_workflows.oci_publish import (
    OciPublishError,
    PublishRequest,
    PublishTarget,
    authenticate,
    cleanup,
    inspect_layout,
    publication_capacity_roots,
    publication_state_root,
    publication_registry_host,
    replay_decision,
    request_from_environment,
    resolve_plan,
)
from ci_workflows.oci_types import OciBuildError, OciTarget

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/oci-publish/oci-products.json"
SHA = "a" * 40
MAX_OCI_TAG_VERSION = "1." + "1" * 124 + ".1"
OVERSIZED_OCI_TAG_VERSION = "1." + "1" * 125 + ".1"


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_blob(layout: Path, payload: bytes) -> dict[str, object]:
    digest = _digest(payload)
    path = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"digest": digest, "size": len(payload)}


def _make_layout(root: Path, target: PublishTarget, ref_name: str = "validation") -> Path:
    layout = root / "layout"
    layout.mkdir(parents=True)
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n', encoding="utf-8")
    labels = {
        "dev.streamscape.product": target.target_id,
        "org.opencontainers.image.created": "2026-08-12T00:00:00Z",
        "org.opencontainers.image.description": target.metadata["description"],
        "org.opencontainers.image.licenses": target.metadata["licenses"],
        "org.opencontainers.image.revision": target.source_reference.rsplit("sha-", 1)[1],
        "org.opencontainers.image.source": f"https://github.com/{target.source_repository}",
        "org.opencontainers.image.title": target.metadata["title"],
        "org.opencontainers.image.version": target.version_reference.rsplit(":", 1)[1],
    }
    manifests = []
    for platform in target.platforms:
        os_name, arch, *variant = platform.split("/")
        layer = _write_blob(layout, f"layer-{platform}".encode())
        layer["mediaType"] = "application/vnd.oci.image.layer.v1.tar"
        config_value = {
            "os": os_name,
            "architecture": arch,
            "variant": variant[0] if variant else None,
            "rootfs": {"type": "layers", "diff_ids": [layer["digest"]]},
            "config": {
                "User": target.required_user or "",
                "Entrypoint": list(target.required_entrypoint),
                "Cmd": list(target.required_command),
                "ExposedPorts": {port: {} for port in target.required_ports},
                "Labels": labels,
            },
        }
        config_bytes = json.dumps(config_value, sort_keys=True, separators=(",", ":")).encode()
        config = _write_blob(layout, config_bytes)
        config["mediaType"] = "application/vnd.oci.image.config.v1+json"
        manifest_value = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": config,
            "layers": [layer],
        }
        manifest_bytes = json.dumps(manifest_value, sort_keys=True, separators=(",", ":")).encode()
        descriptor = _write_blob(layout, manifest_bytes)
        descriptor.update(
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "platform": {
                    "os": os_name,
                    "architecture": arch,
                    **({"variant": variant[0]} if variant else {}),
                },
            }
        )
        manifests.append(descriptor)
    if len(manifests) == 1:
        top = dict(manifests[0])
    else:
        index_value = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": manifests,
        }
        index_bytes = json.dumps(index_value, sort_keys=True, separators=(",", ":")).encode()
        top = _write_blob(layout, index_bytes)
        top["mediaType"] = "application/vnd.oci.image.index.v1+json"
    top["annotations"] = {"org.opencontainers.image.ref.name": ref_name}
    (layout / "index.json").write_text(
        json.dumps({"schemaVersion": 2, "manifests": [top]}, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return layout


class PublishPlanTests(unittest.TestCase):
    def _root(self) -> tempfile.TemporaryDirectory[str]:
        temp = tempfile.TemporaryDirectory()
        contract_dir = Path(temp.name) / "contracts"
        contract_dir.mkdir()
        shutil.copyfile(FIXTURE, contract_dir / "oci-products.json")
        return temp

    def test_plan_derives_fixed_registry_and_immutable_references(self) -> None:
        with self._root() as temp:
            plan = resolve_plan(
                Path(temp),
                PublishRequest("StreamScapeTV/backend", SHA, SHA, "backend-image", "1.2.3", "trusted-exact"),
            )
        self.assertEqual(plan.runs_on, ("linux", "amd64", "buildah", "small"))
        target = plan.targets[0]
        self.assertEqual(target.registry_repository, "registry.example.invalid/fixtures/backend")
        self.assertEqual(target.version_reference, "registry.example.invalid/fixtures/backend:1.2.3")
        self.assertEqual(target.source_reference, f"registry.example.invalid/fixtures/backend:sha-{SHA}")
        self.assertEqual(publication_registry_host(plan.targets), "registry.example.invalid")
        self.assertNotIn("latest", json.dumps(plan.planning_outputs()))

    def test_release_version_tag_accepts_128_characters_and_rejects_129(self) -> None:
        self.assertEqual(len(MAX_OCI_TAG_VERSION), 128)
        self.assertEqual(len(OVERSIZED_OCI_TAG_VERSION), 129)
        with self._root() as temp:
            plan = resolve_plan(
                Path(temp),
                PublishRequest(
                    "StreamScapeTV/backend",
                    SHA,
                    SHA,
                    "backend-image",
                    MAX_OCI_TAG_VERSION,
                    "trusted-exact",
                ),
            )
            tag = plan.targets[0].version_reference.rsplit(":", 1)[1]
            self.assertEqual(tag, MAX_OCI_TAG_VERSION)
            self.assertEqual(len(tag), 128)
            self.assertEqual(
                plan.targets[0].source_reference.rsplit(":", 1)[1],
                "sha-" + SHA,
            )
            with self.assertRaisesRegex(OciPublishError, "invalid_version"):
                resolve_plan(
                    Path(temp),
                    PublishRequest(
                        "StreamScapeTV/backend",
                        SHA,
                        SHA,
                        "backend-image",
                        OVERSIZED_OCI_TAG_VERSION,
                        "trusted-exact",
                    ),
                )

    def test_flux_multi_target_destinations_and_handoff_ids_are_contract_owned(self) -> None:
        with self._root() as temp:
            plan = resolve_plan(
                Path(temp),
                PublishRequest("StreamScapeTV/flux", SHA, SHA, "runner-images", "2.0.0", "trusted-exact"),
            )
        self.assertEqual(
            [target.registry_repository for target in plan.targets],
            [
                "registry.example.invalid/fixtures/github-actions-runner-buildah",
                "registry.example.invalid/fixtures/github-actions-runner-mobile",
            ],
        )
        self.assertEqual(plan.canary_id, "runner-images-canary")
        self.assertEqual(plan.rollback_id, "runner-images-rollback")

    def test_publication_repositories_are_canonical_and_share_one_contract_host(self) -> None:
        invalid_repositories = (
            "Registry.example.invalid/fixtures/backend",
            "registry.example.invalid/fixtures/backend:1.2.3",
            "registry.example.invalid/fixtures/backend@sha256:" + "1" * 64,
            "registry.example.invalid/fixtures/latest",
            "registry.example.invalid/backend",
        )
        for repository in invalid_repositories:
            with self.subTest(repository=repository), self._root() as temp:
                contract_path = Path(temp) / "contracts/oci-products.json"
                contract = json.loads(contract_path.read_text(encoding="utf-8"))
                contract["products"]["backend-image"]["targets"][0][
                    "publication_repository"
                ] = repository
                contract_path.write_text(json.dumps(contract), encoding="utf-8")
                with self.assertRaisesRegex(OciPublishError, "invalid_contract"):
                    resolve_plan(
                        Path(temp),
                        PublishRequest(
                            "StreamScapeTV/backend",
                            SHA,
                            SHA,
                            "backend-image",
                            "1.2.3",
                            "trusted-exact",
                        ),
                    )

        with self._root() as temp:
            contract_path = Path(temp) / "contracts/oci-products.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["products"]["runner-images"]["targets"][1][
                "publication_repository"
            ] = "other.example.invalid/fixtures/github-actions-runner-mobile"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(OciPublishError, "invalid_contract"):
                resolve_plan(
                    Path(temp),
                    PublishRequest(
                        "StreamScapeTV/flux",
                        SHA,
                        SHA,
                        "runner-images",
                        "2.0.0",
                        "trusted-exact",
                    ),
                )

    def test_authentication_uses_only_the_contract_derived_registry_host(self) -> None:
        with self._root() as temp:
            root = Path(temp)
            plan = resolve_plan(
                root,
                PublishRequest(
                    "StreamScapeTV/backend",
                    SHA,
                    SHA,
                    "backend-image",
                    "1.2.3",
                    "trusted-exact",
                ),
            )
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            environment = {
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_RUN_ID": "17",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            capacity = build_execution._test_capacity_roots(  # noqa: SLF001
                root / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="7" * 20,
            )
            with patch("ci_workflows.oci_publish.shutil.which", return_value="/usr/bin/skopeo"), patch(
                "ci_workflows.oci_publish._run"
            ) as run:
                self.assertEqual(
                    authenticate(
                        plan,
                        environment,
                        "publisher",
                        "bounded-token",
                        _capacity_roots=capacity,
                    ),
                    {"result": "authenticated", "failure_code": ""},
                )
            self.assertEqual(run.call_args.args[0][-1], "registry.example.invalid")
            state = json.loads(
                (publication_state_root(
                    environment, _capacity_roots=capacity
                ) / "plan.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(state["registry_host"], "registry.example.invalid")
            cleanup(environment, _capacity_roots=capacity)

    def test_flux_publication_requires_independent_bootstrap(self) -> None:
        with self._root() as temp:
            contract_path = Path(temp) / "contracts/oci-products.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["products"]["runner-images"]["independent_bootstrap"] = False
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(OciPublishError, "invalid_contract"):
                resolve_plan(
                    Path(temp),
                    PublishRequest(
                        "StreamScapeTV/flux",
                        SHA,
                        SHA,
                        "runner-images",
                        "2.0.0",
                        "trusted-exact",
                    ),
                )

    def test_request_rejects_pr_source_and_authority_mismatch(self) -> None:
        base = {
            "GITHUB_REPOSITORY": "StreamScapeTV/backend",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_RELEASE_AUTHORITY_SHA": SHA,
            "INPUT_PRODUCT_ID": "backend-image",
            "INPUT_RELEASE_VERSION": "1.2.3",
        }
        with self.assertRaisesRegex(OciPublishError, "publication_untrusted"):
            request_from_environment({**base, "GITHUB_EVENT_NAME": "pull_request"})
        with self.assertRaisesRegex(OciPublishError, "release_authority_mismatch"):
            request_from_environment({**base, "GITHUB_EVENT_NAME": "push", "INPUT_RELEASE_AUTHORITY_SHA": "b" * 40})
        with self.assertRaisesRegex(OciPublishError, "invalid_version"):
            request_from_environment({**base, "GITHUB_EVENT_NAME": "push", "INPUT_RELEASE_VERSION": "1.2.3-rc.1"})
        accepted = request_from_environment(
            {
                **base,
                "GITHUB_EVENT_NAME": "push",
                "INPUT_RELEASE_VERSION": MAX_OCI_TAG_VERSION,
            }
        )
        self.assertEqual(accepted.release_version, MAX_OCI_TAG_VERSION)
        with self.assertRaisesRegex(OciPublishError, "invalid_version"):
            request_from_environment(
                {
                    **base,
                    "GITHUB_EVENT_NAME": "push",
                    "INPUT_RELEASE_VERSION": OVERSIZED_OCI_TAG_VERSION,
                }
            )


class ReplayTests(unittest.TestCase):
    def test_missing_matching_and_partial_replays(self) -> None:
        digest = "sha256:" + "1" * 64
        self.assertEqual(replay_decision(digest, None, None), (True, True, False))
        self.assertEqual(replay_decision(digest, digest, None), (False, True, True))
        self.assertEqual(replay_decision(digest, digest, digest), (False, False, True))

    def test_conflicting_immutable_reference_fails_closed(self) -> None:
        with self.assertRaisesRegex(OciPublishError, "immutable_reference_conflict"):
            replay_decision("sha256:" + "1" * 64, "sha256:" + "2" * 64, None)


class LayoutReadbackTests(unittest.TestCase):
    def _target(self) -> PublishTarget:
        return PublishTarget(
            target_id="backend",
            source_repository="StreamScapeTV/backend",
            platforms=("linux/amd64", "linux/arm64/v8"),
            registry_repository="registry.example.invalid/fixtures/backend",
            version_reference="registry.example.invalid/fixtures/backend:1.2.3",
            source_reference=f"registry.example.invalid/fixtures/backend:sha-{SHA}",
            metadata={"title": "Backend", "description": "Backend image", "licenses": "Proprietary"},
            required_user="appuser",
            required_entrypoint=(),
            required_command=("/app/start",),
            required_ports=("8080/tcp",),
        )

    def test_layout_inspection_proves_platform_config_layer_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            summary = inspect_layout(_make_layout(Path(temp), self._target()), self._target(), "validation")
        self.assertEqual(set(summary["platforms"]), {"linux/amd64", "linux/arm64/v8"})
        self.assertRegex(summary["manifest_digest"], r"^sha256:[0-9a-f]{64}$")
        for row in summary["platforms"].values():
            self.assertRegex(row["manifest_digest"], r"^sha256:[0-9a-f]{64}$")
            self.assertRegex(row["config_digest"], r"^sha256:[0-9a-f]{64}$")
            self.assertTrue(row["layer_digests"])

    def test_build_and_publication_inspection_share_256_layer_limit(self) -> None:
        target = self._target()
        with tempfile.TemporaryDirectory() as temp:
            layout = _make_layout(Path(temp), target)
            original_public_read = publication_runtime._json_blob  # noqa: SLF001

            def public_read(layout, descriptor, allowed_media):
                value = original_public_read(layout, descriptor, allowed_media)
                if "layers" in value:
                    return {
                        **value,
                        "layers": list(value["layers"]) * 257,
                    }
                return value

            with patch.object(
                publication_runtime, "_json_blob", side_effect=public_read
            ), self.assertRaisesRegex(OciPublishError, "oci_layout_malformed"):
                inspect_layout(layout, target, "validation")

            build_target = OciTarget(
                target_id=target.target_id,
                context_path=".",
                dockerfile_path="Dockerfile",
                target_stage=None,
                platforms=target.platforms,
                smoke_script=None,
                required_user=target.required_user,
                required_entrypoint=target.required_entrypoint,
                required_command=target.required_command,
                required_ports=target.required_ports,
                required_files=(),
                required_tools=(),
                forbidden_tools=(),
                fixed_build_args={},
                secret_mount_ids=(),
                build_input_lock_path="inputs.lock.json",
                input_policy_id="scratch-only-v1",
            )
            labels = {
                "dev.streamscape.product": target.target_id,
                "org.opencontainers.image.created": "2026-08-12T00:00:00Z",
                "org.opencontainers.image.description": target.metadata["description"],
                "org.opencontainers.image.licenses": target.metadata["licenses"],
                "org.opencontainers.image.revision": SHA,
                "org.opencontainers.image.source": (
                    "https://github.com/" + target.source_repository
                ),
                "org.opencontainers.image.title": target.metadata["title"],
                "org.opencontainers.image.version": "1.2.3",
            }
            original_build_read = build_execution._read_json  # noqa: SLF001

            def build_read(path):
                value = original_build_read(path)
                if isinstance(value, dict) and "layers" in value:
                    return {
                        **value,
                        "layers": list(value["layers"]) * 257,
                    }
                return value

            with patch.object(
                build_execution, "_read_json", side_effect=build_read
            ), self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                build_execution.inspect_layout(layout, build_target, labels)

    def test_layout_metadata_mismatch_is_rejected(self) -> None:
        target = self._target()
        bad = PublishTarget(**{**target.__dict__, "metadata": {**target.metadata, "title": "Wrong"}})
        with tempfile.TemporaryDirectory() as temp:
            layout = _make_layout(Path(temp), target)
            with self.assertRaisesRegex(OciPublishError, "metadata_mismatch"):
                inspect_layout(layout, bad, "validation")

    def test_unexpected_runtime_fields_are_rejected_when_contract_is_empty(self) -> None:
        target = PublishTarget(
            **{
                **self._target().__dict__,
                "required_user": None,
                "required_entrypoint": (),
                "required_command": (),
                "required_ports": (),
            }
        )
        for field, value in (
            ("User", "attacker"),
            ("Entrypoint", ["/unexpected"]),
            ("Cmd", ["/unexpected"]),
            ("ExposedPorts", {"9999/tcp": {}}),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                layout = _make_layout(Path(temp), target)
                root = json.loads((layout / "index.json").read_text(encoding="utf-8"))
                index_descriptor = root["manifests"][0]
                index_path = layout / "blobs" / "sha256" / index_descriptor["digest"].removeprefix("sha256:")
                index = json.loads(index_path.read_text(encoding="utf-8"))
                manifest_descriptor = index["manifests"][0]
                manifest_path = layout / "blobs" / "sha256" / manifest_descriptor["digest"].removeprefix("sha256:")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                config_descriptor = manifest["config"]
                config_path = layout / "blobs" / "sha256" / config_descriptor["digest"].removeprefix("sha256:")
                config = json.loads(config_path.read_text(encoding="utf-8"))
                config["config"][field] = value
                config_descriptor.update(
                    _write_blob(
                        layout,
                        json.dumps(config, sort_keys=True, separators=(",", ":")).encode(),
                    )
                )
                manifest_descriptor.update(
                    _write_blob(
                        layout,
                        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
                    )
                )
                index_descriptor.update(
                    _write_blob(
                        layout,
                        json.dumps(index, sort_keys=True, separators=(",", ":")).encode(),
                    )
                )
                (layout / "index.json").write_text(
                    json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                    inspect_layout(layout, target, "validation")

    def test_incomplete_or_inconsistent_manifest_is_rejected(self) -> None:
        target = self._target()
        mutations = (
            "missing-rootfs",
            "diff-id-count",
            "empty-layers",
            "platform-mismatch",
            "unsupported-zstd-layer",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                layout = _make_layout(Path(temp), target)
                root = json.loads((layout / "index.json").read_text(encoding="utf-8"))
                index_descriptor = root["manifests"][0]
                index_path = layout / "blobs" / "sha256" / index_descriptor["digest"].removeprefix("sha256:")
                index = json.loads(index_path.read_text(encoding="utf-8"))
                manifest_descriptor = index["manifests"][0]
                manifest_path = layout / "blobs" / "sha256" / manifest_descriptor["digest"].removeprefix("sha256:")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if mutation == "empty-layers":
                    manifest["layers"] = []
                elif mutation == "unsupported-zstd-layer":
                    manifest["layers"][0]["mediaType"] = (
                        "application/vnd.oci.image.layer.v1.tar+zstd"
                    )
                elif mutation == "platform-mismatch":
                    manifest_descriptor["platform"]["architecture"] = "arm64"
                    index_bytes = json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
                    index_descriptor.update(_write_blob(layout, index_bytes))
                    (layout / "index.json").write_text(
                        json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(OciPublishError, "oci_layout_malformed"):
                        inspect_layout(layout, target, "validation")
                    continue
                elif mutation not in {"empty-layers", "unsupported-zstd-layer"}:
                    config_descriptor = manifest["config"]
                    config_path = layout / "blobs" / "sha256" / config_descriptor["digest"].removeprefix("sha256:")
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    if mutation == "missing-rootfs":
                        config.pop("rootfs")
                    else:
                        config["rootfs"]["diff_ids"] = []
                    config_bytes = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
                    config_descriptor.update(_write_blob(layout, config_bytes))
                manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                manifest_descriptor.update(_write_blob(layout, manifest_bytes))
                index_bytes = json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
                index_descriptor.update(_write_blob(layout, index_bytes))
                (layout / "index.json").write_text(
                    json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(OciPublishError, "oci_layout_malformed"):
                    inspect_layout(layout, target, "validation")


class CleanupTests(unittest.TestCase):
    def test_production_capacity_is_fixed_and_ignores_environment_paths(self) -> None:
        roots = publication_capacity_roots(
            {
                "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
                "GITHUB_RUN_ID": "17",
                "GITHUB_RUN_ATTEMPT": "2",
                "GITHUB_JOB": "publish",
                "RUNNER_TEMP": "/attacker/selected",
                "OCI_PUBLISH_ROOT": "/attacker/selected-too",
            }
        )
        self.assertTrue(roots.production)
        self.assertEqual(roots.scratch_parent, Path("/var/tmp/buildah"))
        self.assertEqual(roots.graph_parent, Path("/var/lib/containers/storage"))
        self.assertEqual(roots.run_parent, Path("/run/containers/storage"))
        self.assertRegex(roots.leaf_name, r"^ciw-oci-publish-[0-9a-f]{20}$")
        self.assertNotIn("attacker", " ".join(map(str, roots.roots)))

    def test_cleanup_removes_all_three_marker_owned_capacity_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = build_execution._test_capacity_roots(  # noqa: SLF001
                Path(temp),
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="d" * 20,
            )
            build_execution.prepare_capacity_roots(roots)
            for root in roots.roots:
                (root / "residue").write_text("bounded", encoding="utf-8")
            cleanup({}, _capacity_roots=roots)
            self.assertTrue(all(not root.exists() for root in roots.roots))

    def test_residue_and_cleanup_track_arbitrarily_renamed_capacity_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = build_execution._test_capacity_roots(  # noqa: SLF001
                Path(temp) / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="f" * 20,
            )
            build_execution.prepare_capacity_roots(roots)
            renamed: list[Path] = []
            siblings: list[Path] = []
            for index, root in enumerate(roots.roots):
                sibling = root.parent / f"unrelated-{index}"
                sibling.write_text("keep\n", encoding="utf-8")
                siblings.append(sibling)
                destination = root.parent / f"renamed-publication-{index}"
                root.rename(destination)
                renamed.append(destination)
            (renamed[0] / "registry-auth.json").write_text(
                "publication auth residue\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(OciPublishError, "residue_detected"):
                publication_runtime.residue({}, _capacity_roots=roots)
            cleanup({}, _capacity_roots=roots)
            publication_runtime.residue({}, _capacity_roots=roots)
            self.assertTrue(all(not path.exists() for path in renamed))
            self.assertTrue(
                all(path.read_text(encoding="utf-8") == "keep\n" for path in siblings)
            )

    def test_capacity_substitution_is_unlinked_and_fails_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            roots = build_execution._test_capacity_roots(  # noqa: SLF001
                base / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="e" * 20,
            )
            build_execution.prepare_capacity_roots(roots)
            sentinel = base / "sentinel"
            sentinel.mkdir()
            (sentinel / "keep").write_text("safe", encoding="utf-8")
            shutil.rmtree(roots.graph_root)
            roots.graph_root.symlink_to(sentinel, target_is_directory=True)
            with self.assertRaisesRegex(OciPublishError, "cleanup_failed"):
                cleanup({}, _capacity_roots=roots)
            self.assertFalse(roots.graph_root.exists())

    def test_cleanup_translates_shared_capacity_scan_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = build_execution._test_capacity_roots(  # noqa: SLF001
                Path(temp) / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="1" * 20,
            )
            with patch.object(
                build_execution,
                "remove_capacity_roots",
                side_effect=OciBuildError("residue_detected"),
            ):
                with self.assertRaisesRegex(OciPublishError, "residue_detected"):
                    cleanup({}, _capacity_roots=roots)
            self.assertTrue(all(not root.exists() for root in roots.roots))

    def test_symlink_state_is_unlinked_without_deleting_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {"RUNNER_TEMP": temp, "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1"}
            target = Path(temp) / "sentinel"
            target.mkdir()
            (target / "keep").write_text("safe", encoding="utf-8")
            capacity = build_execution._test_capacity_roots(  # noqa: SLF001
                Path(temp) / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="8" * 20,
            )
            root = publication_state_root(env, _capacity_roots=capacity)
            root.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(OciPublishError, "cleanup_failed"):
                cleanup(env, _capacity_roots=capacity)
            self.assertFalse(root.exists())
            self.assertTrue((target / "keep").is_file())

    def test_regular_file_state_is_removed_while_reporting_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {"RUNNER_TEMP": temp, "GITHUB_RUN_ID": "13", "GITHUB_RUN_ATTEMPT": "1"}
            capacity = build_execution._test_capacity_roots(  # noqa: SLF001
                Path(temp) / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="9" * 20,
            )
            root = publication_state_root(env, _capacity_roots=capacity)
            root.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(OciPublishError, "cleanup_failed"):
                cleanup(env, _capacity_roots=capacity)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
