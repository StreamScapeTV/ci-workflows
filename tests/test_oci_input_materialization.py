from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ci_workflows import oci_execution as execution
from ci_workflows.oci_input_contract import (
    OciBaseLock,
    OciBasePlatformIdentity,
    OciExternalInputLock,
    OciTargetInputLock,
)
from ci_workflows.oci_types import (
    OciBuildInputEvidence,
    OciBuildPlan,
    OciInputPolicy,
    OciResolvedBase,
    OciResolvedBasePlatform,
    OciResolvedExternalInput,
    OciTarget,
    OciTargetResult,
)

SHA = "a" * 40


def target(target_id: str) -> OciTarget:
    return OciTarget(
        target_id=target_id,
        context_path=".",
        dockerfile_path="Containerfile",
        target_stage=None,
        platforms=("linux/amd64",),
        smoke_script=None,
        required_user=None,
        required_entrypoint=(),
        required_command=(),
        required_ports=(),
        required_files=(),
        required_tools=(),
        forbidden_tools=(),
        fixed_build_args={},
        secret_mount_ids=(),
        build_input_lock_path="inputs.lock.json",
        input_policy_id="oci-inputs-public-v1",
    )


def plan() -> OciBuildPlan:
    policy = OciInputPolicy(
        policy_id="oci-inputs-public-v1",
        allowed_registry_hosts=("docker.io",),
        allowed_registry_api_hosts=("registry-1.docker.io",),
        allowed_registry_token_hosts=("auth.docker.io",),
        allowed_registry_blob_hosts=("production.cloudfront.docker.com",),
        allowed_download_hosts=("raw.githubusercontent.com",),
        https_only=True,
        ambient_auth=False,
        redirect_policy="same-profile-hosts",
        maximum_redirects=5,
        maximum_input_bytes=4096,
    )
    return OciBuildPlan(
        repository="StreamScapeTV/ci-workflows",
        admitted_sha=SHA,
        product_id="fixture-product",
        release_version="1.0.0",
        source_trust="trusted-exact",
        runner_profile="buildah-tiny",
        runs_on=("linux", "amd64", "buildah", "tiny"),
        workspace_profile="minimal",
        timeout_minutes=30,
        builder_id="buildah-v1",
        storage_driver="vfs",
        targets=(target("first"), target("second")),
        flux_asset=False,
        canary_id=None,
        previous_known_good=None,
        rollback_id=None,
        adoption_ready=True,
        input_policies={policy.policy_id: policy},
    )


def scratch_lock(target_id: str = "first") -> OciTargetInputLock:
    return OciTargetInputLock(
        product_id="fixture-product",
        target_id=target_id,
        input_policy_id="scratch-only-v1",
        platforms=("linux/amd64",),
        bases=(
            OciBaseLock(
                stage_id="stage-1",
                from_ordinal=1,
                stage_marker="final",
                kind="scratch",
                declared_reference="scratch",
                dockerfile_platform=None,
                platforms=("linux/amd64",),
                platform_identities=(),
            ),
        ),
        external_inputs=(),
        lock_digest="sha256:" + "f" * 64,
    )


class OciInputMaterializationTests(unittest.TestCase):
    def test_all_targets_are_staged_and_materialized_before_first_bud(self) -> None:
        events: list[str] = []

        def stage(source_root, selected, destination):
            events.append(f"stage:{selected.target_id}")
            destination.mkdir(parents=True)
            (destination / "Containerfile").write_text("FROM scratch\n")
            return destination

        def materialize(source_root, selected_plan, selected, staged, *args):
            events.append(f"materialize:{selected.target_id}")
            return execution.MaterializedTargetInputs(
                lock=scratch_lock(selected.target_id),
                evidence=OciBuildInputEvidence.empty(),
                image_ids_by_platform={},
            )

        def build(selected_plan, selected, staged, root, labels, epoch, *args):
            events.append(f"bud:{selected.target_id}")
            return OciTargetResult(
                selected.target_id,
                "sha256:" + "b" * 64,
                (),
                labels,
                "skipped",
            ), f"manifest-{selected.target_id}"

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            execution, "assert_clean_source"
        ), mock.patch.object(
            execution, "verify_builder_runtime"
        ), mock.patch.object(
            execution, "source_date_epoch", return_value=1
        ), mock.patch.object(
            execution, "load_contract", return_value={"_products": {}}
        ), mock.patch.object(
            execution, "metadata_labels", return_value={}
        ), mock.patch.object(
            execution, "stage_context", side_effect=stage
        ), mock.patch.object(
            execution, "_materialize_target_inputs", side_effect=materialize
        ), mock.patch.object(
            execution, "build_target", side_effect=build
        ):
            execution.execute_plan(
                Path(directory),
                Path(directory),
                plan(),
                {
                    "RUNNER_TEMP": directory,
                    "GITHUB_RUN_ID": "11",
                    "GITHUB_RUN_ATTEMPT": "1",
                },
            )
        self.assertEqual(
            [
                "stage:first",
                "stage:second",
                "materialize:first",
                "materialize:second",
                "bud:first",
                "bud:second",
            ],
            events,
        )

    def test_central_lock_identities_and_host_roles_bind_typed_acquisition(self) -> None:
        base = OciBaseLock(
            stage_id="stage-1",
            from_ordinal=1,
            stage_marker="final",
            kind="external",
            declared_reference="docker.io/library/busybox@sha256:" + "a" * 64,
            dockerfile_platform=None,
            platforms=("linux/amd64",),
            platform_identities=(
                OciBasePlatformIdentity(
                    "linux/amd64", "sha256:" + "b" * 64, "sha256:" + "c" * 64
                ),
            ),
        )
        lock = OciTargetInputLock(
            product_id="fixture-product",
            target_id="first",
            input_policy_id="oci-inputs-public-v1",
            platforms=("linux/amd64",),
            bases=(base,),
            external_inputs=(),
            lock_digest="sha256:" + "e" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source_root = state / "source"
            staged = state / "staged"
            source_root.mkdir()
            staged.mkdir()
            (staged / "Containerfile").write_text(
                "FROM " + base.declared_reference + "\n", encoding="utf-8"
            )
            with mock.patch.object(
                execution,
                "execute_command",
                return_value=subprocess.CompletedProcess(
                    [], 0, "inputs.lock.json\n", ""
                ),
            ), mock.patch.object(
                execution, "load_input_lock_contract", return_value=lock
            ), mock.patch.object(
                execution, "validate_target_dockerfile_lock", return_value=(base,)
            ), mock.patch.object(
                execution,
                "acquire_oci_base",
                side_effect=execution.OciRegistryAcquisitionError(
                    "oci_registry_download_failed"
                ),
            ) as acquired:
                with self.assertRaisesRegex(Exception, "oci_registry_download_failed"):
                    execution._materialize_target_inputs(
                        source_root,
                        plan(),
                        target("first"),
                        staged,
                        state,
                        state / "auth.json",
                        state / "policy.json",
                        {},
                    )
        acquired.assert_called_once()
        request = acquired.call_args.args[0]
        self.assertEqual(base.declared_reference, request.reference)
        self.assertEqual(
            (("linux/amd64", "sha256:" + "b" * 64),),
            request.platform_manifest_digests,
        )
        self.assertEqual("registry-1.docker.io", request.registry_api_host)
        self.assertEqual(("docker.io",), request.allowed_reference_hosts)
        self.assertEqual(("auth.docker.io",), request.allowed_token_hosts)
        self.assertEqual(
            ("production.cloudfront.docker.com",), request.allowed_blob_hosts
        )
        self.assertEqual(5, request.maximum_redirects)
        self.assertNotIn("docker://", Path(execution.__file__).read_text())

    def test_resolved_input_output_is_canonical_and_redacted(self) -> None:
        base = OciResolvedBase(
            stage_id="stage-1",
            declared_reference="docker.io/library/busybox@sha256:" + "a" * 64,
            root_digest="sha256:" + "a" * 64,
            platforms=(
                OciResolvedBasePlatform(
                    "linux/amd64", "sha256:" + "b" * 64, "sha256:" + "c" * 64
                ),
            ),
        )
        external = OciResolvedExternalInput(
            "readme", "sha256:" + "e" * 64, 123
        )
        evidence = OciBuildInputEvidence(
            lock_digest="sha256:" + "f" * 64,
            acquisition_policy_id="oci-inputs-public-v1",
            resolved_bases=(base,),
            resolved_external_inputs=(external,),
            evidence_id="0" * 64,
        )
        payload = evidence.to_dict()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self.assertEqual(encoded, json.dumps(payload, sort_keys=True, separators=(",", ":")))
        for forbidden in ("https://", "RUNNER_TEMP", "auth.json", ".ciw-build-inputs"):
            self.assertNotIn(forbidden, encoded)

    def test_tracked_source_cannot_occupy_reserved_materialization_root(self) -> None:
        scratch = OciBaseLock(
            stage_id="stage-1",
            from_ordinal=1,
            stage_marker="final",
            kind="scratch",
            declared_reference="scratch",
            dockerfile_platform=None,
            platforms=("linux/amd64",),
            platform_identities=(),
        )
        lock = OciTargetInputLock(
            product_id="fixture-product",
            target_id="first",
            input_policy_id="oci-inputs-public-v1",
            platforms=("linux/amd64",),
            bases=(scratch,),
            external_inputs=(),
            lock_digest="sha256:" + "f" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source_root = state / "source"
            staged = state / "staged"
            source_root.mkdir()
            staged.mkdir()
            (staged / "Containerfile").write_text("FROM scratch\n")
            (staged / ".ciw-build-inputs").mkdir()
            with mock.patch.object(
                execution,
                "execute_command",
                return_value=subprocess.CompletedProcess(
                    [], 0, "inputs.lock.json\n", ""
                ),
            ), mock.patch.object(
                execution, "load_input_lock_contract", return_value=lock
            ), mock.patch.object(
                execution, "validate_target_dockerfile_lock", return_value=(scratch,)
            ):
                with self.assertRaisesRegex(Exception, "input_materialization_failed"):
                    execution._materialize_target_inputs(
                        source_root,
                        plan(),
                        target("first"),
                        staged,
                        state,
                        state / "auth.json",
                        state / "policy.json",
                        {},
                    )

    def test_scratch_target_requires_and_retains_its_exact_tracked_lock(self) -> None:
        scratch = OciBaseLock(
            stage_id="stage-1",
            from_ordinal=1,
            stage_marker="final",
            kind="scratch",
            declared_reference="scratch",
            dockerfile_platform=None,
            platforms=("linux/amd64",),
            platform_identities=(),
        )
        lock = OciTargetInputLock(
            product_id="fixture-product",
            target_id="first",
            input_policy_id="scratch-only-v1",
            platforms=("linux/amd64",),
            bases=(scratch,),
            external_inputs=(),
            lock_digest="sha256:" + "f" * 64,
        )
        scratch_policy = OciInputPolicy(
            policy_id="scratch-only-v1",
            allowed_registry_hosts=(),
            allowed_registry_api_hosts=(),
            allowed_registry_token_hosts=(),
            allowed_registry_blob_hosts=(),
            allowed_download_hosts=(),
            https_only=True,
            ambient_auth=False,
            redirect_policy="same-profile-hosts",
            maximum_redirects=0,
            maximum_input_bytes=0,
        )
        scratch_target = replace(target("first"), input_policy_id=scratch_policy.policy_id)
        scratch_plan = replace(plan(), input_policies={scratch_policy.policy_id: scratch_policy})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source_root = state / "source"
            staged = state / "staged"
            source_root.mkdir()
            staged.mkdir()
            (staged / "Containerfile").write_text("FROM scratch\n")
            with mock.patch.object(
                execution,
                "execute_command",
                return_value=subprocess.CompletedProcess(
                    [], 0, "inputs.lock.json\n", ""
                ),
            ), mock.patch.object(
                execution, "load_input_lock_contract", return_value=lock
            ), mock.patch.object(
                execution, "validate_target_dockerfile_lock", return_value=(scratch,)
            ):
                materialized = execution._materialize_target_inputs(
                    source_root,
                    scratch_plan,
                    scratch_target,
                    staged,
                    state,
                    state / "auth.json",
                    state / "policy.json",
                    {},
                )
        self.assertIs(lock, materialized.lock)
        self.assertEqual(lock.lock_digest, materialized.evidence.lock_digest)
        self.assertEqual("scratch-only-v1", materialized.evidence.acquisition_policy_id)
        self.assertEqual((), materialized.evidence.resolved_bases)
        self.assertEqual((), materialized.evidence.resolved_external_inputs)
        self.assertEqual({"linux/amd64": {}}, materialized.image_ids_by_platform)

    def test_same_reference_cannot_resolve_to_conflicting_platform_images(self) -> None:
        reference = "docker.io/library/base@sha256:" + "a" * 64
        image_ids = {"linux/amd64": {}, "linux/arm64/v8": {}}
        execution._record_base_image_id(
            image_ids,
            ("linux/amd64", "linux/arm64/v8"),
            reference,
            "sha256:" + "b" * 64,
        )
        with self.assertRaisesRegex(Exception, "input_lock_mismatch"):
            execution._record_base_image_id(
                image_ids,
                ("linux/amd64", "linux/arm64/v8"),
                reference,
                "sha256:" + "c" * 64,
            )

    def test_external_input_is_rehashed_after_build_and_tampering_fails(self) -> None:
        content = b"locked-input"
        sha256 = hashlib.sha256(content).hexdigest()
        external = OciExternalInputLock(
            input_id="readme",
            url="https://raw.githubusercontent.com/example/repo/commit/README.md",
            sha256=sha256,
            maximum_bytes=4096,
            destination=".ciw-build-inputs/README.md",
        )
        lock = replace(scratch_lock(), external_inputs=(external,))
        evidence = OciBuildInputEvidence(
            lock_digest=lock.lock_digest,
            acquisition_policy_id="oci-inputs-public-v1",
            resolved_bases=(),
            resolved_external_inputs=(
                OciResolvedExternalInput("readme", f"sha256:{sha256}", len(content)),
            ),
            evidence_id="a" * 64,
        )
        materialized = execution.MaterializedTargetInputs(lock, evidence, {})
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            destination = context / external.destination
            destination.parent.mkdir()
            destination.write_bytes(content)
            destination.chmod(0o444)
            execution._verify_materialized_external_inputs(context, materialized)
            destination.chmod(0o644)
            destination.write_bytes(b"tampered----")
            destination.chmod(0o444)
            with self.assertRaisesRegex(Exception, "input_materialization_failed"):
                execution._verify_materialized_external_inputs(context, materialized)

    def test_bud_maps_only_verified_local_id_and_remains_networkless(self) -> None:
        commands: list[list[str]] = []

        def run(argv, **kwargs):
            commands.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")

        selected = target("first")
        source_reference = "docker.io/library/busybox@sha256:" + "a" * 64
        materialized = execution.MaterializedTargetInputs(
            lock=scratch_lock(),
            evidence=OciBuildInputEvidence.empty(),
            image_ids_by_platform={
                "linux/amd64": {source_reference: "sha256:" + "b" * 64}
            },
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            execution, "execute_command", side_effect=run
        ), mock.patch.object(
            execution,
            "inspect_layout",
            return_value=OciTargetResult(
                "first", "sha256:" + "c" * 64, (), {}, "not-run"
            ),
        ), mock.patch.object(execution, "verify_no_secret_leakage"):
            root = Path(directory)
            staged = root / "staged"
            staged.mkdir()
            (staged / "Containerfile").write_text("FROM " + source_reference + "\n")
            auth = root / "auth.json"
            auth.write_text('{"auths":{}}\n')
            execution.build_target(
                plan(),
                selected,
                staged,
                root,
                {},
                1,
                {},
                auth,
                {"REGISTRY_AUTH_FILE": str(auth)},
                materialized,
                root / "policy.json",
            )
        bud = next(command for command in commands if "bud" in command)
        self.assertIn("--pull=never", bud)
        self.assertEqual("none", bud[bud.index("--network") + 1])
        self.assertIn("--http-proxy=false", bud)
        context = bud[bud.index("--build-context") + 1]
        self.assertEqual(
            f"{source_reference}=container-image://sha256:" + "b" * 64,
            context,
        )
        self.assertNotIn("docker://", context)


if __name__ == "__main__":
    unittest.main()
