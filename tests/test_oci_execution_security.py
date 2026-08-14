from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows import oci_execution as execution  # noqa: E402
from ci_workflows import oci_execution_safe as safe  # noqa: E402
from ci_workflows.oci_input_contract import (  # noqa: E402
    OciBaseLock,
    OciTargetInputLock,
)
from ci_workflows.oci_types import (  # noqa: E402
    OciBuildInputEvidence,
    OciBuildError,
    OciBuildPlan,
    OciBuildResult,
    OciTarget,
    OciTargetResult,
)

SHA = "a" * 40


def target(smoke: str | None = "ci/verify.sh") -> OciTarget:
    return OciTarget(
        target_id="fixture",
        context_path=".",
        dockerfile_path="Containerfile",
        target_stage=None,
        platforms=("linux/amd64",),
        smoke_script=smoke,
        required_user=None,
        required_entrypoint=(),
        required_command=(),
        required_ports=(),
        required_files=("/hello",),
        required_tools=(),
        forbidden_tools=("docker",),
        fixed_build_args={},
        secret_mount_ids=(),
        build_input_lock_path="inputs.lock.json",
        input_policy_id="scratch-only-v1",
    )


def plan(source_trust: str = "trusted-exact") -> OciBuildPlan:
    return OciBuildPlan(
        repository="StreamScapeTV/ci-workflows",
        admitted_sha=SHA,
        product_id="fixture-product",
        release_version="1.0.0",
        source_trust=source_trust,
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
    )


def materialized_scratch() -> execution.MaterializedTargetInputs:
    lock = OciTargetInputLock(
        product_id="fixture-product",
        target_id="fixture",
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
    return execution.MaterializedTargetInputs(
        lock=lock,
        evidence=OciBuildInputEvidence.empty(),
        image_ids_by_platform={"linux/amd64": {}},
    )


class OciExecutionSecurityTests(unittest.TestCase):
    def test_private_engine_namespace_binds_only_registered_implicit_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "state"
            private = root / "implicit-containers"
            system = Path(temp) / "system-containers"
            private.mkdir(parents=True)
            system.mkdir()
            mount = mock.Mock(return_value=0)
            unshare = mock.Mock()
            with mock.patch.object(
                execution.os, "unshare", unshare, create=True
            ), mock.patch.object(
                execution.os, "CLONE_NEWNS", 0x20000, create=True
            ), mock.patch.object(execution, "_MOUNT", mount):
                execution._private_engine_preexec(root, system)()
            unshare.assert_called_once_with(0x20000)
            mount.assert_any_call(
                None,
                b"/",
                None,
                execution._MOUNT_REC | execution._MOUNT_PRIVATE,
                None,
            )
            mount.assert_any_call(
                os.fsencode(private),
                os.fsencode(system),
                None,
                execution._MOUNT_BIND | execution._MOUNT_REC,
                None,
            )

    def test_every_engine_command_receives_private_namespace_preexec(self) -> None:
        root = Path("/registered/state")
        prepared = mock.Mock()
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            execution, "_private_engine_preexec", return_value=prepared
        ), mock.patch.object(
            execution, "execute_command", return_value=completed
        ) as command:
            result = execution.execute_engine_command(
                root, ["skopeo", "--version"], capture=True, env={"PATH": "/bin"}
            )
        self.assertIs(completed, result)
        command.assert_called_once_with(
            ["skopeo", "--version"],
            cwd=None,
            capture=True,
            env={"PATH": "/bin"},
            preexec_fn=prepared,
        )

    def test_engine_command_maps_private_namespace_setup_failure(self) -> None:
        with mock.patch.object(
            execution,
            "execute_command",
            side_effect=subprocess.SubprocessError("Exception occurred in preexec_fn."),
        ):
            with self.assertRaisesRegex(OciBuildError, "engine_isolation_failed"):
                execution.execute_engine_command(
                    Path("/registered/state"),
                    ["buildah", "--version"],
                )

    def test_binary_engine_capture_preserves_exact_newline_bearing_bytes(self) -> None:
        root = Path("/registered/state")
        prepared = mock.Mock()
        payload = b'{"schemaVersion":2}\n'
        completed = subprocess.CompletedProcess([], 0, payload, b"")
        with mock.patch.object(
            execution, "_private_engine_preexec", return_value=prepared
        ), mock.patch.object(
            execution, "execute_binary_command", return_value=completed
        ) as command:
            result = execution.capture_engine_bytes(
                root,
                ["skopeo", "inspect", "--raw", "oci:/verified/layout"],
                env={"PATH": "/bin"},
            )
        self.assertEqual(payload, result)
        command.assert_called_once_with(
            ["skopeo", "inspect", "--raw", "oci:/verified/layout"],
            cwd=None,
            env={"PATH": "/bin"},
            preexec_fn=prepared,
        )

    def test_private_engine_namespace_rejects_relative_registered_state(self) -> None:
        with self.assertRaisesRegex(OciBuildError, "engine_isolation_failed"):
            execution._private_engine_preexec(Path("relative-state"))

    def test_layer_inventory_accepts_busybox_and_container_root_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                for name, linkname, member_type in (
                    ("bin/sh", "bin/[", tarfile.LNKTYPE),
                    ("usr/bin/env", "../../bin/env", tarfile.SYMTYPE),
                    ("lib64", "lib", tarfile.SYMTYPE),
                    ("var/run", "/run", tarfile.SYMTYPE),
                ):
                    member = tarfile.TarInfo(name)
                    member.type = member_type
                    member.linkname = linkname
                    archive.addfile(member)
            payload = buffer.getvalue()
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
            blob.parent.mkdir(parents=True)
            blob.write_bytes(payload)
            self.assertEqual(
                {"/bin/sh", "/usr/bin/env", "/lib64", "/var/run"},
                safe._layer_paths(layout, (digest,)),
            )

    def test_layer_inventory_still_rejects_symlink_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                member = tarfile.TarInfo("safe/link")
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
                archive.addfile(member)
            payload = buffer.getvalue()
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
            blob.parent.mkdir(parents=True)
            blob.write_bytes(payload)
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                safe._layer_paths(layout, (digest,))

    def test_layer_inventory_rejects_hard_link_archive_root_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                member = tarfile.TarInfo("safe/link")
                member.type = tarfile.LNKTYPE
                member.linkname = "../outside"
                archive.addfile(member)
            payload = buffer.getvalue()
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
            blob.parent.mkdir(parents=True)
            blob.write_bytes(payload)
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                safe._layer_paths(layout, (digest,))

    def test_layer_inventory_streams_verified_blobs_without_read_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                member = tarfile.TarInfo("usr/bin/tool")
                member.mode = 0o755
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
            payload = buffer.getvalue()
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
            blob.parent.mkdir(parents=True)
            blob.write_bytes(payload)
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError):
                self.assertEqual({"/usr/bin/tool"}, safe._layer_paths(layout, (digest,)))

    def test_whiteouts_do_not_remove_same_layer_additions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            digests: list[str] = []
            for entries in (("usr/bin/old",), ("usr/bin/new", "usr/bin/.wh.new")):
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as archive:
                    for name in entries:
                        member = tarfile.TarInfo(name)
                        member.size = 1
                        archive.addfile(member, io.BytesIO(b"x"))
                payload = buffer.getvalue()
                digest = "sha256:" + hashlib.sha256(payload).hexdigest()
                blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
                blob.parent.mkdir(parents=True, exist_ok=True)
                blob.write_bytes(payload)
                digests.append(digest)
            self.assertEqual(
                {"/usr/bin/old", "/usr/bin/new"},
                safe._layer_paths(layout, tuple(digests)),
            )

    def test_later_non_directory_ancestor_removes_lower_children(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            digests: list[str] = []
            for entries in (("usr/bin/tool",), ("usr",)):
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as archive:
                    for name in entries:
                        member = tarfile.TarInfo(name)
                        member.size = 1
                        archive.addfile(member, io.BytesIO(b"x"))
                payload = buffer.getvalue()
                digest = "sha256:" + hashlib.sha256(payload).hexdigest()
                blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
                blob.parent.mkdir(parents=True, exist_ok=True)
                blob.write_bytes(payload)
                digests.append(digest)
            self.assertEqual({"/usr"}, safe._layer_paths(layout, tuple(digests)))

    def test_build_engine_disables_network_during_context_execution(self) -> None:
        commands: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(argv, **kwargs):
            commands.append((list(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        expected = OciTargetResult(
            "fixture", "sha256:" + "b" * 64, (), {}, "not-run"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staged = root / "staged"
            state = root / "state"
            staged.mkdir()
            state.mkdir()
            (staged / "Containerfile").write_text(
                "FROM scratch\n", encoding="utf-8"
            )
            default_authfile = root / "missing-default-auth.json"
            authfile = execution._credential_free_authfile(state)
            with mock.patch.dict(
                os.environ, {"REGISTRY_AUTH_FILE": str(default_authfile)}
            ), mock.patch.object(
                execution, "execute_command", side_effect=fake_run
            ), mock.patch.object(
                execution, "inspect_layout", return_value=expected
            ):
                execution.build_target(
                    plan(),
                    target(smoke=None),
                    staged,
                    state,
                    {"example": "value"},
                    1,
                    {},
                    authfile,
                    execution.credential_free_environment(authfile),
                    materialized_scratch(),
                )
            build = next(command for command, _ in commands if "bud" in command)
            self.assertIn("--network", build)
            self.assertEqual("none", build[build.index("--network") + 1])
            self.assertIn("--identity-label=false", build)
            self.assertNotIn("--inherit-labels=false", build)
            self.assertEqual(str(authfile), build[build.index("--authfile") + 1])
            push = next(command for command, _ in commands if "push" in command)
            self.assertEqual(str(authfile), push[push.index("--authfile") + 1])
            self.assertEqual('{"auths":{}}\n', authfile.read_text(encoding="utf-8"))
            self.assertEqual(0o600, authfile.stat().st_mode & 0o777)
            self.assertFalse(default_authfile.exists())
            for command, kwargs in commands:
                with self.subTest(command=command):
                    environment = kwargs.get("env")
                    self.assertIsInstance(environment, dict)
                    self.assertEqual(str(authfile), environment["REGISTRY_AUTH_FILE"])

    def test_cleanup_unlinks_state_root_symlink_without_dereferencing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {
                "RUNNER_TEMP": temp,
                "GITHUB_RUN_ID": "4",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            root = execution.state_root(environment)
            target_root = Path(temp) / "outside"
            target_root.mkdir()
            sentinel = target_root / "manifests.json"
            sentinel.write_text('["outside"]\n', encoding="utf-8")
            root.symlink_to(target_root, target_is_directory=True)
            with mock.patch.object(execution.shutil, "which") as builder:
                execution.cleanup(environment)
            builder.assert_not_called()
            self.assertFalse(root.exists() or root.is_symlink())
            self.assertEqual('["outside"]\n', sentinel.read_text(encoding="utf-8"))

    def test_cleanup_rejects_manifest_state_symlink_without_dereferencing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {
                "RUNNER_TEMP": temp,
                "GITHUB_RUN_ID": "5",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            root = execution.state_root(environment)
            root.mkdir()
            target = Path(temp) / "outside-state.json"
            target.write_text('["outside"]\n', encoding="utf-8")
            (root / "manifests.json").symlink_to(target)
            with mock.patch.object(execution.shutil, "which", return_value=None) as builder:
                with self.assertRaisesRegex(OciBuildError, "cleanup_failed"):
                    execution.cleanup(environment)
            builder.assert_called_once_with("buildah")
            self.assertFalse(root.exists())
            self.assertEqual('["outside"]\n', target.read_text(encoding="utf-8"))

    def test_cleanup_never_uses_ambient_registry_authfile(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(argv, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            environment = {
                "RUNNER_TEMP": temp,
                "GITHUB_RUN_ID": "6",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            root = execution.state_root(environment)
            root.mkdir()
            ambient = Path(temp) / "ambient-auth.json"
            ambient.write_text('{"auths":{"registry.invalid":{"auth":"secret"}}}\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"REGISTRY_AUTH_FILE": str(ambient)}), mock.patch.object(
                execution.shutil, "which", return_value="buildah"
            ), mock.patch.object(execution.subprocess, "run", side_effect=fake_run):
                execution.cleanup(environment)
            self.assertFalse(root.exists())
            self.assertTrue(calls)
            for kwargs in calls:
                cleanup_environment = kwargs.get("env")
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
                self.assertEqual(
                    str(root / "xdg-cache"), cleanup_environment["XDG_CACHE_HOME"]
                )
                self.assertEqual(
                    str(root / "xdg-data"), cleanup_environment["XDG_DATA_HOME"]
                )
                for forbidden in (
                    "INPUT_REGISTRY_TOKEN",
                    "DOCKER_CONFIG",
                    "HTTPS_PROXY",
                    "AWS_SECRET_ACCESS_KEY",
                    "GH_TOKEN",
                ):
                    self.assertNotIn(forbidden, cleanup_environment)

    def test_isolated_smoke_uses_networkless_capability_dropped_container(self) -> None:
        commands: list[list[str]] = []
        environments: list[dict[str, str]] = []

        def fake_run(argv, **kwargs):
            commands.append(list(argv))
            environments.append(dict(kwargs["env"]))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            safe.base,
            "execute_engine_command",
            side_effect=lambda _root, argv, **kwargs: fake_run(argv, **kwargs),
        ), mock.patch.object(
            safe.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ):
            script = Path(temp) / "verify.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            result = safe._run_isolated_smoke(Path(temp), plan(), target(), script)
        self.assertEqual("isolated-script-passed", result)
        joined = "\n".join(" ".join(command) for command in commands)
        self.assertIn("run --network none --cap-drop all", joined)
        self.assertIn("--security-opt no-new-privileges", joined)
        self.assertIn("copy", joined)
        self.assertNotIn(f"/bin/bash {script}", joined)
        self.assertTrue(environments)
        for environment in environments:
            self.assertEqual(str(Path(temp) / "home"), environment["HOME"])
            self.assertEqual(
                str(Path(temp) / "xdg-cache"), environment["XDG_CACHE_HOME"]
            )
            self.assertEqual(
                str(Path(temp) / "xdg-config"), environment["XDG_CONFIG_HOME"]
            )
            self.assertEqual(
                str(Path(temp) / "xdg-data"), environment["XDG_DATA_HOME"]
            )
            self.assertEqual(
                str(Path(temp) / "xdg-runtime"), environment["XDG_RUNTIME_DIR"]
            )
            self.assertEqual(str(Path(temp) / "tmp"), environment["TMPDIR"])
            self.assertEqual(
                str(Path(temp) / "storage.conf"),
                environment["CONTAINERS_STORAGE_CONF"],
            )

    def test_isolated_smoke_rejects_a_symlinked_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "verify.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            alias = root / "verify-link.sh"
            alias.symlink_to(script)
            with self.assertRaisesRegex(OciBuildError, "invalid_path"):
                safe._run_isolated_smoke(root, plan(), target(), alias)

    def test_isolated_smoke_maps_namespace_failure_during_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            safe.base,
            "execute_engine_command",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ), mock.patch.object(
            safe.subprocess,
            "run",
            side_effect=subprocess.SubprocessError("Exception occurred in preexec_fn."),
        ):
            root = Path(temp)
            script = root / "verify.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            with self.assertRaisesRegex(OciBuildError, "cleanup_failed"):
                safe._run_isolated_smoke(root, plan(), target(), script)

    def test_execute_masks_consumer_script_before_base_builder(self) -> None:
        captured: list[OciBuildPlan] = []
        original = plan()
        base_result = OciBuildResult(
            product_id=original.product_id,
            admitted_sha=SHA,
            release_version="1.0.0",
            source_date_epoch=1,
            targets=(OciTargetResult("fixture", "sha256:" + "b" * 64, (), {}, "not-run"),),
            clean_tree=True,
            cleanup_result="not-run",
            evidence_id="c" * 64,
            canary_id=None,
            previous_known_good=None,
            rollback_id=None,
        )
        input_evidence = OciBuildInputEvidence(
            lock_digest="sha256:" + "f" * 64,
            acquisition_policy_id="public-root-only-v1",
            resolved_bases=(),
            resolved_external_inputs=(),
            evidence_id="d" * 64,
        )
        base_result = replace(
            base_result,
            targets=(replace(base_result.targets[0], build_input_evidence=input_evidence),),
        )

        def fake_execute(repository_root, source_root, received, environment, secret_files):
            captured.append(received)
            staged = safe.base.state_root(environment) / "staged" / "fixture" / "ci"
            staged.mkdir(parents=True)
            (staged / "verify.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            return base_result

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            safe.base, "execute_plan", side_effect=fake_execute
        ), mock.patch.object(
            safe, "_assert_target_filesystem"
        ), mock.patch.object(
            safe, "_run_isolated_smoke", return_value="isolated-script-passed"
        ) as isolated:
            result = safe.execute_plan(
                ROOT,
                Path(temp),
                original,
                {"RUNNER_TEMP": temp, "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1"},
            )
        self.assertIsNone(captured[0].targets[0].smoke_script)
        isolated.assert_called_once()
        self.assertEqual(
            safe.base.state_root({"RUNNER_TEMP": temp, "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1"})
            / "staged" / "fixture" / "ci" / "verify.sh",
            isolated.call_args.args[3],
        )
        self.assertEqual("isolated-script-passed", result.targets[0].smoke_result)
        self.assertEqual(input_evidence, result.targets[0].build_input_evidence)

    def test_trusted_pr_defers_consumer_script_but_keeps_central_inspection(self) -> None:
        original = plan("trusted-pr")
        base_result = OciBuildResult(
            product_id=original.product_id,
            admitted_sha=SHA,
            release_version="1.0.0",
            source_date_epoch=1,
            targets=(OciTargetResult("fixture", "sha256:" + "d" * 64, (), {}, "not-run"),),
            clean_tree=True,
            cleanup_result="not-run",
            evidence_id="e" * 64,
            canary_id=None,
            previous_known_good=None,
            rollback_id=None,
        )

        def fake_execute(repository_root, source_root, received, environment, secret_files):
            safe.base.state_root(environment).mkdir(parents=True)
            return base_result

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            safe.base, "execute_plan", side_effect=fake_execute
        ), mock.patch.object(
            safe, "_assert_target_filesystem"
        ) as asserted, mock.patch.object(
            safe, "_run_isolated_smoke"
        ) as isolated:
            result = safe.execute_plan(
                ROOT,
                Path(temp),
                original,
                {"RUNNER_TEMP": temp, "GITHUB_RUN_ID": "2", "GITHUB_RUN_ATTEMPT": "1"},
            )
        asserted.assert_called_once()
        isolated.assert_not_called()
        self.assertEqual("inspection-passed-script-deferred", result.targets[0].smoke_result)

    def test_only_the_safe_adapter_can_execute_a_consumer_smoke_script(self) -> None:
        safe_source = (ROOT / "src/ci_workflows/oci_execution_safe.py").read_text()
        base_source = (ROOT / "src/ci_workflows/oci_execution.py").read_text()
        self.assertNotIn('["/bin/bash", str(script)]', safe_source)
        self.assertNotIn('["/bin/bash", str(script)]', base_source)
        self.assertIn('"--network",', safe_source)
        self.assertIn('"none",', safe_source)
        self.assertIn('"--cap-drop",', safe_source)
        self.assertIn('"all",', safe_source)
        self.assertIn('"no-new-privileges",', safe_source)
        self.assertIn("smoke_script=None", safe_source)

    def test_cli_dispatches_through_the_public_oci_facade(self) -> None:
        source = (ROOT / "src/ci_workflows/ciw_oci.py").read_text()
        self.assertIn("from . import oci", source)
        self.assertIn("from .oci_execution_safe import cleanup, residue", source)
        self.assertIn("result = oci.build", source)
        self.assertNotIn("from .oci_execution import cleanup, execute_plan, residue", source)


if __name__ == "__main__":
    unittest.main()
