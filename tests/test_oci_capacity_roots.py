from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ci_workflows import oci_execution as execution
from ci_workflows.oci_types import OciBuildError


class OciCapacityRootsTests(unittest.TestCase):
    def test_production_identity_is_complete_and_paths_are_fixed(self) -> None:
        environment = {
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_JOB": "publish",
            "RUNNER_TEMP": "/attacker/selected",
        }
        roots = execution.build_capacity_roots(environment)
        self.assertEqual(Path("/var/tmp/buildah"), roots.scratch_parent)
        self.assertEqual(Path("/var/lib/containers/storage"), roots.graph_parent)
        self.assertEqual(Path("/run/containers/storage"), roots.run_parent)
        self.assertNotIn("attacker", str(roots.roots))
        for missing in (
            "GITHUB_REPOSITORY",
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
            "GITHUB_JOB",
        ):
            with self.subTest(missing=missing), self.assertRaisesRegex(
                OciBuildError, "capacity_identity_invalid"
            ):
                execution.build_capacity_roots(
                    {key: value for key, value in environment.items() if key != missing}
                )
        publication = execution.build_capacity_roots(
            environment, domain="oci-publish", prefix="ciw-oci-publish"
        )
        self.assertNotEqual(roots.token, publication.token)
        self.assertNotEqual(roots.leaf_name, publication.leaf_name)
        hostile = dict(environment)
        hostile.update(
            {
                "RUNNER_TEMP": "/hostile/runner",
                "TMPDIR": "/hostile/tmp",
                "CI_SCRATCH_ROOT": "/hostile/scratch",
                "CI_GRAPH_ROOT": "/hostile/graph",
            }
        )
        self.assertEqual(roots, execution.build_capacity_roots(hostile))

    def test_production_parent_validation_requires_shape_and_exact_mounts(self) -> None:
        roots = execution.CapacityRoots(
            "oci-build",
            "ciw-oci",
            "1" * 20,
            Path("/var/tmp/buildah"),
            Path("/var/lib/containers/storage"),
            Path("/run/containers/storage"),
            True,
        )
        identities = {
            parent: execution.DirectoryIdentity(100 + index, 200 + index, 300 + index)
            for index, parent in enumerate(
                (roots.scratch_parent, roots.graph_parent, roots.run_parent)
            )
        }

        def opened(parent: Path):
            index = tuple(identities).index(parent)
            return 10 + index, identities[parent]

        with mock.patch.object(execution.sys, "platform", "linux"), mock.patch.object(
            execution.os, "geteuid", return_value=0
        ), mock.patch.object(execution, "_open_bound_parent", side_effect=opened), \
                mock.patch.object(execution.os, "close"), mock.patch.object(
                    execution,
                    "_mounted_path_ids",
                    return_value={
                        roots.scratch_parent: identities[roots.scratch_parent].mount_id,
                        roots.graph_parent: identities[roots.graph_parent].mount_id,
                    },
                ):
            with self.assertRaisesRegex(OciBuildError, "capacity_mount_invalid"):
                execution._validate_capacity_parents(roots)

        for shape in ("absent", "symlink", "file"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as temp:
                parent = Path(temp) / "parent"
                if shape == "symlink":
                    target = Path(temp) / "target"
                    target.mkdir()
                    parent.symlink_to(target, target_is_directory=True)
                elif shape == "file":
                    parent.write_text("not a directory\n", encoding="utf-8")
                with self.assertRaisesRegex(OciBuildError, "capacity_root_invalid"):
                    execution._open_bound_parent(parent)

        with mock.patch.object(execution.sys, "platform", "linux"), mock.patch.object(
            execution.os, "geteuid", return_value=0
        ), mock.patch.object(execution, "_open_bound_parent", side_effect=opened), \
                mock.patch.object(execution.os, "close"), mock.patch.object(
            execution,
            "_mounted_path_ids",
            return_value={parent: identity.mount_id for parent, identity in identities.items()},
        ):
            execution._validate_capacity_parents(roots)

    def test_prepare_creates_marker_bound_exact_mode_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            for root in roots.roots:
                self.assertEqual(0o700, root.stat().st_mode & 0o777)
                marker = root / execution._CAPACITY_MARKER
                self.assertTrue(marker.is_file())
                self.assertFalse(marker.is_symlink())
                self.assertEqual(0o600, marker.stat().st_mode & 0o777)
            execution._verify_capacity_markers(roots)

    def test_partial_marker_write_failure_removes_invocation_owned_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            with mock.patch.object(execution.os, "write", side_effect=OSError("full")):
                with self.assertRaisesRegex(OciBuildError, "capacity_root_invalid"):
                    execution.prepare_capacity_roots(roots)
            self.assertTrue(all(not root.exists() for root in roots.roots))

    def test_partial_registry_write_removes_leaves_and_parent_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            real_write = execution.os.write
            marker_writes = 0

            def fail_first_registry_write(descriptor: int, payload: bytes) -> int:
                nonlocal marker_writes
                marker_writes += 1
                if marker_writes == 4:
                    raise OSError("registry full")
                return real_write(descriptor, payload)

            with mock.patch.object(
                execution.os,
                "write",
                side_effect=fail_first_registry_write,
            ), self.assertRaisesRegex(OciBuildError, "capacity_root_invalid"):
                execution.prepare_capacity_roots(roots)
            self.assertTrue(all(not root.exists() for root in roots.roots))
            for parent in (
                roots.scratch_parent,
                roots.graph_parent,
                roots.run_parent,
            ):
                self.assertFalse((parent / execution._capacity_registry_name(roots)).exists())

    def test_allocation_mount_mismatch_rolls_back_opened_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            real_identity = execution._directory_identity
            real_open_leaf = execution._open_capacity_leaf
            leaf_inode: int | None = None

            def open_mismatched_leaf(*args, **kwargs):
                nonlocal leaf_inode
                descriptor, identity = real_open_leaf(*args, **kwargs)
                leaf_inode = identity.inode
                return descriptor, execution.DirectoryIdentity(
                    identity.device,
                    identity.inode,
                    identity.mount_id + 1,
                )

            def stable_mismatched_identity(
                descriptor: int,
            ) -> execution.DirectoryIdentity:
                identity = real_identity(descriptor)
                if leaf_inode is not None and identity.inode == leaf_inode:
                    return execution.DirectoryIdentity(
                        identity.device,
                        identity.inode,
                        identity.mount_id + 1,
                    )
                return identity

            # Production gets mnt_id from Linux fdinfo and detects even
            # same-filesystem bind boundaries; this injected seam is portable.
            with mock.patch.object(
                execution, "_open_capacity_leaf", side_effect=open_mismatched_leaf
            ), mock.patch.object(
                execution,
                "_directory_identity",
                side_effect=stable_mismatched_identity,
            ):
                with self.assertRaisesRegex(
                    OciBuildError, "capacity_mount_invalid|cleanup_failed"
                ):
                    execution.prepare_capacity_roots(roots)
            self.assertFalse(execution._capacity_residue_names(roots.scratch_parent, roots))

    def test_mismatched_preexisting_directory_is_never_traversed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            roots.scratch_root.mkdir(mode=0o700)
            sentinel = roots.scratch_root / "owned-by-someone-else"
            sentinel.write_text("keep\n", encoding="utf-8")
            sibling = roots.scratch_parent / "sibling"
            sibling.write_text("keep\n", encoding="utf-8")
            self.assertFalse(execution.remove_capacity_roots(roots))
            self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual("keep\n", sibling.read_text(encoding="utf-8"))

    def test_symlink_substitution_unlinks_only_leaf_and_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            outside = Path(temp) / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel"
            sentinel.write_text("keep\n", encoding="utf-8")
            roots.scratch_root.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(OciBuildError, "capacity_root_invalid"):
                execution._verify_capacity_markers(roots)
            self.assertFalse(execution.remove_capacity_roots(roots))
            self.assertFalse(roots.scratch_root.exists() or roots.scratch_root.is_symlink())
            self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))

    def test_marker_and_leaf_modes_and_nonregular_marker_fail_closed(self) -> None:
        for mutation in (
            "leaf-mode",
            "marker-mode",
            "marker-directory",
            "marker-fifo",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                roots = execution._test_capacity_roots(Path(temp) / "capacity")
                execution.prepare_capacity_roots(roots)
                marker = roots.scratch_root / execution._CAPACITY_MARKER
                if mutation == "leaf-mode":
                    roots.scratch_root.chmod(0o755)
                elif mutation == "marker-mode":
                    marker.chmod(0o644)
                elif mutation == "marker-directory":
                    marker.unlink()
                    marker.mkdir(mode=0o700)
                else:
                    marker.unlink()
                    execution.os.mkfifo(marker, mode=0o600)
                with self.assertRaisesRegex(OciBuildError, "capacity_marker_invalid"):
                    execution._verify_capacity_markers(roots)

    def test_cleanup_refuses_same_filesystem_child_mount_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            external_mount = roots.scratch_root / "same-filesystem-bind"
            external_mount.mkdir()
            sentinel = external_mount / "external-sentinel"
            sentinel.write_text("keep\n", encoding="utf-8")
            leaf_fd = execution._open_directory(roots.scratch_root)
            leaf_mount_id = execution._directory_identity(leaf_fd).mount_id
            child_inode = external_mount.stat().st_ino
            real_mount_id = execution._mount_id_for_fd

            def mount_id(descriptor: int) -> int:
                if execution.os.fstat(descriptor).st_ino == child_inode:
                    return leaf_mount_id + 1
                return real_mount_id(descriptor)

            try:
                with mock.patch.object(
                    execution, "_mount_id_for_fd", side_effect=mount_id
                ):
                    self.assertFalse(
                        execution._remove_tree_contents_nofollow(
                            leaf_fd, leaf_mount_id, roots
                        )
                    )
            finally:
                execution.os.close(leaf_fd)
            self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual(
                roots.scratch_root.stat().st_dev,
                external_mount.stat().st_dev,
            )

    def test_cleanup_never_deletes_replacement_after_leaf_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            real_rename = execution.os.rename
            substituted = False

            def racing_rename(source, target, *args, **kwargs):
                nonlocal substituted
                result = real_rename(source, target, *args, **kwargs)
                if source == roots.leaf_name and not substituted:
                    substituted = True
                    roots.scratch_root.mkdir(mode=0o700)
                    (roots.scratch_root / "replacement-sentinel").write_text(
                        "keep\n", encoding="utf-8"
                    )
                return result

            with mock.patch.object(execution.os, "rename", side_effect=racing_rename):
                self.assertFalse(
                    execution._remove_capacity_leaf(roots.scratch_parent, roots)
                )
            self.assertEqual(
                "keep\n",
                (roots.scratch_root / "replacement-sentinel").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertTrue(execution._capacity_residue_names(roots.scratch_parent, roots))

    def test_residue_detects_owned_leaf_renamed_outside_tombstone_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            stranded = roots.scratch_parent / "stranded-owned"
            roots.scratch_root.rename(stranded)
            with self.assertRaisesRegex(OciBuildError, "residue_detected"):
                execution.residue({}, _capacity_roots=roots)
            self.assertIn(
                "stranded-owned",
                execution._capacity_residue_names(roots.scratch_parent, roots),
            )

    def test_cleanup_recovers_tombstone_renamed_before_postcheck(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            real_rename = execution.os.rename
            stranded_name = "stranded-owned"
            raced = False

            def steal_tombstone(source, target, *args, **kwargs):
                nonlocal raced
                result = real_rename(source, target, *args, **kwargs)
                if source == roots.leaf_name and not raced:
                    raced = True
                    real_rename(
                        target,
                        stranded_name,
                        src_dir_fd=kwargs["dst_dir_fd"],
                        dst_dir_fd=kwargs["dst_dir_fd"],
                    )
                return result

            with mock.patch.object(
                execution.os, "rename", side_effect=steal_tombstone
            ):
                self.assertTrue(
                    execution._remove_capacity_leaf(roots.scratch_parent, roots)
                )
            self.assertFalse((roots.scratch_parent / stranded_name).exists())

    def test_cleanup_retries_interrupted_owned_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            blocked = roots.scratch_root / "interrupted-bind"
            blocked.mkdir()
            (blocked / "sentinel").write_text("keep\n", encoding="utf-8")
            blocked_inode = blocked.stat().st_ino
            real_mount_id = execution._mount_id_for_fd

            def interrupted_mount_id(descriptor: int) -> int:
                if execution.os.fstat(descriptor).st_ino == blocked_inode:
                    return real_mount_id(descriptor) + 1
                return real_mount_id(descriptor)

            with mock.patch.object(
                execution,
                "_mount_id_for_fd",
                side_effect=interrupted_mount_id,
            ):
                self.assertFalse(execution.remove_capacity_roots(roots))
            self.assertTrue(
                execution._capacity_residue_names(roots.scratch_parent, roots)
            )
            self.assertTrue(execution.remove_capacity_roots(roots))
            self.assertFalse(
                execution._capacity_residue_names(roots.scratch_parent, roots)
            )

    def test_cleanup_recovers_all_arbitrarily_renamed_owned_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            renamed: list[Path] = []
            siblings: list[Path] = []
            for index, root in enumerate(roots.roots):
                sibling = root.parent / f"unrelated-{index}"
                sibling.write_text("keep\n", encoding="utf-8")
                siblings.append(sibling)
                destination = root.parent / f"interrupted-owned-{index}"
                root.rename(destination)
                renamed.append(destination)
            (renamed[0] / "registry-auth.json").write_text(
                "secret residue\n", encoding="utf-8"
            )
            self.assertTrue(execution.remove_capacity_roots(roots))
            self.assertTrue(all(not path.exists() for path in renamed))
            self.assertTrue(
                all(path.read_text(encoding="utf-8") == "keep\n" for path in siblings)
            )

    def test_external_registry_recovers_renamed_markerless_owned_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            renamed: list[Path] = []
            siblings: list[Path] = []
            for index, root in enumerate(roots.roots):
                sibling = root.parent / f"unrelated-markerless-{index}"
                sibling.write_text("keep\n", encoding="utf-8")
                siblings.append(sibling)
                destination = root.parent / f"markerless-owned-{index}"
                root.rename(destination)
                (destination / execution._CAPACITY_MARKER).unlink()
                renamed.append(destination)
            (renamed[0] / "registry-auth.json").write_text(
                "secret residue\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(OciBuildError, "residue_detected"):
                execution.residue({}, _capacity_roots=roots)
            self.assertTrue(execution.remove_capacity_roots(roots))
            self.assertTrue(all(not path.exists() for path in renamed))
            self.assertTrue(
                all(path.read_text(encoding="utf-8") == "keep\n" for path in siblings)
            )

    def test_registry_keeps_terminal_rmdir_failure_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            real_rmdir = execution.os.rmdir
            failed = False

            def fail_owned_leaf_once(path, *args, **kwargs):
                nonlocal failed
                if not failed and str(path).startswith(f".{roots.leaf_name}.delete-"):
                    failed = True
                    raise OSError("interrupted rmdir")
                return real_rmdir(path, *args, **kwargs)

            with mock.patch.object(
                execution.os, "rmdir", side_effect=fail_owned_leaf_once
            ):
                self.assertFalse(execution.remove_capacity_roots(roots))
            self.assertTrue(
                execution._capacity_residue_names(roots.scratch_parent, roots)
            )
            self.assertTrue(execution.remove_capacity_roots(roots))

    def test_terminal_registry_unlink_failure_is_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            real_unlink = execution.os.unlink
            failed = False
            registry_inode = (
                roots.scratch_parent / execution._capacity_registry_name(roots)
            ).stat().st_ino

            def fail_registry_once(path, *args, **kwargs):
                nonlocal failed
                if not failed and str(path).startswith(
                    f".{roots.leaf_name}.delete-"
                ):
                    directory_fd = kwargs.get("dir_fd")
                    if directory_fd is not None:
                        try:
                            tombstone = execution.os.stat(
                                path,
                                dir_fd=directory_fd,
                                follow_symlinks=False,
                            )
                            if tombstone.st_ino == registry_inode:
                                failed = True
                                raise OSError("interrupted registry unlink")
                        except OSError:
                            if failed:
                                raise
                            pass
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(
                execution.os, "unlink", side_effect=fail_registry_once
            ):
                self.assertFalse(execution.remove_capacity_roots(roots))
            self.assertTrue(
                execution._capacity_residue_names(roots.scratch_parent, roots)
            )
            self.assertTrue(execution.remove_capacity_roots(roots))

    def test_stranded_cleanup_fails_closed_on_candidate_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            with mock.patch.object(
                execution,
                "_owned_capacity_leaf_names",
                return_value=("owned-one", "owned-two"),
            ), mock.patch.object(
                execution, "_remove_open_capacity_leaf"
            ) as remove:
                self.assertFalse(
                    execution._remove_stranded_capacity_leaf(
                        "scratch", roots.scratch_parent, roots
                    )
                )
            remove.assert_not_called()

    def test_cleanup_preserves_preexisting_invalid_registry_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            registry = roots.scratch_parent / execution._capacity_registry_name(roots)
            registry.write_text('{"not":"owned"}\n', encoding="utf-8")
            registry.chmod(0o600)
            with self.assertRaisesRegex(OciBuildError, "residue_detected"):
                execution.remove_capacity_roots(roots)
            self.assertEqual('{"not":"owned"}\n', registry.read_text(encoding="utf-8"))

    def test_cleanup_preserves_forged_valid_shaped_registry_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            other = execution._test_capacity_roots(
                Path(temp) / "other", token="2" * 20
            )
            execution.prepare_capacity_roots(other)
            source = other.scratch_parent / execution._capacity_registry_name(other)
            forged = roots.scratch_parent / (
                f".{roots.leaf_name}.delete-forged-valid-registry"
            )
            forged.write_bytes(source.read_bytes())
            forged.chmod(0o600)
            self.assertFalse(execution.remove_capacity_roots(roots))
            self.assertTrue(forged.is_file())

    def test_cleanup_preserves_replacement_created_after_capacity_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            moved = roots.scratch_parent / "moved-owned"
            real_match = execution._path_matches_directory
            replaced = False

            def replace_after_check(parent_fd, name, identity):
                nonlocal replaced
                matches = real_match(parent_fd, name, identity)
                if not replaced:
                    replaced = True
                    roots.scratch_root.rename(moved)
                    roots.scratch_root.mkdir(mode=0o700)
                    (roots.scratch_root / "auth.json").write_text(
                        "sentinel\n", encoding="utf-8"
                    )
                return matches

            with mock.patch.object(
                execution,
                "_path_matches_directory",
                side_effect=replace_after_check,
            ), self.assertRaisesRegex(OciBuildError, "cleanup_failed"):
                execution.cleanup({}, _capacity_roots=roots)
            self.assertEqual(
                "sentinel\n",
                (roots.scratch_root / "auth.json").read_text(encoding="utf-8"),
            )
            self.assertFalse(moved.exists())

    def test_cleanup_refuses_nested_mount_before_mutating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            execution.prepare_capacity_roots(roots)
            implicit = roots.scratch_root / "implicit-containers"
            implicit.mkdir(mode=0o700)
            sentinel = implicit / "external-sentinel"
            sentinel.write_text("keep\n", encoding="utf-8")
            implicit_inode = implicit.stat().st_ino
            real_mount_id = execution._mount_id_for_fd

            def nested_mount_id(descriptor: int) -> int:
                if execution.os.fstat(descriptor).st_ino == implicit_inode:
                    return real_mount_id(descriptor) + 1
                return real_mount_id(descriptor)

            with mock.patch.object(
                execution,
                "_mount_id_for_fd",
                side_effect=nested_mount_id,
            ), self.assertRaisesRegex(OciBuildError, "cleanup_failed"):
                execution.cleanup({}, _capacity_roots=roots)
            sentinels = list(roots.scratch_parent.rglob("external-sentinel"))
            self.assertEqual(1, len(sentinels))
            self.assertEqual("keep\n", sentinels[0].read_text(encoding="utf-8"))
            self.assertFalse((sentinels[0].parent / "storage").exists())

    def test_allocation_rollback_never_deletes_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            real_rename = execution.os.rename
            substituted = False

            def racing_rename(source, target, *args, **kwargs):
                nonlocal substituted
                result = real_rename(source, target, *args, **kwargs)
                if source == roots.leaf_name and not substituted:
                    substituted = True
                    # Rollback begins with run, so create the replacement in
                    # that same bound parent after its owned leaf is moved.
                    roots.run_root.mkdir(mode=0o700)
                    (roots.run_root / "replacement-sentinel").write_text(
                        "keep\n", encoding="utf-8"
                    )
                return result

            with mock.patch.object(
                execution, "_write_capacity_marker", side_effect=OSError("full")
            ), mock.patch.object(execution.os, "rename", side_effect=racing_rename):
                with self.assertRaisesRegex(OciBuildError, "cleanup_failed"):
                    execution.prepare_capacity_roots(roots)
            self.assertEqual(
                "keep\n",
                (roots.run_root / "replacement-sentinel").read_text(encoding="utf-8"),
            )

    def test_final_verification_race_uses_bound_allocation_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = execution._test_capacity_roots(Path(temp) / "capacity")
            moved_parent = roots.scratch_parent.with_name("moved-scratch-capacity")

            def replace_parent(_roots: execution.CapacityRoots) -> None:
                roots.scratch_parent.rename(moved_parent)
                roots.scratch_parent.mkdir(mode=0o700)
                raise OciBuildError("capacity_root_invalid")

            with mock.patch.object(
                execution,
                "_verify_capacity_markers",
                side_effect=replace_parent,
            ):
                with self.assertRaisesRegex(OciBuildError, "capacity_root_invalid"):
                    execution.prepare_capacity_roots(roots)
            self.assertFalse((moved_parent / roots.leaf_name).exists())
            self.assertFalse(roots.graph_root.exists())
            self.assertFalse(roots.run_root.exists())

    def test_parent_open_rejects_path_replacement_after_descriptor_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / "parent"
            parent.mkdir()
            moved = Path(temp) / "moved"
            real_open = execution._open_directory

            def racing_open(path: Path) -> int:
                descriptor = real_open(path)
                path.rename(moved)
                path.mkdir()
                return descriptor

            with mock.patch.object(
                execution, "_open_directory", side_effect=racing_open
            ), self.assertRaisesRegex(OciBuildError, "capacity_root_invalid"):
                execution._open_bound_parent(parent)
            self.assertTrue(parent.is_dir())
            self.assertTrue(moved.is_dir())


if __name__ == "__main__":
    unittest.main()
