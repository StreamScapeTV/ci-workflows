from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from ci_workflows import oci_execution as execution
from ci_workflows.oci_types import (
    OciBuildError,
    OciBuildInputEvidence,
    OciBuildPlan,
    OciInputPolicy,
    OciTarget,
)


SHA = "a" * 40


class SimulatedInterruption(BaseException):
    """Represent cancellation that must bypass ordinary Exception handlers."""


def target() -> OciTarget:
    return OciTarget(
        target_id="fixture",
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
        targets=(target(),),
        flux_asset=False,
        canary_id=None,
        previous_known_good=None,
        rollback_id=None,
        adoption_ready=True,
        input_policies={policy.policy_id: policy},
    )


class OciInputCleanupTests(unittest.TestCase):
    def _populate_residue(
        self,
        root: Path,
        staged: Path,
        outside: Path,
    ) -> None:
        blob = (
            root
            / "input-layouts"
            / "fixture"
            / "stage-1"
            / "root"
            / "blobs"
            / "sha256"
            / ("b" * 64)
        )
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(b"partially-acquired-base")
        (blob.parent / ("c" * 64 + ".partial")).write_bytes(b"partial-base")

        reserved = staged / ".ciw-build-inputs"
        reserved.mkdir(parents=True, exist_ok=True)
        (reserved / "README.md").write_bytes(b"verified-input")
        (reserved / ".download.partial").write_bytes(b"partial-input")
        (reserved / "outside-link").symlink_to(outside, target_is_directory=True)

        for directory in (root / "storage", root / "runroot"):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "partial-state").write_text("residue\n", encoding="utf-8")
        (root / "auth.json").write_text('{"auths":{"forbidden":"secret"}}\n', encoding="utf-8")
        (root / "metadata.json").write_text('{"partial":true}\n', encoding="utf-8")
        (root / "result.json").write_text('{"partial":true}\n', encoding="utf-8")
        (root / "manifests.json").write_text(
            '["ciw-fixture-first","ciw-fixture-second"]\n', encoding="utf-8"
        )

    def _assert_cleanup_after_failure(
        self,
        failure: BaseException,
        *,
        fail_during_materialization: bool,
    ) -> None:
        cleanup_calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_stage(source_root: Path, selected: OciTarget, destination: Path) -> Path:
            destination.mkdir(parents=True)
            (destination / "Containerfile").write_text("FROM scratch\n", encoding="utf-8")
            return destination

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            source_root = temporary / "source"
            source_root.mkdir()
            environment = {
                "RUNNER_TEMP": str(temporary / "runner"),
                "GITHUB_RUN_ID": "150",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            root = execution.state_root(environment)
            outside = temporary / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("outside-must-survive\n", encoding="utf-8")

            def materialize(*args: object) -> execution.MaterializedTargetInputs:
                staged = args[3]
                self.assertIsInstance(staged, Path)
                self._populate_residue(root, staged, outside)
                if fail_during_materialization:
                    raise failure
                return execution.MaterializedTargetInputs(
                    lock=None,
                    evidence=OciBuildInputEvidence.empty(),
                    image_ids_by_platform={"linux/amd64": {}},
                )

            def build(*args: object, **kwargs: object) -> object:
                del args, kwargs
                raise failure

            def fake_cleanup_run(
                argv: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                cleanup_calls.append((list(argv), kwargs))
                return subprocess.CompletedProcess(argv, 0, "", "")

            def invoke() -> None:
                try:
                    with mock.patch.object(
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
                        execution, "stage_context", side_effect=fake_stage
                    ), mock.patch.object(
                        execution, "_materialize_target_inputs", side_effect=materialize
                    ), mock.patch.object(
                        execution, "build_target", side_effect=build
                    ):
                        execution.execute_plan(
                            temporary,
                            source_root,
                            plan(),
                            environment,
                        )
                finally:
                    with mock.patch.object(
                        execution.shutil, "which", return_value="/usr/bin/buildah"
                    ), mock.patch.object(
                        execution.subprocess, "run", side_effect=fake_cleanup_run
                    ):
                        execution.cleanup(environment)
                    execution.residue(environment)

            with self.assertRaises(type(failure)) as raised:
                invoke()
            self.assertEqual(str(failure), str(raised.exception))
            self.assertFalse(root.exists() or root.is_symlink())
            self.assertEqual("outside-must-survive\n", sentinel.read_text(encoding="utf-8"))

            self.assertEqual(4, len(cleanup_calls))
            expected_prefix = [
                "buildah",
                "--storage-driver",
                "vfs",
                "--root",
                str(root / "storage"),
                "--runroot",
                str(root / "runroot"),
            ]
            for command, options in cleanup_calls:
                with self.subTest(command=command):
                    self.assertEqual(expected_prefix, command[: len(expected_prefix)])
                    cleanup_environment = options["env"]
                    self.assertIsInstance(cleanup_environment, dict)
                    self.assertEqual(
                        str(root / "auth.json"),
                        cleanup_environment["REGISTRY_AUTH_FILE"],
                    )
                    self.assertEqual(
                        str(root / "storage.conf"),
                        cleanup_environment["CONTAINERS_STORAGE_CONF"],
                    )
                    self.assertEqual(str(root / "home"), cleanup_environment["HOME"])
                    self.assertTrue(callable(options["preexec_fn"]))
                    self.assertNotIn("forbidden", " ".join(cleanup_environment.values()))

    def test_base_and_external_acquisition_failures_leave_zero_residue(self) -> None:
        for instruction in ("base_acquisition_failed", "external_input_failed"):
            with self.subTest(instruction=instruction):
                self._assert_cleanup_after_failure(
                    OciBuildError(instruction), fail_during_materialization=True
                )

    def test_bud_failure_after_materialization_leaves_zero_residue(self) -> None:
        self._assert_cleanup_after_failure(
            OciBuildError("build_failed"), fail_during_materialization=False
        )

    def test_interruption_after_materialization_leaves_zero_residue(self) -> None:
        self._assert_cleanup_after_failure(
            SimulatedInterruption("cancelled"), fail_during_materialization=False
        )


if __name__ == "__main__":
    unittest.main()
