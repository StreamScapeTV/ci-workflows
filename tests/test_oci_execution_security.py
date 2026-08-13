from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
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


def prepared_roots(base_path: Path) -> execution.CapacityRoots:
    roots = execution._test_capacity_roots(base_path)
    execution.prepare_capacity_roots(roots)
    return roots


class OciExecutionSecurityTests(unittest.TestCase):
    def test_private_engine_namespace_binds_only_registered_implicit_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            execution._write_private_runtime_files(roots)
            private = roots.scratch_root / "implicit-containers"
            system = Path(temp) / "system-containers"
            system.mkdir()
            mount = mock.Mock(return_value=0)
            unshare = mock.Mock()
            with mock.patch.object(
                execution.os, "unshare", unshare, create=True
            ), mock.patch.object(
                execution.os, "CLONE_NEWNS", 0x20000, create=True
            ), mock.patch.object(execution, "_MOUNT", mount):
                execution._private_engine_preexec(roots, system)()
            unshare.assert_called_once_with(0x20000)
            mount.assert_any_call(
                None,
                b"/",
                None,
                execution._MOUNT_REC | execution._MOUNT_PRIVATE,
                None,
            )
            bind_calls = [
                call.args
                for call in mount.call_args_list
                if call.args[3] == execution._MOUNT_BIND | execution._MOUNT_REC
            ]
            self.assertEqual(2, len(bind_calls))
            for source, target, filesystem, _flags, data in bind_calls:
                self.assertRegex(source, rb"^/proc/self/fd/[0-9]+$")
                self.assertRegex(target, rb"^/proc/self/fd/[0-9]+$")
                self.assertIsNone(filesystem)
                self.assertIsNone(data)
            flattened = b"\n".join(
                value
                for call in bind_calls
                for value in call[:2]
                if isinstance(value, bytes)
            )
            self.assertNotIn(os.fsencode(roots.graph_root), flattened)
            self.assertNotIn(os.fsencode(private), flattened)
            self.assertNotIn(os.fsencode(system), flattened)
            anchor_calls = [
                call.args
                for call in mount.call_args_list
                if call.args[3] == execution._MOUNT_BIND
            ]
            self.assertEqual(2, len(anchor_calls))
            self.assertEqual(
                {os.fsencode(roots.scratch_root), os.fsencode(roots.run_root)},
                {call[1] for call in anchor_calls},
            )
            self.assertTrue(
                all(
                    re.fullmatch(rb"/proc/self/fd/[0-9]+", call[0])
                    for call in anchor_calls
                )
            )

    def test_private_engine_revalidates_bound_identity_before_mount_use(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            execution._write_private_runtime_files(roots)
            system = Path(temp) / "system-containers"
            system.mkdir()
            graph_inode = roots.graph_root.stat().st_ino
            namespace_entered = False
            real_identity = execution._directory_identity

            def unshare(_flags: int) -> None:
                nonlocal namespace_entered
                namespace_entered = True

            def changed_after_unshare(
                descriptor: int,
            ) -> execution.DirectoryIdentity:
                identity = real_identity(descriptor)
                if namespace_entered and identity.inode == graph_inode:
                    return execution.DirectoryIdentity(
                        identity.device, identity.inode + 1, identity.mount_id
                    )
                return identity

            mount = mock.Mock(return_value=0)
            with mock.patch.object(
                execution.os, "unshare", side_effect=unshare, create=True
            ), mock.patch.object(
                execution.os, "CLONE_NEWNS", 0x20000, create=True
            ), mock.patch.object(execution, "_MOUNT", mount), mock.patch.object(
                execution,
                "_directory_identity",
                side_effect=changed_after_unshare,
            ):
                with self.assertRaisesRegex(OSError, "capacity identity changed"):
                    execution._private_engine_preexec(roots, system)()
            self.assertFalse(
                any(
                    call.args[3]
                    == execution._MOUNT_BIND | execution._MOUNT_REC
                    for call in mount.call_args_list
                )
            )

    def test_private_engine_refuses_nested_same_filesystem_mount(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            execution._write_private_runtime_files(roots)
            private = roots.scratch_root / "implicit-containers"
            nested_inode = private.stat().st_ino
            system = Path(temp) / "system-containers"
            system.mkdir()
            real_mount_id = execution._mount_id_for_fd

            def nested_mount_id(descriptor: int) -> int:
                if execution.os.fstat(descriptor).st_ino == nested_inode:
                    return real_mount_id(descriptor) + 1
                return real_mount_id(descriptor)

            mount = mock.Mock(return_value=0)
            with mock.patch.object(
                execution.os, "unshare", mock.Mock(), create=True
            ), mock.patch.object(
                execution.os, "CLONE_NEWNS", 0x20000, create=True
            ), mock.patch.object(execution, "_MOUNT", mount), mock.patch.object(
                execution,
                "_mount_id_for_fd",
                side_effect=nested_mount_id,
            ):
                with self.assertRaisesRegex(OSError, "capacity child mount changed"):
                    execution._private_engine_preexec(roots, system)()
            mount.assert_not_called()

    def test_private_engine_rejects_graph_descendant_mount_before_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            execution._write_private_runtime_files(roots)
            descendant = roots.graph_root / "unexpected-bind"
            descendant.mkdir()
            descendant_inode = descendant.stat().st_ino
            system = Path(temp) / "system-containers"
            system.mkdir()
            real_mount_id = execution._mount_id_for_fd

            def descendant_mount_id(descriptor: int) -> int:
                if execution.os.fstat(descriptor).st_ino == descendant_inode:
                    return real_mount_id(descriptor) + 1
                return real_mount_id(descriptor)

            mount = mock.Mock(return_value=0)
            with mock.patch.object(
                execution.os, "unshare", mock.Mock(), create=True
            ), mock.patch.object(
                execution.os, "CLONE_NEWNS", 0x20000, create=True
            ), mock.patch.object(execution, "_MOUNT", mount), mock.patch.object(
                execution,
                "_mount_id_for_fd",
                side_effect=descendant_mount_id,
            ):
                with self.assertRaisesRegex(OSError, "descendant mount changed"):
                    execution._private_engine_preexec(roots, system)()
            self.assertFalse(
                any(
                    call.args[3] & execution._MOUNT_BIND
                    for call in mount.call_args_list
                )
            )

    def test_private_engine_rejects_canonical_path_swap_during_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            execution._write_private_runtime_files(roots)
            system = Path(temp) / "system-containers"
            system.mkdir()
            moved = roots.scratch_parent / "moved-owned"
            replacement = roots.scratch_root
            swapped = False

            def mount(_source, target, _filesystem, _flags, _data):
                nonlocal swapped
                if target == os.fsencode(roots.scratch_root) and not swapped:
                    swapped = True
                    roots.scratch_root.rename(moved)
                    replacement.mkdir(mode=0o700)
                    (replacement / "sentinel").write_text(
                        "keep\n", encoding="utf-8"
                    )
                return 0

            with mock.patch.object(
                execution.os, "unshare", mock.Mock(), create=True
            ), mock.patch.object(
                execution.os, "CLONE_NEWNS", 0x20000, create=True
            ), mock.patch.object(execution, "_MOUNT", side_effect=mount):
                with self.assertRaisesRegex(OSError, "capacity pathname changed"):
                    execution._private_engine_preexec(roots, system)()
            self.assertEqual(
                "keep\n",
                (replacement / "sentinel").read_text(encoding="utf-8"),
            )
            self.assertTrue((moved / execution._CAPACITY_MARKER).is_file())

    def test_every_engine_command_receives_private_namespace_preexec(self) -> None:
        roots = mock.sentinel.capacity_roots
        prepared = mock.Mock()
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            execution, "_private_engine_preexec", return_value=prepared
        ), mock.patch.object(
            execution, "execute_command", return_value=completed
        ) as command:
            result = execution.execute_engine_command(
                roots, ["skopeo", "--version"], capture=True, env={"PATH": "/bin"}
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
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            with mock.patch.object(
                execution,
                "execute_command",
                side_effect=subprocess.SubprocessError("Exception occurred in preexec_fn."),
            ):
                with self.assertRaisesRegex(OciBuildError, "engine_isolation_failed"):
                    execution.execute_engine_command(roots, ["buildah", "--version"])

    def test_binary_engine_capture_preserves_exact_newline_bearing_bytes(self) -> None:
        roots = mock.sentinel.capacity_roots
        prepared = mock.Mock()
        payload = b'{"schemaVersion":2}\n'
        completed = subprocess.CompletedProcess([], 0, payload, b"")
        with mock.patch.object(
            execution, "_private_engine_preexec", return_value=prepared
        ), mock.patch.object(
            execution, "execute_binary_command", return_value=completed
        ) as command:
            result = execution.capture_engine_bytes(
                roots,
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

    def test_capacity_roots_reject_relative_parents(self) -> None:
        with self.assertRaisesRegex(OciBuildError, "capacity_root_invalid"):
            execution.CapacityRoots(
                "oci-build", "ciw-oci", "0" * 20,
                Path("relative"), Path("/graph"), Path("/run"), False,
            )

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

    def test_layer_inventory_accepts_container_root_absolute_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                for name, linkname in (
                    ("var/run", "/run"),
                    ("etc/localtime", "/usr/share/zoneinfo/Etc/UTC"),
                    ("etc/os-release", "../usr/lib/os-release"),
                ):
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.SYMTYPE
                    member.linkname = linkname
                    archive.addfile(member)
            payload = buffer.getvalue()
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
            blob.parent.mkdir(parents=True)
            blob.write_bytes(payload)
            self.assertEqual(
                {"/var/run", "/etc/localtime", "/etc/os-release"},
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
            "fixture",
            "sha256:" + "b" * 64,
            (),
            {},
            "not-run",
            publication_manifest_digest="sha256:" + "c" * 64,
        )
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            root = roots.scratch_root
            staged = root / "staged"
            staged.mkdir()
            (staged / "Containerfile").write_text(
                "FROM scratch\n", encoding="utf-8"
            )
            default_authfile = root / "missing-default-auth.json"
            authfile = execution._credential_free_authfile(root)
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
                    roots,
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

    def test_cleanup_unlinks_substituted_leaf_without_dereferencing_and_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            root = roots.scratch_root
            target_root = Path(temp) / "outside"
            target_root.mkdir()
            sentinel = target_root / "manifests.json"
            sentinel.write_text('["outside"]\n', encoding="utf-8")
            shutil.rmtree(root)
            root.symlink_to(target_root, target_is_directory=True)
            with mock.patch.object(execution.shutil, "which") as builder:
                with self.assertRaisesRegex(OciBuildError, "cleanup_failed"):
                    execution.cleanup({}, _capacity_roots=roots)
            builder.assert_not_called()
            self.assertFalse(root.exists() or root.is_symlink())
            self.assertEqual('["outside"]\n', sentinel.read_text(encoding="utf-8"))
            self.assertFalse(roots.graph_root.exists())
            self.assertFalse(roots.run_root.exists())

    def test_cleanup_rejects_manifest_state_symlink_without_dereferencing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            root = roots.scratch_root
            execution._write_private_runtime_files(roots)
            target = Path(temp) / "outside-state.json"
            target.write_text('["outside"]\n', encoding="utf-8")
            (root / "manifests.json").symlink_to(target)
            with mock.patch.object(execution.shutil, "which", return_value=None) as builder:
                with self.assertRaisesRegex(OciBuildError, "cleanup_failed"):
                    execution.cleanup({}, _capacity_roots=roots)
            builder.assert_called_once_with("buildah")
            self.assertFalse(root.exists())
            self.assertEqual('["outside"]\n', target.read_text(encoding="utf-8"))

    def test_cleanup_never_uses_ambient_registry_authfile(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(argv, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            root = roots.scratch_root
            execution._write_private_runtime_files(roots)
            ambient = Path(temp) / "ambient-auth.json"
            ambient.write_text('{"auths":{"registry.invalid":{"auth":"secret"}}}\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"REGISTRY_AUTH_FILE": str(ambient)}), mock.patch.object(
                execution.shutil, "which", return_value="buildah"
            ), mock.patch.object(execution.subprocess, "run", side_effect=fake_run):
                execution.cleanup({}, _capacity_roots=roots)
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
            roots = prepared_roots(Path(temp) / "capacity")
            script = Path(temp) / "verify.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            result = safe._run_isolated_smoke(roots, plan(), target(), script)
        self.assertEqual("isolated-script-passed", result)
        joined = "\n".join(" ".join(command) for command in commands)
        self.assertIn("run --network none --cap-drop all", joined)
        self.assertIn("--security-opt no-new-privileges", joined)
        self.assertIn("copy", joined)
        self.assertNotIn(f"/bin/bash {script}", joined)
        self.assertTrue(environments)
        for environment in environments:
            self.assertEqual(str(roots.scratch_root / "home"), environment["HOME"])
            self.assertEqual(
                str(roots.scratch_root / "xdg-cache"), environment["XDG_CACHE_HOME"]
            )
            self.assertEqual(
                str(roots.scratch_root / "xdg-config"), environment["XDG_CONFIG_HOME"]
            )
            self.assertEqual(
                str(roots.scratch_root / "xdg-data"), environment["XDG_DATA_HOME"]
            )
            self.assertEqual(
                str(roots.scratch_root / "xdg-runtime"), environment["XDG_RUNTIME_DIR"]
            )
            self.assertEqual(str(roots.scratch_root / "tmp"), environment["TMPDIR"])
            self.assertEqual(
                str(roots.scratch_root / "storage.conf"),
                environment["CONTAINERS_STORAGE_CONF"],
            )

    def test_isolated_smoke_rejects_a_symlinked_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            script = Path(temp) / "verify.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            alias = Path(temp) / "verify-link.sh"
            alias.symlink_to(script)
            with self.assertRaisesRegex(OciBuildError, "invalid_path"):
                safe._run_isolated_smoke(roots, plan(), target(), alias)

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
            roots = prepared_roots(Path(temp) / "capacity")
            script = Path(temp) / "verify.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            with self.assertRaisesRegex(OciBuildError, "cleanup_failed"):
                safe._run_isolated_smoke(roots, plan(), target(), script)

    def test_execute_masks_consumer_script_before_base_builder(self) -> None:
        captured: list[OciBuildPlan] = []
        original = plan()
        base_result = OciBuildResult(
            product_id=original.product_id,
            admitted_sha=SHA,
            release_version="1.0.0",
            source_date_epoch=1,
            targets=(OciTargetResult(
                "fixture",
                "sha256:" + "b" * 64,
                (),
                {},
                "not-run",
                publication_manifest_digest="sha256:" + "c" * 64,
            ),),
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

        roots: execution.CapacityRoots

        def fake_execute(
            repository_root, source_root, received, environment, secret_files, *,
            _capacity_roots,
        ):
            captured.append(received)
            self.assertIs(roots, _capacity_roots)
            staged = roots.scratch_root / "staged" / "fixture" / "ci"
            staged.mkdir(parents=True)
            (staged / "verify.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            return base_result

        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            with mock.patch.object(
                safe.base, "execute_plan", side_effect=fake_execute
            ), mock.patch.object(
            safe, "_assert_target_filesystem"
            ), mock.patch.object(
                safe, "_run_isolated_smoke", return_value="isolated-script-passed"
            ) as isolated:
                result = safe.execute_plan(
                    ROOT, Path(temp), original, {}, _capacity_roots=roots
                )
        self.assertIsNone(captured[0].targets[0].smoke_script)
        isolated.assert_called_once()
        self.assertEqual(
            roots.scratch_root / "staged" / "fixture" / "ci" / "verify.sh",
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
            targets=(OciTargetResult(
                "fixture",
                "sha256:" + "d" * 64,
                (),
                {},
                "not-run",
                publication_manifest_digest="sha256:" + "f" * 64,
            ),),
            clean_tree=True,
            cleanup_result="not-run",
            evidence_id="e" * 64,
            canary_id=None,
            previous_known_good=None,
            rollback_id=None,
        )

        roots: execution.CapacityRoots

        def fake_execute(
            repository_root, source_root, received, environment, secret_files, *,
            _capacity_roots,
        ):
            self.assertIs(roots, _capacity_roots)
            return base_result

        with tempfile.TemporaryDirectory() as temp:
            roots = prepared_roots(Path(temp) / "capacity")
            with mock.patch.object(
                safe.base, "execute_plan", side_effect=fake_execute
            ), mock.patch.object(
                safe, "_assert_target_filesystem"
            ) as asserted, mock.patch.object(
                safe, "_run_isolated_smoke"
            ) as isolated:
                result = safe.execute_plan(
                    ROOT, Path(temp), original, {}, _capacity_roots=roots
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
