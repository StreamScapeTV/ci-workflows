from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ci_workflows import oci_publish as runtime
from ci_workflows import oci_publish_contract as public
from ci_workflows import oci_publish_guards as guards
from ci_workflows import oci_execution as build_execution
from ci_workflows.oci_input_contract import (
    OciBaseLock,
    OciBasePlatformIdentity,
    OciTargetInputLock,
)
from ci_workflows.oci_publish import OciPublishError, PublishRequest
from ci_workflows.oci_types import (
    OciBuildInputEvidence,
    OciBuildResult,
    OciResolvedBase,
    OciResolvedBasePlatform,
    OciTarget,
    oci_build_evidence_id,
)
from tests.test_oci_publication import SHA, _make_layout

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/oci-publish/oci-products.json"
PUBLISH_SHA = "be0ec9505800bb5678083fc7ce912be83a90f139"


def _resolved_inputs(
    platforms: tuple[str, ...],
    *,
    base_platforms: tuple[str, ...] | None = None,
) -> dict[str, object]:
    resolved_platforms = platforms if base_platforms is None else base_platforms
    payload = {
        "lock_digest": "sha256:" + "d" * 64,
        "input_policy_id": "oci-inputs-public-v1",
        "bases": [
            {
                "stage_id": "stage-1",
                "declared_reference": (
                    "example.invalid/runtime@sha256:" + "4" * 64
                ),
                "root_digest": "sha256:" + "4" * 64,
                "platforms": [
                    {
                        "platform": platform,
                        "manifest_digest": "sha256:" + "6" * 64,
                        "config_digest": "sha256:" + "7" * 64,
                    }
                    for platform in resolved_platforms
                ],
            }
        ],
        "external_inputs": [],
    }
    evidence_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**payload, "evidence_id": evidence_id}


def _input_lock(
    product_id: str,
    target,
    *,
    base_platforms: tuple[str, ...] | None = None,
    dockerfile_platform: str | None = None,
) -> OciTargetInputLock:
    locked_platforms = (
        target.platforms if base_platforms is None else base_platforms
    )
    return OciTargetInputLock(
        product_id=product_id,
        target_id=target.target_id,
        input_policy_id=target.input_policy_id,
        platforms=target.platforms,
        bases=(
            OciBaseLock(
                stage_id="stage-1",
                from_ordinal=1,
                stage_marker="final",
                kind="external",
                declared_reference=(
                    "example.invalid/runtime@sha256:" + "4" * 64
                ),
                dockerfile_platform=dockerfile_platform,
                platforms=locked_platforms,
                platform_identities=tuple(
                    OciBasePlatformIdentity(
                        platform=platform,
                        manifest_digest="sha256:" + "6" * 64,
                        config_digest="sha256:" + "7" * 64,
                    )
                    for platform in locked_platforms
                ),
            ),
        ),
        external_inputs=(),
        lock_digest="sha256:" + "d" * 64,
    )


def _write_exact_source_checkout(
    workspace: Path,
    product_id: str,
    target,
    *,
    base_platforms: tuple[str, ...] | None = None,
    dockerfile_platform: str | None = None,
) -> tuple[Path, str]:
    source = workspace / "source"
    lock = _input_lock(
        product_id,
        target,
        base_platforms=base_platforms,
        dockerfile_platform=dockerfile_platform,
    )
    lock_path = source / ".ciw/oci-build-inputs/backend.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(lock.canonical_payload(), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    platform_prefix = (
        f"--platform={dockerfile_platform} " if dockerfile_platform else ""
    )
    (source / "Dockerfile").write_text(
        f"FROM {platform_prefix}{lock.bases[0].declared_reference}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.email", "oci-publication@example.invalid"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "OCI Publication Test"],
        cwd=source,
        check=True,
    )
    subprocess.run(["git", "add", "--all"], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "exact source"],
        cwd=source,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    return source, sha


def _tag_listing(target, *references: str) -> guards._RepositoryTagListing:  # noqa: SLF001
    prefix = f"{target.registry_repository}:"
    return guards._RepositoryTagListing(  # noqa: SLF001
        repository=target.registry_repository,
        tags=tuple(reference.removeprefix(prefix) for reference in references),
    )


def _write_build_result(
    environment: dict[str, str],
    plan,
    manifest_digests: dict[str, str],
    resolved_inputs: dict[str, dict[str, object]] | None = None,
) -> None:
    root = runtime.build_state_root(environment)
    resolved = resolved_inputs or {
        target.target_id: _resolved_inputs(target.platforms)
        for target in plan.targets
    }
    indexes: dict[str, str] = {}
    platforms: dict[str, list[dict[str, object]]] = {}
    targets: list[dict[str, object]] = []
    for target in plan.targets:
        layout = root / "layouts" / target.target_id
        local = guards.inspect_layout(layout, target, "validation")
        rows = [
            {
                "platform": platform,
                "manifest_digest": local["platforms"][platform][
                    "manifest_digest"
                ],
                "config_digest": local["platforms"][platform][
                    "config_digest"
                ],
                "layer_digests": local["platforms"][platform][
                    "layer_digests"
                ],
            }
            for platform in sorted(local["platforms"])
        ]
        labels = local["platforms"][target.platforms[0]]["labels"]
        index_digest = build_execution.sha256_file(layout / "index.json")
        indexes[target.target_id] = index_digest
        platforms[target.target_id] = rows
        targets.append(
            {
                "target_id": target.target_id,
                "index_digest": index_digest,
                "publication_manifest_digest": manifest_digests[target.target_id],
                "platform_results": rows,
                "labels": labels,
                "smoke_result": "inspection-passed",
                "build_input_evidence": resolved[target.target_id],
            }
        )
    evidence_id = oci_build_evidence_id(
        plan.admitted_sha,
        plan.product_id,
        plan.release_version,
        targets,
        plan.canary_id,
        plan.previous_known_good,
        plan.rollback_id,
    )
    payload = {
        "result": "success",
        "source_sha": plan.admitted_sha,
        "product_id": plan.product_id,
        "release_version": plan.release_version,
        "manifest_digests_json": json.dumps(
            indexes, sort_keys=True, separators=(",", ":")
        ),
        "publication_manifest_digests_json": json.dumps(
            manifest_digests, sort_keys=True, separators=(",", ":")
        ),
        "resolved_inputs_json": json.dumps(
            resolved,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "platform_results_json": json.dumps(
            platforms, sort_keys=True, separators=(",", ":")
        ),
        "target_results_json": json.dumps(
            targets, sort_keys=True, separators=(",", ":")
        ),
        "clean_tree": "true",
        "cleanup_result": "not-run",
        "artifact_exception_used": "false",
        "evidence_id": evidence_id,
        "canary_id": plan.canary_id or "",
        "previous_known_good": plan.previous_known_good or "",
        "rollback_id": plan.rollback_id or "",
        "failure_code": "",
    }
    (root / "result.json").write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "result.json").chmod(0o600)


def _install_capacity_resolution(
    test: unittest.TestCase, root: Path
) -> tuple[build_execution.CapacityRoots, build_execution.CapacityRoots]:
    """Inject immutable test capacity without granting environment path control."""

    build = build_execution._test_capacity_roots(  # noqa: SLF001
        root / "build-capacity", token="1" * 20
    )
    publication = build_execution._test_capacity_roots(  # noqa: SLF001
        root / "publication-capacity",
        domain="oci-publish",
        prefix="ciw-oci-publish",
        token="2" * 20,
    )

    def resolve(_environment, *, domain="oci-build", prefix="ciw-oci"):
        return publication if domain == "oci-publish" else build

    patcher = patch.object(build_execution, "build_capacity_roots", side_effect=resolve)
    patcher.start()
    test.addCleanup(patcher.stop)
    return build, publication


class GuardedPublicationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build_capacity, _publication_capacity = _install_capacity_resolution(
            self, self.root
        )
        build_execution.prepare_capacity_roots(self.build_capacity)
        (self.root / "contracts").mkdir()
        shutil.copyfile(FIXTURE, self.root / "contracts/oci-products.json")
        self.plan = runtime.resolve_plan(
            self.root,
            PublishRequest(
                "StreamScapeTV/backend",
                SHA,
                SHA,
                "backend-image",
                "1.2.3",
                "trusted-exact",
            ),
        )
        self.input_locks = {
            target.target_id: _input_lock(self.plan.product_id, target)
            for target in self.plan.targets
        }
        self.validate_source_locks = guards._validated_source_input_locks  # noqa: SLF001
        source_lock_patcher = patch.object(
            guards,
            "_validated_source_input_locks",
            return_value=(self.root, self.input_locks),
        )
        exact_source_patcher = patch.object(guards, "_assert_exact_source")
        source_lock_patcher.start()
        exact_source_patcher.start()
        self.addCleanup(source_lock_patcher.stop)
        self.addCleanup(exact_source_patcher.stop)
        self.env = {
            "RUNNER_TEMP": str(self.root / "runner-temp"),
            "GITHUB_RUN_ID": "9001",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
        }
        Path(self.env["RUNNER_TEMP"]).mkdir()
        self.build_layout = runtime.build_state_root(self.env) / "layouts" / "backend"
        self.build_layout.parent.mkdir(parents=True)
        created = _make_layout(self.root / "source-layout", self.plan.targets[0])
        shutil.copytree(created, self.build_layout)
        self.local = guards.inspect_layout(
            self.build_layout, self.plan.targets[0], "validation"
        )
        _write_build_result(
            self.env,
            self.plan,
            {"backend": str(self.local["manifest_digest"])},
        )
        publication_capacity = runtime.publication_capacity_roots(self.env)
        build_execution.prepare_capacity_roots(publication_capacity)
        runtime._prepare_publication_runtime(publication_capacity)  # noqa: SLF001
        publication_root = publication_capacity.scratch_root
        (publication_root / "registry-auth.json").write_text("{}\n", encoding="utf-8")
        (publication_root / "registry-auth.json").chmod(0o600)
        runtime._write_publication_plan_state(  # noqa: SLF001
            self.plan, publication_capacity
        )

    def test_layout_marker_is_required_before_registry_parity(self) -> None:
        marker = self.build_layout / "oci-layout"
        marker.write_text('{"imageLayoutVersion":"9.9"}\n', encoding="utf-8")
        with self.assertRaisesRegex(OciPublishError, "oci_layout_malformed"):
            guards.inspect_layout(self.build_layout, self.plan.targets[0], "validation")

    def test_layout_inspection_streams_blob_digest_validation(self) -> None:
        with patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("layout blobs must not be read all at once"),
        ):
            inspected = guards.inspect_layout(
                self.build_layout, self.plan.targets[0], "validation"
            )

        self.assertEqual(inspected, self.local)

    def test_fixed_platform_multiarch_build_result_publishes_with_exact_lock_binding(self) -> None:
        publish_target = self.plan.targets[0]
        fixed_platforms = (publish_target.platforms[0],)
        self.input_locks[publish_target.target_id] = _input_lock(
            self.plan.product_id,
            publish_target,
            base_platforms=fixed_platforms,
            dockerfile_platform=fixed_platforms[0],
        )
        build_target = OciTarget(
            target_id=publish_target.target_id,
            context_path=".",
            dockerfile_path="Dockerfile",
            target_stage=None,
            platforms=publish_target.platforms,
            smoke_script=None,
            required_user=publish_target.required_user,
            required_entrypoint=publish_target.required_entrypoint,
            required_command=publish_target.required_command,
            required_ports=publish_target.required_ports,
            required_files=(),
            required_tools=(),
            forbidden_tools=(),
            fixed_build_args={},
            secret_mount_ids=(),
            build_input_lock_path=".ciw/oci-build-inputs/backend.json",
            input_policy_id="oci-inputs-public-v1",
        )
        labels = next(iter(self.local["platforms"].values()))["labels"]
        built = replace(
            build_execution.inspect_layout(
                self.build_layout,
                build_target,
                labels,
            ),
            smoke_result="inspection-passed",
            build_input_evidence=OciBuildInputEvidence(
                lock_digest="sha256:" + "d" * 64,
                acquisition_policy_id="oci-inputs-public-v1",
                resolved_bases=(
                    OciResolvedBase(
                        stage_id="stage-1",
                        declared_reference=(
                            "example.invalid/runtime@sha256:" + "4" * 64
                        ),
                        root_digest="sha256:" + "4" * 64,
                        platforms=tuple(
                            OciResolvedBasePlatform(
                                platform,
                                "sha256:" + "6" * 64,
                                "sha256:" + "7" * 64,
                            )
                            for platform in fixed_platforms
                        ),
                    ),
                ),
                resolved_external_inputs=(),
                evidence_id=str(
                    _resolved_inputs(
                        publish_target.platforms,
                        base_platforms=fixed_platforms,
                    )["evidence_id"]
                ),
            ),
        )
        self.assertNotEqual(built.index_digest, built.publication_manifest_digest)
        self.assertEqual(
            built.publication_manifest_digest,
            self.local["manifest_digest"],
        )
        result = OciBuildResult(
            product_id=self.plan.product_id,
            admitted_sha=self.plan.admitted_sha,
            release_version=self.plan.release_version,
            source_date_epoch=1,
            targets=(built,),
            clean_tree=True,
            cleanup_result="not-run",
            evidence_id="",
            canary_id=None,
            previous_known_good=None,
            rollback_id=None,
        )
        build_root = runtime.build_state_root(self.env)
        result = replace(
            result,
            evidence_id=oci_build_evidence_id(
                result.admitted_sha,
                result.product_id,
                result.release_version,
                result.targets,
                result.canary_id,
                result.previous_known_good,
                result.rollback_id,
            ),
        )
        build_execution._write_build_result_file(  # noqa: SLF001
            build_root / "result.json", result
        )

        with patch.object(
            guards,
            "_list_repository_tags",
            return_value=_tag_listing(
                publish_target,
                publish_target.version_reference,
                publish_target.source_reference,
            ),
        ), patch.object(
            guards,
            "_inspect_remote_digest",
            return_value=self.local["manifest_digest"],
        ), patch.object(runtime, "_copy") as copy:
            guards.publish(
                self.plan,
                self.env,
                allow_publish=False,
                repository_root=self.root,
            )

        copy.assert_not_called()

    def test_exact_source_fixed_platform_lock_is_loaded_and_revalidated(self) -> None:
        target = self.plan.targets[0]
        fixed_platforms = (target.platforms[0],)
        workspace = self.root / "exact-workspace"
        _source, sha = _write_exact_source_checkout(
            workspace,
            self.plan.product_id,
            target,
            base_platforms=fixed_platforms,
            dockerfile_platform=fixed_platforms[0],
        )
        exact_plan = replace(self.plan, admitted_sha=sha)
        source_root, locks = self.validate_source_locks(
            self.root,
            {"GITHUB_WORKSPACE": str(workspace)},
            exact_plan,
        )

        self.assertEqual(source_root, (workspace / "source").resolve())
        self.assertEqual(locks[target.target_id].platforms, target.platforms)
        self.assertEqual(
            locks[target.target_id].bases[0].platforms, fixed_platforms
        )
        self.assertEqual(
            locks[target.target_id].bases[0].dockerfile_platform,
            fixed_platforms[0],
        )

    def test_exact_source_binding_rejects_clean_tracked_lock_symlink(self) -> None:
        target = self.plan.targets[0]
        workspace = self.root / "symlink-workspace"
        source, _sha = _write_exact_source_checkout(
            workspace, self.plan.product_id, target
        )
        lock_path = source / ".ciw/oci-build-inputs/backend.json"
        external = self.root / "outside-input-lock.json"
        external.write_bytes(lock_path.read_bytes())
        lock_path.unlink()
        lock_path.symlink_to(external)
        subprocess.run(["git", "add", "--all"], cwd=source, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "tracked symlink"],
            cwd=source,
            check=True,
        )
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

        with self.assertRaisesRegex(OciPublishError, "build_evidence_mismatch"):
            self.validate_source_locks(
                self.root,
                {"GITHUB_WORKSPACE": str(workspace)},
                replace(self.plan, admitted_sha=sha),
            )

    def test_generic_not_found_is_not_proof_of_manifest_absence(self) -> None:
        generic = subprocess.CompletedProcess(
            args=["skopeo"], returncode=1, stdout=b"", stderr=b"network endpoint not found"
        )
        explicit = subprocess.CompletedProcess(
            args=["skopeo"], returncode=1, stdout=b"", stderr=b"manifest unknown"
        )
        capacity = runtime.publication_capacity_roots(self.env)
        with patch.object(runtime, "_run", return_value=generic):
            with self.assertRaisesRegex(OciPublishError, "registry_inspection_failed"):
                guards._inspect_remote_digest(
                    "ghcr.io/example/image:1.0.0", Path("auth"), capacity
                )
        with patch.object(runtime, "_run", return_value=explicit):
            self.assertIsNone(
                guards._inspect_remote_digest(
                    "ghcr.io/example/image:1.0.0", Path("auth"), capacity
                )
            )

    def test_authenticated_tag_listing_preserves_exact_repository_and_tags(self) -> None:
        target = self.plan.targets[0]
        version_tag = target.version_reference.rsplit(":", 1)[1]
        source_tag = target.source_reference.rsplit(":", 1)[1]
        child = subprocess.CompletedProcess(
            args=["skopeo", "list-tags"],
            returncode=0,
            stdout=json.dumps(
                {
                    "Repository": target.registry_repository,
                    "Tags": [version_tag, source_tag, "older"],
                }
            ).encode(),
            stderr=b"",
        )
        capacity = runtime.publication_capacity_roots(self.env)
        authfile = guards.publication_state_root(self.env) / "registry-auth.json"

        with patch.object(runtime, "_run", return_value=child) as run:
            listing = guards._list_repository_tags(  # noqa: SLF001
                target, authfile, capacity
            )

        self.assertTrue(listing.contains_reference(target.version_reference))
        self.assertTrue(listing.contains_reference(target.source_reference))
        run.assert_called_once_with(
            [
                "skopeo",
                "list-tags",
                "--authfile",
                str(authfile),
                f"docker://{target.registry_repository}",
            ],
            check=False,
            capacity_roots=capacity,
            stdout_limit=guards._MAXIMUM_TAG_LIST_BYTES,  # noqa: SLF001
            stderr_limit=runtime._MAX_REGISTRY_INSPECTION_STDERR_BYTES,  # noqa: SLF001
            overflow_code="registry_inspection_failed",
            expected_auth_state=None,
        )

    def test_tag_listing_failures_are_zero_copy_and_zero_inspect(self) -> None:
        target = self.plan.targets[0]
        source_tag = target.source_reference.rsplit(":", 1)[1]
        cases = {
            "auth": subprocess.CompletedProcess(
                ["skopeo", "list-tags"], 1, b"", b"unauthorized"
            ),
            "transient": subprocess.CompletedProcess(
                ["skopeo", "list-tags"], 2, b"", b"temporarily unavailable"
            ),
            "malformed": subprocess.CompletedProcess(
                ["skopeo", "list-tags"], 0, b"{", b""
            ),
            "wrong-repository": subprocess.CompletedProcess(
                ["skopeo", "list-tags"],
                0,
                json.dumps(
                    {"Repository": "registry.example.invalid/other", "Tags": []}
                ).encode(),
                b"",
            ),
            "duplicate": subprocess.CompletedProcess(
                ["skopeo", "list-tags"],
                0,
                json.dumps(
                    {
                        "Repository": target.registry_repository,
                        "Tags": [source_tag, source_tag],
                    }
                ).encode(),
                b"",
            ),
            "duplicate-json-key": subprocess.CompletedProcess(
                ["skopeo", "list-tags"],
                0,
                (
                    '{"Repository":"'
                    + target.registry_repository
                    + '","Tags":[],"Tags":[]}'
                ).encode(),
                b"",
            ),
            "case-ambiguous": subprocess.CompletedProcess(
                ["skopeo", "list-tags"],
                0,
                json.dumps(
                    {
                        "Repository": target.registry_repository,
                        "Tags": [source_tag.upper()],
                    }
                ).encode(),
                b"",
            ),
        }
        for name, child in cases.items():
            with self.subTest(name=name), patch.object(
                runtime, "_run", return_value=child
            ) as run, patch.object(
                guards, "_inspect_remote_digest"
            ) as inspect, patch.object(
                runtime, "_copy"
            ) as copy:
                with self.assertRaisesRegex(
                    OciPublishError, "registry_inspection_failed"
                ):
                    guards.publish(
                        self.plan,
                        self.env,
                        allow_publish=False,
                        repository_root=self.root,
                    )
                run.assert_called_once()
                inspect.assert_not_called()
                copy.assert_not_called()

    def test_build_capacity_marker_failures_precede_inspection_and_registry_io(self) -> None:
        marker = self.build_capacity.graph_root / ".ciw-capacity-root.json"
        original_marker = marker.read_bytes()
        sentinel = self.root / "build-capacity-substitution-target"
        sentinel.mkdir()
        (sentinel / "keep").write_text("safe", encoding="utf-8")

        endpoints = (
            (
                "guarded",
                lambda: guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                ),
            ),
            ("direct", lambda: runtime.publish(self.plan, self.env)),
        )
        for endpoint_name, endpoint in endpoints:
            for mutation in ("missing-marker", "corrupt-marker", "substituted-leaf"):
                backup = self.build_capacity.graph_parent / (
                    self.build_capacity.leaf_name + "-backup"
                )
                if mutation == "missing-marker":
                    marker.unlink()
                elif mutation == "corrupt-marker":
                    marker.write_text("{}\n", encoding="utf-8")
                else:
                    self.build_capacity.graph_root.rename(backup)
                    self.build_capacity.graph_root.symlink_to(
                        sentinel, target_is_directory=True
                    )
                try:
                    with self.subTest(endpoint=endpoint_name, mutation=mutation), patch.object(
                        guards, "inspect_layout"
                    ) as guarded_inspect, patch.object(
                        runtime, "inspect_layout"
                    ) as direct_inspect, patch.object(
                        guards, "_inspect_remote_digest"
                    ) as guarded_remote, patch.object(
                        runtime, "_inspect_remote_digest"
                    ) as direct_remote, patch.object(
                        runtime, "_copy"
                    ) as copy:
                        with self.assertRaisesRegex(
                            OciPublishError,
                            "capacity_(?:marker|root)_invalid",
                        ):
                            endpoint()
                        guarded_inspect.assert_not_called()
                        direct_inspect.assert_not_called()
                        guarded_remote.assert_not_called()
                        direct_remote.assert_not_called()
                        copy.assert_not_called()
                finally:
                    if mutation == "substituted-leaf":
                        self.build_capacity.graph_root.unlink()
                        backup.rename(self.build_capacity.graph_root)
                    else:
                        marker.write_bytes(original_marker)
                        marker.chmod(0o600)
        self.assertTrue((sentinel / "keep").is_file())

    def test_privileged_phases_reject_unbound_auth_plan_before_registry_io(self) -> None:
        capacity = runtime.publication_capacity_roots(self.env)
        state_path = capacity.scratch_root / "plan.json"
        authfile = capacity.scratch_root / "registry-auth.json"
        original_state = state_path.read_bytes()
        original_authfile = authfile.read_bytes()
        sentinel = self.root / "substituted-plan.json"
        sentinel.write_bytes(original_state)
        sentinel.chmod(0o600)
        auth_sentinel = self.root / "substituted-auth.json"
        auth_sentinel.write_bytes(original_authfile)
        auth_sentinel.chmod(0o600)

        endpoints = (
            (
                "guarded-publish",
                lambda: guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                ),
            ),
            ("direct-publish", lambda: runtime.publish(self.plan, self.env)),
            (
                "guarded-read-back",
                lambda: guards.read_back(
                    self.plan, self.env, repository_root=self.root
                ),
            ),
            ("direct-read-back", lambda: runtime.read_back(self.plan, self.env)),
            ("verify", lambda: runtime.verify(self.plan, self.env)),
            ("public-verify", lambda: public.verify(self.plan, self.env)),
        )
        for endpoint_name, endpoint in endpoints:
            for mutation in (
                "missing",
                "corrupt",
                "mode-mismatch",
                "plan-mismatch",
                "source-mismatch",
                "version-mismatch",
                "registry-host-mismatch",
                "repository-mismatch",
                "capacity-mismatch",
                "authfile-mismatch",
                "authfile-missing",
                "authfile-corrupt",
                "authfile-mode-mismatch",
                "authfile-substituted",
                "substituted",
            ):
                backup = capacity.scratch_root / "plan.json.backup"
                auth_backup = capacity.scratch_root / "registry-auth.json.backup"
                if mutation == "missing":
                    state_path.unlink()
                elif mutation == "corrupt":
                    state_path.write_bytes(b"{corrupt\n")
                elif mutation == "mode-mismatch":
                    state_path.chmod(0o640)
                elif mutation == "authfile-mismatch":
                    authfile.write_text(
                        '{"auths":{"registry.example.invalid":{"auth":"changed"}}}\n',
                        encoding="utf-8",
                    )
                    authfile.chmod(0o600)
                elif mutation == "authfile-missing":
                    authfile.unlink()
                elif mutation == "authfile-corrupt":
                    authfile.write_bytes(b"{corrupt\n")
                    authfile.chmod(0o600)
                elif mutation == "authfile-mode-mismatch":
                    authfile.chmod(0o640)
                elif mutation == "authfile-substituted":
                    authfile.rename(auth_backup)
                    authfile.symlink_to(auth_sentinel)
                elif mutation == "substituted":
                    state_path.rename(backup)
                    state_path.symlink_to(sentinel)
                else:
                    payload = json.loads(original_state)
                    if mutation == "plan-mismatch":
                        payload["product_id"] = "another-product"
                    elif mutation == "source-mismatch":
                        payload["source"]["sha"] = "f" * 40
                    elif mutation == "version-mismatch":
                        payload["release_version"] = "9.9.9"
                    elif mutation == "registry-host-mismatch":
                        payload["registry_host"] = "other.example.invalid"
                    elif mutation == "repository-mismatch":
                        payload["repositories"]["backend"] = (
                            "registry.example.invalid/fixtures/other"
                        )
                    else:
                        payload["capacity"]["token"] = "f" * 20
                    state_path.write_text(
                        json.dumps(payload, sort_keys=True, separators=(",", ":"))
                        + "\n",
                        encoding="utf-8",
                    )
                if mutation not in {"missing", "substituted", "mode-mismatch"}:
                    state_path.chmod(0o600)
                try:
                    if mutation == "authfile-missing":
                        expected_code = "registry_auth_missing"
                    elif (
                        mutation.startswith("authfile-")
                        and mutation != "authfile-mismatch"
                    ):
                        expected_code = "registry_auth_invalid"
                    else:
                        expected_code = "publication_state_missing"
                    with self.subTest(
                        endpoint=endpoint_name, mutation=mutation
                    ), patch.object(
                        guards, "_list_repository_tags"
                    ) as list_tags, patch.object(
                        guards, "inspect_layout"
                    ) as guarded_inspect, patch.object(
                        runtime, "inspect_layout"
                    ) as direct_inspect, patch.object(
                        guards, "_inspect_remote_digest"
                    ) as guarded_remote, patch.object(
                        runtime, "_inspect_remote_digest"
                    ) as direct_remote, patch.object(
                        runtime, "_copy"
                    ) as copy, patch.object(
                        runtime, "_run"
                    ) as run, patch.object(
                        Path,
                        "read_bytes",
                        side_effect=AssertionError(
                            "privileged state must be consumed from one descriptor"
                        ),
                    ):
                        with self.assertRaisesRegex(
                            OciPublishError, expected_code
                        ):
                            endpoint()
                        list_tags.assert_not_called()
                        guarded_inspect.assert_not_called()
                        direct_inspect.assert_not_called()
                        guarded_remote.assert_not_called()
                        direct_remote.assert_not_called()
                        copy.assert_not_called()
                        run.assert_not_called()
                finally:
                    if mutation == "substituted":
                        state_path.unlink()
                        backup.rename(state_path)
                    elif mutation != "authfile-mismatch":
                        state_path.write_bytes(original_state)
                        state_path.chmod(0o600)
                    if mutation == "authfile-substituted":
                        authfile.unlink()
                        auth_backup.rename(authfile)
                    elif mutation.startswith("authfile-"):
                        authfile.write_bytes(original_authfile)
                        authfile.chmod(0o600)

        self.assertEqual(state_path.read_bytes(), original_state)
        self.assertEqual(authfile.read_bytes(), original_authfile)
        self.assertEqual(sentinel.read_bytes(), original_state)
        self.assertEqual(auth_sentinel.read_bytes(), original_authfile)

    def test_secure_state_read_rejects_path_replacement_after_open(self) -> None:
        capacity = runtime.publication_capacity_roots(self.env)
        state_path = capacity.scratch_root / "plan.json"
        original = state_path.read_bytes()
        moved = capacity.scratch_root / "original-plan.json"
        real_read = os.read
        replaced = False

        def replace_path_after_open(descriptor: int, size: int) -> bytes:
            nonlocal replaced
            if not replaced:
                replaced = True
                state_path.rename(moved)
                state_path.write_bytes(b"{substituted\n")
                state_path.chmod(0o600)
            return real_read(descriptor, size)

        try:
            with patch.object(runtime.os, "read", side_effect=replace_path_after_open):
                with self.assertRaisesRegex(
                    OciPublishError, "publication_state_missing"
                ):
                    runtime._read_secure_state_file(  # noqa: SLF001
                        state_path,
                        runtime._MAX_PLAN_STATE_BYTES,  # noqa: SLF001
                        "publication_state_missing",
                    )
        finally:
            if moved.exists():
                state_path.unlink(missing_ok=True)
                moved.rename(state_path)

        self.assertTrue(replaced)
        self.assertEqual(state_path.read_bytes(), original)

    def test_phase_state_write_is_exclusive_nofollow_bounded_and_durable(self) -> None:
        capacity = runtime.publication_capacity_roots(self.env)
        state_path = capacity.scratch_root / "publication.json"
        sentinel = self.root / "external-state.json"
        sentinel.write_bytes(b"keep\n")
        sentinel.chmod(0o640)

        state_path.symlink_to(sentinel)
        with self.assertRaisesRegex(OciPublishError, "publication_state_missing"):
            runtime._write_state(  # noqa: SLF001
                capacity, "publication.json", {"targets": {}}
            )
        self.assertEqual(sentinel.read_bytes(), b"keep\n")
        self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), 0o640)
        state_path.unlink()

        with patch.object(runtime.os, "fsync", wraps=os.fsync) as fsync:
            runtime._write_state(  # noqa: SLF001
                capacity, "publication.json", {"targets": {}}
            )
        self.assertGreaterEqual(fsync.call_count, 2)
        self.assertEqual(state_path.read_bytes(), b'{"targets":{}}\n')
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)

        with self.assertRaisesRegex(OciPublishError, "publication_state_missing"):
            runtime._write_state(  # noqa: SLF001
                capacity, "publication.json", {"targets": {"changed": {}}}
            )
        self.assertEqual(state_path.read_bytes(), b'{"targets":{}}\n')
        state_path.unlink()

        oversized = {"value": "x" * runtime._MAX_PHASE_STATE_BYTES}  # noqa: SLF001
        with self.assertRaisesRegex(OciPublishError, "publication_state_missing"):
            runtime._write_state(  # noqa: SLF001
                capacity, "publication.json", oversized
            )
        self.assertFalse(state_path.exists())

        real_write = os.write
        moved = self.root / "partially-written-state.json"
        substituted = False

        def substitute_after_open(descriptor: int, value: bytes) -> int:
            nonlocal substituted
            if not substituted:
                substituted = True
                state_path.rename(moved)
                state_path.symlink_to(sentinel)
            return real_write(descriptor, value)

        try:
            with patch.object(runtime.os, "write", side_effect=substitute_after_open):
                with self.assertRaisesRegex(
                    OciPublishError, "publication_state_missing"
                ):
                    runtime._write_state(  # noqa: SLF001
                        capacity, "publication.json", {"targets": {}}
                    )
        finally:
            state_path.unlink(missing_ok=True)
            moved.unlink(missing_ok=True)
        self.assertTrue(substituted)
        self.assertEqual(sentinel.read_bytes(), b"keep\n")
        self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), 0o640)

    def test_phase_state_destinations_reject_residue_before_registry_io(self) -> None:
        capacity = runtime.publication_capacity_roots(self.env)
        sentinel = self.root / "external-phase-state.json"
        sentinel.write_bytes(b"keep\n")
        sentinel.chmod(0o640)

        for state_name, endpoint in (
            (
                "publication.json",
                lambda: guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                ),
            ),
            (
                "readback.json",
                lambda: guards.read_back(
                    self.plan, self.env, repository_root=self.root
                ),
            ),
        ):
            state_path = capacity.scratch_root / state_name
            state_path.symlink_to(sentinel)
            try:
                with self.subTest(state=state_name), patch.object(
                    guards, "_list_repository_tags"
                ) as list_tags, patch.object(
                    runtime, "_copy"
                ) as copy, patch.object(
                    guards, "_inspect_remote_digest"
                ) as inspect:
                    with self.assertRaisesRegex(
                        OciPublishError, "publication_state_missing"
                    ):
                        endpoint()
                    list_tags.assert_not_called()
                    inspect.assert_not_called()
                    copy.assert_not_called()
            finally:
                state_path.unlink()

        self.assertEqual(sentinel.read_bytes(), b"keep\n")
        self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), 0o640)

    def test_phase_state_rejects_substituted_scratch_root_parent(self) -> None:
        capacity = runtime.publication_capacity_roots(self.env)
        scratch_root = capacity.scratch_root
        moved_root = scratch_root.parent / "renamed-owned-scratch"
        outside = self.root / "outside-state-root"
        outside.mkdir(mode=0o700)
        crafted = outside / "publication.json"
        crafted.write_bytes(b'{"targets":{}}\n')
        crafted.chmod(0o600)

        runtime._validate_active_publication_capacity(capacity)  # noqa: SLF001
        scratch_root.rename(moved_root)
        scratch_root.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaisesRegex(
                OciPublishError, "capacity_root_invalid"
            ):
                runtime._write_state(  # noqa: SLF001
                    capacity, "readback.json", {"targets": {}}
                )
            self.assertFalse((outside / "readback.json").exists())
            with self.assertRaisesRegex(
                OciPublishError, "capacity_root_invalid"
            ):
                runtime._read_state(  # noqa: SLF001
                    capacity, "publication.json"
                )
        finally:
            scratch_root.unlink()
            moved_root.rename(scratch_root)

        self.assertEqual(crafted.read_bytes(), b'{"targets":{}}\n')
        self.assertEqual(stat.S_IMODE(crafted.stat().st_mode), 0o600)

    def test_phase_state_rejects_scratch_root_replacement_during_io(self) -> None:
        capacity = runtime.publication_capacity_roots(self.env)
        scratch_root = capacity.scratch_root
        moved_root = scratch_root.parent / "renamed-during-state-io"
        outside = self.root / "outside-during-state-io"
        outside.mkdir(mode=0o700)
        real_write = os.write
        replaced = False

        def replace_root_during_write(descriptor: int, value: bytes) -> int:
            nonlocal replaced
            if not replaced:
                replaced = True
                scratch_root.rename(moved_root)
                scratch_root.symlink_to(outside, target_is_directory=True)
            return real_write(descriptor, value)

        try:
            with patch.object(
                runtime.os, "write", side_effect=replace_root_during_write
            ):
                with self.assertRaisesRegex(
                    OciPublishError, "capacity_root_invalid"
                ):
                    runtime._write_state(  # noqa: SLF001
                        capacity, "publication.json", {"targets": {}}
                    )
        finally:
            if moved_root.exists():
                scratch_root.unlink(missing_ok=True)
                moved_root.rename(scratch_root)
        self.assertTrue(replaced)
        self.assertFalse((outside / "publication.json").exists())
        (scratch_root / "publication.json").unlink(missing_ok=True)

        runtime._write_state(  # noqa: SLF001
            capacity, "publication.json", {"targets": {}}
        )
        state_info = (scratch_root / "publication.json").stat()
        real_read = os.read
        replaced = False

        def replace_root_during_read(descriptor: int, size: int) -> bytes:
            nonlocal replaced
            info = os.fstat(descriptor)
            if not replaced and (info.st_dev, info.st_ino) == (
                state_info.st_dev,
                state_info.st_ino,
            ):
                replaced = True
                scratch_root.rename(moved_root)
                scratch_root.symlink_to(outside, target_is_directory=True)
            return real_read(descriptor, size)

        try:
            with patch.object(
                runtime.os, "read", side_effect=replace_root_during_read
            ):
                with self.assertRaisesRegex(
                    OciPublishError, "capacity_root_invalid"
                ):
                    runtime._read_state(  # noqa: SLF001
                        capacity, "publication.json"
                    )
        finally:
            if moved_root.exists():
                scratch_root.unlink(missing_ok=True)
                moved_root.rename(scratch_root)
            (scratch_root / "publication.json").unlink(missing_ok=True)
        self.assertTrue(replaced)

    def test_phase_state_consumers_reject_unsafe_files_before_registry_io(self) -> None:
        capacity = runtime.publication_capacity_roots(self.env)
        root = capacity.scratch_root
        target = self.plan.targets[0]
        digest = str(self.local["manifest_digest"])
        existing = _tag_listing(
            target, target.version_reference, target.source_reference
        )
        with patch.object(
            guards, "_list_repository_tags", return_value=existing
        ), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            guards.publish(
                self.plan,
                self.env,
                allow_publish=False,
                repository_root=self.root,
            )
        publication_path = root / "publication.json"
        original = publication_path.read_bytes()
        sentinel = self.root / "external-publication-state.json"
        sentinel.write_bytes(original)
        sentinel.chmod(0o600)

        for mutation in (
            "missing",
            "symlink",
            "directory",
            "mode",
            "oversize",
            "noncanonical",
        ):
            backup = root / "publication.json.backup"
            publication_path.rename(backup)
            if mutation == "symlink":
                publication_path.symlink_to(sentinel)
            elif mutation == "directory":
                publication_path.mkdir(mode=0o700)
            elif mutation == "mode":
                publication_path.write_bytes(original)
                publication_path.chmod(0o640)
            elif mutation == "oversize":
                publication_path.write_bytes(
                    b"x" * (runtime._MAX_PHASE_STATE_BYTES + 1)  # noqa: SLF001
                )
                publication_path.chmod(0o600)
            elif mutation == "noncanonical":
                publication_path.write_bytes(b'{"targets": {}}\n')
                publication_path.chmod(0o600)
            try:
                with self.subTest(mutation=mutation), patch.object(
                    runtime, "_copy"
                ) as copy, patch.object(
                    guards, "_inspect_remote_digest"
                ) as inspect:
                    with self.assertRaisesRegex(
                        OciPublishError, "publication_state_missing"
                    ):
                        guards.read_back(
                            self.plan, self.env, repository_root=self.root
                        )
                    inspect.assert_not_called()
                    copy.assert_not_called()
            finally:
                if publication_path.is_dir() and not publication_path.is_symlink():
                    publication_path.rmdir()
                else:
                    publication_path.unlink(missing_ok=True)
                backup.rename(publication_path)

        readback_path = root / "readback.json"
        for mutation in (
            "missing",
            "symlink",
            "directory",
            "mode",
            "oversize",
            "noncanonical",
        ):
            if mutation == "symlink":
                readback_path.symlink_to(sentinel)
            elif mutation == "directory":
                readback_path.mkdir(mode=0o700)
            elif mutation == "mode":
                readback_path.write_bytes(original)
                readback_path.chmod(0o640)
            elif mutation == "oversize":
                readback_path.write_bytes(
                    b"x" * (runtime._MAX_PHASE_STATE_BYTES + 1)  # noqa: SLF001
                )
                readback_path.chmod(0o600)
            elif mutation == "noncanonical":
                readback_path.write_bytes(b'{"targets": {}}\n')
                readback_path.chmod(0o600)
            try:
                with self.subTest(verify_mutation=mutation):
                    with self.assertRaisesRegex(
                        OciPublishError, "publication_state_missing"
                    ):
                        runtime.verify(self.plan, self.env)
            finally:
                if readback_path.is_dir() and not readback_path.is_symlink():
                    readback_path.rmdir()
                else:
                    readback_path.unlink(missing_ok=True)

        self.assertEqual(sentinel.read_bytes(), original)

    def test_publication_state_path_substitution_during_read_fails_closed(self) -> None:
        capacity = runtime.publication_capacity_roots(self.env)
        root = capacity.scratch_root
        target = self.plan.targets[0]
        digest = str(self.local["manifest_digest"])
        existing = _tag_listing(
            target, target.version_reference, target.source_reference
        )
        with patch.object(
            guards, "_list_repository_tags", return_value=existing
        ), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            guards.publish(
                self.plan,
                self.env,
                allow_publish=False,
                repository_root=self.root,
            )

        state_path = root / "publication.json"
        original_info = state_path.stat()
        moved = root / "original-publication.json"
        real_read = os.read
        replaced = False

        def replace_state_after_open(descriptor: int, size: int) -> bytes:
            nonlocal replaced
            info = os.fstat(descriptor)
            if not replaced and (info.st_dev, info.st_ino) == (
                original_info.st_dev,
                original_info.st_ino,
            ):
                replaced = True
                state_path.rename(moved)
                state_path.write_bytes(b'{"targets":{}}\n')
                state_path.chmod(0o600)
            return real_read(descriptor, size)

        try:
            with patch.object(
                runtime.os, "read", side_effect=replace_state_after_open
            ), patch.object(runtime, "_copy") as copy, patch.object(
                guards, "_inspect_remote_digest"
            ) as inspect:
                with self.assertRaisesRegex(
                    OciPublishError, "publication_state_missing"
                ):
                    guards.read_back(
                        self.plan, self.env, repository_root=self.root
                    )
                inspect.assert_not_called()
                copy.assert_not_called()
        finally:
            if moved.exists():
                state_path.unlink(missing_ok=True)
                moved.rename(state_path)

        self.assertTrue(replaced)

    def test_verify_only_requires_both_existing_refs_and_performs_no_write(self) -> None:
        digest = str(self.local["manifest_digest"])
        target = self.plan.targets[0]
        with patch.object(
            guards,
            "_list_repository_tags",
            return_value=_tag_listing(
                target, target.version_reference, target.source_reference
            ),
        ) as list_tags, patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ), patch.object(runtime, "_copy") as copy:
            result = guards.publish(
                self.plan,
                self.env,
                allow_publish=False,
                repository_root=self.root,
            )
        self.assertEqual(result["result"], "replayed")
        copy.assert_not_called()
        self.assertEqual(list_tags.call_count, 2)
        publication_path = (
            guards.publication_state_root(self.env) / "publication.json"
        )
        self.assertTrue(publication_path.is_file())
        publication_path.unlink()

        with patch.object(
            guards,
            "_list_repository_tags",
            return_value=_tag_listing(target, target.version_reference),
        ), patch.object(guards, "_inspect_remote_digest", return_value=digest), patch.object(
            runtime, "_copy"
        ) as copy:
            with self.assertRaisesRegex(OciPublishError, "remote_reference_missing"):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )
        copy.assert_not_called()

    def test_tag_publication_repairs_only_missing_refs_then_reads_back(self) -> None:
        digest = str(self.local["manifest_digest"])
        remote: dict[str, str] = {}
        remote_layout = self.root / "remote-layout"
        shutil.copytree(self.build_layout, remote_layout)

        def inspect(reference: str, _authfile: Path, _capacity) -> str | None:
            return remote.get(reference)

        def copy(source: str, destination: str, _authfile: Path, _capacity) -> None:
            if destination.startswith("docker://"):
                remote[destination.removeprefix("docker://")] = digest
                return
            if source.startswith("docker://") and destination.startswith("oci:"):
                payload = destination.removeprefix("oci:")
                path_text, _, _ref = payload.rpartition(":")
                destination_path = Path(path_text)
                shutil.copytree(remote_layout, destination_path)
                return
            raise AssertionError((source, destination))

        self.env.update(
            {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_TYPE": "tag",
                "GITHUB_REF_NAME": "1.2.3",
                "GITHUB_REF": "refs/tags/1.2.3",
            }
        )
        def listing(target, *_args):
            return _tag_listing(
                target,
                *(reference for reference in remote if reference.startswith(
                    f"{target.registry_repository}:"
                )),
            )

        with patch.object(
            guards,
            "_list_repository_tags",
            side_effect=listing,
        ), patch.object(guards, "_inspect_remote_digest", side_effect=inspect), patch.object(
            runtime, "_copy", side_effect=copy
        ) as copy_call:
            published = guards.publish(
                self.plan, self.env, repository_root=self.root
            )
            self.assertEqual(published["result"], "published")
            self.assertEqual(copy_call.call_count, 2)
            shutil.rmtree(runtime.build_state_root(self.env))
            readback = guards.read_back(
                self.plan, self.env, repository_root=self.root
            )
        self.assertEqual(readback["result"], "read-back")
        self.assertEqual(json.loads(readback["manifest_digests_json"])["backend"], digest)
        verified = public.verify(self.plan, self.env)
        self.assertEqual(verified["result"], "success")
        self.assertEqual(
            json.loads(verified["manifest_digests_json"])["backend"], digest
        )
        immutable = json.loads(verified["immutable_references_json"])
        self.assertEqual(immutable["release"], {"source_sha": SHA, "version": "1.2.3"})
        self.assertEqual(
            immutable["targets"]["backend"]["source_reference"],
            f"registry.example.invalid/fixtures/backend:sha-{SHA}",
        )
        self.assertNotIn("source_sha", immutable["targets"]["backend"])
        self.assertEqual(
            immutable["targets"]["backend"]["resolved_inputs"],
            _resolved_inputs(self.plan.targets[0].platforms),
        )

    def test_post_write_tag_listing_must_confirm_both_exact_tags(self) -> None:
        target = self.plan.targets[0]
        absent = subprocess.CompletedProcess(
            ["skopeo", "list-tags"],
            0,
            json.dumps(
                {"Repository": target.registry_repository, "Tags": []}
            ).encode(),
            b"",
        )
        self.env.update(
            {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_TYPE": "tag",
                "GITHUB_REF_NAME": "1.2.3",
                "GITHUB_REF": "refs/tags/1.2.3",
            }
        )
        for case in ("missing", "malformed"):
            post = (
                absent
                if case == "missing"
                else subprocess.CompletedProcess(
                    ["skopeo", "list-tags"], 0, b"{", b""
                )
            )
            with self.subTest(case=case), patch.object(
                runtime, "_run", side_effect=(absent, post)
            ) as run, patch.object(
                guards, "_inspect_remote_digest", return_value=None
            ), patch.object(
                runtime, "_copy"
            ) as copy:
                with self.assertRaisesRegex(
                    OciPublishError, "registry_inspection_failed"
                ):
                    guards.publish(
                        self.plan, self.env, repository_root=self.root
                    )
                self.assertEqual(run.call_count, 2)
                self.assertEqual(copy.call_count, 2)
                self.assertFalse(
                    (
                        guards.publication_state_root(self.env)
                        / "publication.json"
                    ).exists()
                )

    def test_publication_rejects_missing_or_mismatched_build_input_evidence(self) -> None:
        digest = str(self.local["manifest_digest"])
        result_path = runtime.build_state_root(self.env) / "result.json"
        result_path.unlink()
        target = self.plan.targets[0]
        existing = _tag_listing(
            target, target.version_reference, target.source_reference
        )
        with patch.object(guards, "_list_repository_tags", return_value=existing), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            with self.assertRaisesRegex(OciPublishError, "build_evidence_missing"):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )

        _write_build_result(
            self.env,
            self.plan,
            {"backend": "sha256:" + "f" * 64},
        )
        with patch.object(guards, "_list_repository_tags", return_value=existing), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            with self.assertRaisesRegex(OciPublishError, "build_evidence_mismatch"):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )

        opened = _resolved_inputs(self.plan.targets[0].platforms)
        opened["source_url"] = "https://secret.example/private-input"
        _write_build_result(
            self.env,
            self.plan,
            {"backend": digest},
            {"backend": opened},
        )
        with patch.object(guards, "_list_repository_tags", return_value=existing), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            with self.assertRaisesRegex(OciPublishError, "build_evidence_mismatch"):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )

        wrong_policy = _resolved_inputs(self.plan.targets[0].platforms)
        wrong_policy["input_policy_id"] = "another-policy"
        wrong_policy_payload = {
            key: wrong_policy[key]
            for key in (
                "lock_digest",
                "input_policy_id",
                "bases",
                "external_inputs",
            )
        }
        wrong_policy["evidence_id"] = hashlib.sha256(
            json.dumps(
                wrong_policy_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        _write_build_result(
            self.env,
            self.plan,
            {"backend": digest},
            {"backend": wrong_policy},
        )
        with patch.object(guards, "_list_repository_tags", return_value=existing), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            with self.assertRaisesRegex(OciPublishError, "build_evidence_mismatch"):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )

        incomplete_platforms = _resolved_inputs(self.plan.targets[0].platforms)
        incomplete_platforms["bases"][0]["platforms"] = incomplete_platforms[
            "bases"
        ][0]["platforms"][:1]
        incomplete_payload = {
            key: incomplete_platforms[key]
            for key in (
                "lock_digest",
                "input_policy_id",
                "bases",
                "external_inputs",
            )
        }
        incomplete_platforms["evidence_id"] = hashlib.sha256(
            json.dumps(
                incomplete_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        _write_build_result(
            self.env,
            self.plan,
            {"backend": digest},
            {"backend": incomplete_platforms},
        )
        with patch.object(guards, "_list_repository_tags", return_value=existing), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            with self.assertRaisesRegex(OciPublishError, "build_evidence_mismatch"):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )

        normal_lock = self.input_locks[target.target_id]
        fixed_platforms = (target.platforms[0],)
        self.input_locks[target.target_id] = _input_lock(
            self.plan.product_id,
            target,
            base_platforms=fixed_platforms,
            dockerfile_platform=fixed_platforms[0],
        )
        extra_platform = _resolved_inputs(target.platforms)
        _write_build_result(
            self.env,
            self.plan,
            {"backend": digest},
            {"backend": extra_platform},
        )
        with patch.object(
            guards, "_list_repository_tags", return_value=existing
        ), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            with self.assertRaisesRegex(
                OciPublishError, "build_evidence_mismatch"
            ):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )
        self.input_locks[target.target_id] = normal_lock

        wrong_root = _resolved_inputs(self.plan.targets[0].platforms)
        wrong_root["bases"][0]["root_digest"] = "sha256:" + "8" * 64
        wrong_root_payload = {
            key: wrong_root[key]
            for key in (
                "lock_digest",
                "input_policy_id",
                "bases",
                "external_inputs",
            )
        }
        wrong_root["evidence_id"] = hashlib.sha256(
            json.dumps(
                wrong_root_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        _write_build_result(
            self.env,
            self.plan,
            {"backend": digest},
            {"backend": wrong_root},
        )
        with patch.object(guards, "_list_repository_tags", return_value=existing), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ):
            with self.assertRaisesRegex(OciPublishError, "build_evidence_mismatch"):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )

    def test_publication_rejects_tampered_target_result_with_stale_evidence_id(
        self,
    ) -> None:
        target = self.plan.targets[0]
        digest = str(self.local["manifest_digest"])
        result_path = runtime.build_state_root(self.env) / "result.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        target_results = json.loads(payload["target_results_json"])
        target_results[0]["smoke_result"] = "isolated-script-passed"
        payload["target_results_json"] = json.dumps(
            target_results, sort_keys=True, separators=(",", ":")
        )
        result_path.write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        result_path.chmod(0o600)

        with patch.object(
            guards,
            "_list_repository_tags",
            return_value=_tag_listing(
                target, target.version_reference, target.source_reference
            ),
        ), patch.object(
            guards, "_inspect_remote_digest", return_value=digest
        ), patch.object(runtime, "_copy") as copy:
            with self.assertRaisesRegex(
                OciPublishError, "build_evidence_mismatch"
            ):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )

        copy.assert_not_called()

    def test_build_evidence_platforms_must_exactly_match_each_locked_base(self) -> None:
        target = self.plan.targets[0]
        digest = str(self.local["manifest_digest"])
        fixed_platforms = (target.platforms[0],)
        existing = _tag_listing(
            target, target.version_reference, target.source_reference
        )
        cases = {
            "missing-from-all-platform-lock": (
                _input_lock(self.plan.product_id, target),
                _resolved_inputs(
                    target.platforms, base_platforms=fixed_platforms
                ),
            ),
            "extra-for-fixed-platform-lock": (
                _input_lock(
                    self.plan.product_id,
                    target,
                    base_platforms=fixed_platforms,
                    dockerfile_platform=fixed_platforms[0],
                ),
                _resolved_inputs(target.platforms),
            ),
        }
        original_lock = self.input_locks[target.target_id]
        try:
            for name, (input_lock, evidence) in cases.items():
                self.input_locks[target.target_id] = input_lock
                _write_build_result(
                    self.env,
                    self.plan,
                    {target.target_id: digest},
                    {target.target_id: evidence},
                )
                with self.subTest(name=name), patch.object(
                    guards, "_list_repository_tags", return_value=existing
                ), patch.object(
                    guards, "_inspect_remote_digest", return_value=digest
                ):
                    with self.assertRaisesRegex(
                        OciPublishError, "build_evidence_mismatch"
                    ):
                        guards.publish(
                            self.plan,
                            self.env,
                            allow_publish=False,
                            repository_root=self.root,
                        )
        finally:
            self.input_locks[target.target_id] = original_lock
            _write_build_result(
                self.env,
                self.plan,
                {target.target_id: digest},
            )

    def test_pre_copy_recheck_rejects_new_conflict_without_writing(self) -> None:
        digest = str(self.local["manifest_digest"])
        conflict = "sha256:" + "f" * 64
        observations = iter((conflict,))
        self.env.update(
            {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_TYPE": "tag",
                "GITHUB_REF_NAME": "1.2.3",
                "GITHUB_REF": "refs/tags/1.2.3",
            }
        )

        with patch.object(
            guards,
            "_list_repository_tags",
            return_value=_tag_listing(self.plan.targets[0]),
        ), patch.object(
            guards,
            "_inspect_remote_digest",
            side_effect=lambda *_: next(observations),
        ), patch.object(runtime, "_copy") as copy:
            with self.assertRaisesRegex(
                OciPublishError, "immutable_reference_conflict"
            ):
                guards.publish(self.plan, self.env, repository_root=self.root)

        copy.assert_not_called()


class MultiTargetPublicationPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build_capacity, _publication_capacity = _install_capacity_resolution(
            self, self.root
        )
        build_execution.prepare_capacity_roots(self.build_capacity)
        (self.root / "contracts").mkdir()
        shutil.copyfile(FIXTURE, self.root / "contracts/oci-products.json")
        self.plan = runtime.resolve_plan(
            self.root,
            PublishRequest(
                "StreamScapeTV/flux",
                SHA,
                SHA,
                "runner-images",
                "1.2.3",
                "trusted-exact",
            ),
        )
        self.input_locks = {
            target.target_id: _input_lock(self.plan.product_id, target)
            for target in self.plan.targets
        }
        source_lock_patcher = patch.object(
            guards,
            "_validated_source_input_locks",
            return_value=(self.root, self.input_locks),
        )
        exact_source_patcher = patch.object(guards, "_assert_exact_source")
        source_lock_patcher.start()
        exact_source_patcher.start()
        self.addCleanup(source_lock_patcher.stop)
        self.addCleanup(exact_source_patcher.stop)
        self.env = {
            "RUNNER_TEMP": str(self.root / "runner-temp"),
            "GITHUB_RUN_ID": "9003",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF_TYPE": "tag",
            "GITHUB_REF_NAME": "1.2.3",
            "GITHUB_REF": "refs/tags/1.2.3",
        }
        Path(self.env["RUNNER_TEMP"]).mkdir()
        layouts_root = runtime.build_state_root(self.env) / "layouts"
        layouts_root.mkdir(parents=True)
        self.local_digests: dict[str, str] = {}
        for target in self.plan.targets:
            created = _make_layout(self.root / f"source-{target.target_id}", target)
            layout = layouts_root / target.target_id
            shutil.copytree(created, layout)
            local = guards.inspect_layout(layout, target, "validation")
            self.local_digests[target.target_id] = str(local["manifest_digest"])
        _write_build_result(self.env, self.plan, self.local_digests)
        publication_capacity = runtime.publication_capacity_roots(self.env)
        build_execution.prepare_capacity_roots(publication_capacity)
        runtime._prepare_publication_runtime(publication_capacity)  # noqa: SLF001
        publication_root = publication_capacity.scratch_root
        (publication_root / "registry-auth.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (publication_root / "registry-auth.json").chmod(0o600)
        runtime._write_publication_plan_state(  # noqa: SLF001
            self.plan, publication_capacity
        )

    def test_later_target_conflict_prevents_every_registry_write(self) -> None:
        later = self.plan.targets[1]
        remote = {later.version_reference: "sha256:" + "f" * 64}

        with patch.object(
            guards,
            "_list_repository_tags",
            side_effect=lambda target, *_: _tag_listing(
                target,
                *(reference for reference in remote if reference.startswith(
                    f"{target.registry_repository}:"
                )),
            ),
        ), patch.object(
            guards,
            "_inspect_remote_digest",
            side_effect=lambda reference, _authfile, _capacity: remote.get(reference),
        ) as inspect, patch.object(runtime, "_copy") as copy:
            with self.assertRaisesRegex(
                OciPublishError, "immutable_reference_conflict"
            ):
                guards.publish(self.plan, self.env, repository_root=self.root)

        self.assertEqual(
            [call.args[0] for call in inspect.call_args_list],
            [later.version_reference],
        )
        copy.assert_not_called()
        self.assertFalse(
            (guards.publication_state_root(self.env) / "publication.json").exists()
        )

    def test_oversized_public_evidence_fails_before_every_registry_write(self) -> None:
        with patch.object(
            guards,
            "_list_repository_tags",
            side_effect=lambda target, *_: _tag_listing(target),
        ), patch.object(
            guards, "_inspect_remote_digest", return_value=None
        ), patch.object(
            runtime,
            "public_json_outputs",
            side_effect=OciPublishError("publication_output_invalid"),
        ), patch.object(runtime, "_copy") as copy:
            with self.assertRaisesRegex(
                OciPublishError, "publication_output_invalid"
            ):
                guards.publish(self.plan, self.env, repository_root=self.root)

        copy.assert_not_called()
        self.assertFalse(
            (guards.publication_state_root(self.env) / "publication.json").exists()
        )

    def test_every_repository_tag_listing_completes_before_local_or_remote_inspection(self) -> None:
        first, second = self.plan.targets
        calls: list[str] = []

        def listing(target, *_args):
            calls.append(target.target_id)
            if target == second:
                raise OciPublishError("registry_inspection_failed")
            return _tag_listing(target)

        with patch.object(
            guards, "_list_repository_tags", side_effect=listing
        ), patch.object(guards, "inspect_layout") as local, patch.object(
            guards, "_inspect_remote_digest"
        ) as remote, patch.object(runtime, "_copy") as copy:
            with self.assertRaisesRegex(
                OciPublishError, "registry_inspection_failed"
            ):
                guards.publish(self.plan, self.env, repository_root=self.root)

        self.assertEqual(calls, [first.target_id, second.target_id])
        local.assert_not_called()
        remote.assert_not_called()
        copy.assert_not_called()

    def test_later_target_assertion_failure_prevents_every_registry_write(self) -> None:
        later = self.plan.targets[1]

        def assert_filesystem(_root, _plan, target, _layout) -> None:
            if target == later:
                raise OciPublishError("assertion_failed")

        with patch.object(
            guards,
            "_list_repository_tags",
            side_effect=lambda target, *_: _tag_listing(target),
        ), patch.object(
            guards._assertions,
            "assert_filesystem_contract",
            side_effect=assert_filesystem,
        ), patch.object(guards, "_inspect_remote_digest", return_value=None), patch.object(
            runtime, "_copy"
        ) as copy:
            with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                guards.publish(self.plan, self.env, repository_root=self.root)

        copy.assert_not_called()
        self.assertFalse(
            (guards.publication_state_root(self.env) / "publication.json").exists()
        )

    def test_multi_target_partial_repair_runs_only_after_preflight(self) -> None:
        first, second = self.plan.targets
        remote = {
            first.version_reference: self.local_digests[first.target_id],
            second.version_reference: self.local_digests[second.target_id],
            second.source_reference: self.local_digests[second.target_id],
        }

        def inspect(reference: str, _authfile: Path, _capacity) -> str | None:
            return remote.get(reference)

        def copy(_source: str, destination: str, _authfile: Path, _capacity) -> None:
            reference = destination.removeprefix("docker://")
            remote[reference] = self.local_digests[first.target_id]

        def listing(target, *_args):
            return _tag_listing(
                target,
                *(reference for reference in remote if reference.startswith(
                    f"{target.registry_repository}:"
                )),
            )

        with patch.object(
            guards, "_list_repository_tags", side_effect=listing
        ), patch.object(
            guards, "_inspect_remote_digest", side_effect=inspect
        ), patch.object(runtime, "_copy", side_effect=copy) as copy_call:
            result = guards.publish(self.plan, self.env, repository_root=self.root)

        self.assertEqual(result["result"], "published")
        self.assertEqual(result["replayed"], "true")
        copy_call.assert_called_once()
        self.assertEqual(
            copy_call.call_args.args[1], f"docker://{first.source_reference}"
        )
        self.assertEqual(
            json.loads(result["manifest_digests_json"]), self.local_digests
        )


class AuthenticationAndCleanupTests(unittest.TestCase):
    def test_registry_copy_preserves_digests_in_actual_skopeo_argv(self) -> None:
        result = subprocess.CompletedProcess(["skopeo", "copy"], 0, b"", b"")
        authfile = Path("registry-auth.json")
        capacity_base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, capacity_base)
        capacity = build_execution._test_capacity_roots(  # noqa: SLF001
            capacity_base,
            domain="oci-publish",
            prefix="ciw-oci-publish",
            token="3" * 20,
        )
        with patch.object(runtime, "_run", return_value=result) as run:
            runtime._copy(  # noqa: SLF001
                "oci:/tmp/layout:validation",
                "docker://ghcr.io/streamscapetv/backend:1.2.3",
                authfile,
                capacity,
            )

        run.assert_called_once_with(
            [
                "skopeo",
                "copy",
                "--all",
                "--preserve-digests",
                "--authfile",
                str(authfile),
                "oci:/tmp/layout:validation",
                "docker://ghcr.io/streamscapetv/backend:1.2.3",
            ],
            capacity_roots=capacity,
            stdout_limit=runtime._MAX_REGISTRY_COPY_STDOUT_BYTES,  # noqa: SLF001
            stderr_limit=runtime._MAX_REGISTRY_COPY_STDERR_BYTES,  # noqa: SLF001
            overflow_code="registry_copy_failed",
            retain_output=False,
            expected_auth_state=None,
        )

    def test_registry_children_receive_only_bounded_noncredential_environment(self) -> None:
        token = "must-not-reach-child-environment"
        child = subprocess.CompletedProcess(["skopeo", "version"], 0, b"", b"")
        with tempfile.TemporaryDirectory() as directory:
            capacity = build_execution._test_capacity_roots(  # noqa: SLF001
                Path(directory),
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="4" * 20,
            )
            build_execution.prepare_capacity_roots(capacity)
            runtime._prepare_publication_runtime(capacity)  # noqa: SLF001
            authfile = capacity.scratch_root / "registry-auth.json"
            authfile.write_text("{}\n", encoding="utf-8")
            authfile.chmod(0o600)
            with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "HOME": "/tmp/ciw-home",
                "HTTP_PROXY": "http://ambient-proxy.invalid",
                "INPUT_REGISTRY_USERNAME": "publisher",
                "INPUT_REGISTRY_TOKEN": token,
                "GITHUB_TOKEN": "must-also-stay-out",
            },
            clear=True,
            ), patch.object(
                runtime, "_run_bounded_subprocess", return_value=child
            ) as run:
                runtime._run(  # noqa: SLF001
                    ["skopeo", "version"],
                    capacity_roots=capacity,
                    stdout_limit=1024,
                    stderr_limit=1024,
                    overflow_code="registry_inspection_failed",
                )

        child_environment = run.call_args.kwargs["env"]
        pass_fds = run.call_args.kwargs["pass_fds"]
        self.assertEqual(child_environment["PATH"], "/usr/bin")
        for key in (
            "HOME",
            "TMPDIR",
            "CONTAINERS_REGISTRIES_CONF",
            "REGISTRY_AUTH_FILE",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR",
        ):
            path = child_environment[key]
            self.assertRegex(path, r"^/proc/self/fd/[0-9]+$")
            self.assertIn(int(path.rsplit("/", 1)[1]), pass_fds)
        self.assertNotIn("HTTP_PROXY", child_environment)
        self.assertNotIn(token, child_environment.values())
        self.assertIsNotNone(run.call_args.kwargs["preexec_fn"])

    def test_registry_child_rejects_capacity_marker_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            capacity = build_execution._test_capacity_roots(  # noqa: SLF001
                Path(directory),
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="f" * 20,
            )
            build_execution.prepare_capacity_roots(capacity)
            runtime._prepare_publication_runtime(capacity)  # noqa: SLF001
            marker = capacity.graph_root / ".ciw-capacity-root.json"
            marker.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(OciPublishError, "capacity_marker_invalid"):
                runtime._run(  # noqa: SLF001
                    ["skopeo", "version"],
                    capacity_roots=capacity,
                    stdout_limit=1024,
                    stderr_limit=1024,
                    overflow_code="registry_inspection_failed",
                )

    def test_authentication_revalidates_authfile_after_skopeo_login(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "RUNNER_TEMP": str(root),
                "GITHUB_RUN_ID": "9004",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            plan = SimpleNamespace(
                admitted_sha=SHA,
                product_id="backend-image",
                release_version="1.2.3",
                registry_write_policy=runtime.OciRegistryWritePolicy(
                    policy_id="fixture-create-only-v1",
                    registry_host="registry.example.invalid",
                    required_enforcement="server-side-create-only-tags-v1",
                    status="verified",
                    authority_repository="StreamScapeTV/flux",
                    authority_source_sha="1" * 40,
                    evidence_id="sha256:" + "2" * 64,
                ),
                targets=(
                    SimpleNamespace(
                        registry_repository="registry.example.invalid/fixtures/backend"
                    ),
                ),
            )

            capacity = build_execution._test_capacity_roots(  # noqa: SLF001
                root / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="5" * 20,
            )

            def fake_run(argv, **kwargs):
                authfile = Path(argv[argv.index("--authfile") + 1])
                authfile.chmod(0o644)
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            with patch.object(
                runtime.shutil, "which", return_value="/usr/bin/skopeo"
            ), patch.object(runtime, "_run", side_effect=fake_run):
                with self.assertRaisesRegex(OciPublishError, "registry_auth_invalid"):
                    runtime.authenticate(
                        plan,
                        env,
                        "publisher",
                        "bounded-token",
                        _capacity_roots=capacity,
                    )

    def test_token_is_stdin_only_auth_is_0600_and_cleanup_removes_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            shutil.copyfile(FIXTURE, root / "contracts/oci-products.json")
            plan = runtime.resolve_plan(
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
            env = {
                "RUNNER_TEMP": str(root / "runner-temp"),
                "GITHUB_RUN_ID": "9002",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_TYPE": "tag",
                "GITHUB_REF_NAME": "1.2.3",
                "GITHUB_REF": "refs/tags/1.2.3",
            }
            Path(env["RUNNER_TEMP"]).mkdir()
            token = "bounded-secret-token"
            seen: dict[str, object] = {}

            capacity = build_execution._test_capacity_roots(  # noqa: SLF001
                root / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
                token="6" * 20,
            )

            def fake_run(argv, **kwargs):
                seen["argv"] = tuple(argv)
                seen["stdin"] = kwargs.get("input_bytes")
                seen["run_policy"] = {
                    key: kwargs.get(key)
                    for key in (
                        "stdout_limit",
                        "stderr_limit",
                        "overflow_code",
                        "retain_output",
                    )
                }
                authfile = Path(argv[argv.index("--authfile") + 1])
                seen["authfile_is_file"] = authfile.is_file()
                seen["authfile_is_symlink"] = authfile.is_symlink()
                seen["authfile_contents"] = authfile.read_bytes()
                seen["authfile_mode"] = stat.S_IMODE(authfile.lstat().st_mode)
                authfile.write_text("{}\n", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            with patch.object(runtime.shutil, "which", return_value="/usr/bin/skopeo"), patch.object(
                runtime, "_run", side_effect=fake_run
            ):
                result = guards.authenticate(
                    plan, env, "publisher", token, _capacity_roots=capacity
                )
            self.assertEqual(result["result"], "authenticated")
            self.assertEqual(seen["stdin"], token.encode())
            self.assertEqual(
                seen["run_policy"],
                {
                    "stdout_limit": runtime._MAX_REGISTRY_LOGIN_STDOUT_BYTES,  # noqa: SLF001
                    "stderr_limit": runtime._MAX_REGISTRY_LOGIN_STDERR_BYTES,  # noqa: SLF001
                    "overflow_code": "registry_auth_failed",
                    "retain_output": False,
                },
            )
            self.assertNotIn(token, " ".join(seen["argv"]))
            self.assertIs(seen["authfile_is_file"], True)
            self.assertIs(seen["authfile_is_symlink"], False)
            self.assertEqual(seen["authfile_contents"], b"{}\n")
            self.assertEqual(seen["authfile_mode"], 0o600)
            authfile = guards.publication_state_root(
                env, _capacity_roots=capacity
            ) / "registry-auth.json"
            self.assertEqual(stat.S_IMODE(authfile.stat().st_mode), 0o600)
            state_text = (guards.publication_state_root(
                env, _capacity_roots=capacity
            ) / "plan.json").read_text(
                encoding="utf-8"
            )
            state = json.loads(state_text)
            self.assertNotIn(token, state_text)
            self.assertNotIn(str(root), state_text)
            self.assertNotIn("registry-auth.json", state_text)
            self.assertEqual(
                set(state),
                {
                    "api",
                    "authfile",
                    "capacity",
                    "execution",
                    "product_id",
                    "registry_host",
                    "registry_write_policy",
                    "release_policy",
                    "release_version",
                    "repositories",
                    "source",
                    "targets",
                    "version",
                },
            )
            self.assertEqual(
                set(state["authfile"]),
                {"format", "mode", "sha256", "size_bytes"},
            )
            self.assertEqual(state["capacity"]["token"], "6" * 20)
            self.assertEqual(
                state["repositories"],
                {"backend": "registry.example.invalid/fixtures/backend"},
            )
            guards.cleanup(env, _capacity_roots=capacity)
            guards.residue(env, _capacity_roots=capacity)
            self.assertFalse(
                guards.publication_state_root(
                    env, _capacity_roots=capacity
                ).exists()
            )


class WorkflowTrustRecoveryTests(unittest.TestCase):
    def test_reusable_workflow_uses_immutable_private_helpers_and_event_authority(self) -> None:
        text = (
            ROOT / ".github/workflows/reusable-oci-publish.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("repository: ${{ job.workflow_repository }}", text)
        self.assertNotIn("ref: ${{ job.workflow_sha }}", text)
        self.assertNotIn("path: .ciw", text)
        self.assertNotIn("./.ciw/actions/", text)
        self.assertIn(
            f"uses: StreamScapeTV/ci-workflows/actions/publish-oci@{PUBLISH_SHA}",
            text,
        )
        self.assertIn("github.event_name == 'workflow_dispatch'", text)
        self.assertIn("'existing-tag' || 'tag-push'", text)
        self.assertIn("Verify authority matches the public release request", text)
        self.assertIn("Publish or verify immutable version and source identities", text)


if __name__ == "__main__":
    unittest.main()
