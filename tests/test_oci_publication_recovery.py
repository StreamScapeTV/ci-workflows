from __future__ import annotations

from dataclasses import replace
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
from ci_workflows.oci_publish import OciPublishError, PublishRequest
from ci_workflows.oci_types import OciBuildResult, OciTarget
from tests.test_oci_publication import SHA, _make_layout

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/oci-publish/oci-products.json"
PUBLISH_SHA = "1661da705cac03206ba7f41598457bb7726c0dc9"


def _write_build_result(
    environment: dict[str, str],
    plan,
    manifest_digests: dict[str, str],
    base_references: dict[str, list[str]] | None = None,
) -> None:
    root = runtime.build_state_root(environment)
    payload = {
        "result": "success",
        "source_sha": plan.admitted_sha,
        "product_id": plan.product_id,
        "release_version": plan.release_version,
        "manifest_digests_json": json.dumps(
            manifest_digests, sort_keys=True, separators=(",", ":")
        ),
        "publication_manifest_digests_json": json.dumps(
            manifest_digests, sort_keys=True, separators=(",", ":")
        ),
        "resolved_base_references_json": json.dumps(
            base_references
            or {target.target_id: ["scratch"] for target in plan.targets},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "clean_tree": "true",
        "artifact_exception_used": "false",
        "evidence_id": "b" * 64,
    }
    (root / "result.json").write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )


class GuardedPublicationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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
        publication_root = guards.publication_state_root(self.env)
        publication_root.mkdir(mode=0o700)
        (publication_root / "registry-auth.json").write_text("{}\n", encoding="utf-8")
        (publication_root / "registry-auth.json").chmod(0o600)

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

    def test_actual_build_result_binds_publishable_root_descriptor(self) -> None:
        publish_target = self.plan.targets[0]
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
        )
        labels = next(iter(self.local["platforms"].values()))["labels"]
        built = replace(
            build_execution.inspect_layout(
                self.build_layout,
                build_target,
                labels,
            ),
            resolved_base_references=("scratch",),
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
            evidence_id="b" * 64,
            canary_id=None,
            previous_known_good=None,
            rollback_id=None,
        )
        build_root = runtime.build_state_root(self.env)
        (build_root / "result.json").write_text(
            json.dumps(result.output_values(), sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with patch.object(
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

    def test_generic_not_found_is_not_proof_of_manifest_absence(self) -> None:
        generic = subprocess.CompletedProcess(
            args=["skopeo"], returncode=1, stdout=b"", stderr=b"network endpoint not found"
        )
        explicit = subprocess.CompletedProcess(
            args=["skopeo"], returncode=1, stdout=b"", stderr=b"manifest unknown"
        )
        with patch.object(runtime, "_run", return_value=generic):
            with self.assertRaisesRegex(OciPublishError, "registry_inspection_failed"):
                guards._inspect_remote_digest("ghcr.io/example/image:1.0.0", Path("auth"))
        with patch.object(runtime, "_run", return_value=explicit):
            self.assertIsNone(
                guards._inspect_remote_digest("ghcr.io/example/image:1.0.0", Path("auth"))
            )

    def test_verify_only_requires_both_existing_refs_and_performs_no_write(self) -> None:
        digest = str(self.local["manifest_digest"])
        with patch.object(guards, "_inspect_remote_digest", return_value=digest), patch.object(
            runtime, "_copy"
        ) as copy:
            result = guards.publish(
                self.plan,
                self.env,
                allow_publish=False,
                repository_root=self.root,
            )
        self.assertEqual(result["result"], "replayed")
        copy.assert_not_called()
        self.assertTrue((guards.publication_state_root(self.env) / "publication.json").is_file())

        values = iter((digest, None))
        with patch.object(guards, "_inspect_remote_digest", side_effect=lambda *_: next(values)), patch.object(
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

        def inspect(reference: str, _authfile: Path) -> str | None:
            return remote.get(reference)

        def copy(source: str, destination: str, _authfile: Path) -> None:
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
        with patch.object(guards, "_inspect_remote_digest", side_effect=inspect), patch.object(
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
            f"ghcr.io/streamscapetv/backend:sha-{SHA}",
        )
        self.assertNotIn("source_sha", immutable["targets"]["backend"])
        self.assertEqual(
            immutable["targets"]["backend"]["base_references"], ["scratch"]
        )

    def test_publication_rejects_missing_or_mismatched_build_base_evidence(self) -> None:
        digest = str(self.local["manifest_digest"])
        result_path = runtime.build_state_root(self.env) / "result.json"
        result_path.unlink()
        with patch.object(guards, "_inspect_remote_digest", return_value=digest):
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
        with patch.object(guards, "_inspect_remote_digest", return_value=digest):
            with self.assertRaisesRegex(OciPublishError, "build_evidence_mismatch"):
                guards.publish(
                    self.plan,
                    self.env,
                    allow_publish=False,
                    repository_root=self.root,
                )

    def test_pre_copy_recheck_rejects_new_conflict_without_writing(self) -> None:
        digest = str(self.local["manifest_digest"])
        conflict = "sha256:" + "f" * 64
        observations = iter((None, digest, conflict))
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
        publication_root = guards.publication_state_root(self.env)
        publication_root.mkdir(mode=0o700)
        (publication_root / "registry-auth.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (publication_root / "registry-auth.json").chmod(0o600)

    def test_later_target_conflict_prevents_every_registry_write(self) -> None:
        later = self.plan.targets[1]
        remote = {later.version_reference: "sha256:" + "f" * 64}

        with patch.object(
            guards,
            "_inspect_remote_digest",
            side_effect=lambda reference, _authfile: remote.get(reference),
        ) as inspect, patch.object(runtime, "_copy") as copy:
            with self.assertRaisesRegex(
                OciPublishError, "immutable_reference_conflict"
            ):
                guards.publish(self.plan, self.env, repository_root=self.root)

        self.assertEqual(
            [call.args[0] for call in inspect.call_args_list],
            [
                self.plan.targets[0].version_reference,
                self.plan.targets[0].source_reference,
                later.version_reference,
                later.source_reference,
            ],
        )
        copy.assert_not_called()
        self.assertFalse(
            (guards.publication_state_root(self.env) / "publication.json").exists()
        )

    def test_later_target_assertion_failure_prevents_every_registry_write(self) -> None:
        later = self.plan.targets[1]

        def assert_filesystem(_root, _plan, target, _layout) -> None:
            if target == later:
                raise OciPublishError("assertion_failed")

        with patch.object(
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

        def inspect(reference: str, _authfile: Path) -> str | None:
            return remote.get(reference)

        def copy(_source: str, destination: str, _authfile: Path) -> None:
            reference = destination.removeprefix("docker://")
            remote[reference] = self.local_digests[first.target_id]

        with patch.object(
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
        with patch.object(runtime, "_run", return_value=result) as run:
            runtime._copy(  # noqa: SLF001
                "oci:/tmp/layout:validation",
                "docker://ghcr.io/streamscapetv/backend:1.2.3",
                authfile,
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
            ]
        )

    def test_registry_children_receive_only_bounded_noncredential_environment(self) -> None:
        token = "must-not-reach-child-environment"
        child = subprocess.CompletedProcess(["skopeo", "version"], 0, b"", b"")
        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "HOME": "/tmp/ciw-home",
                "INPUT_REGISTRY_USERNAME": "publisher",
                "INPUT_REGISTRY_TOKEN": token,
                "GITHUB_TOKEN": "must-also-stay-out",
            },
            clear=True,
        ), patch.object(runtime.subprocess, "run", return_value=child) as run:
            runtime._run(["skopeo", "version"])  # noqa: SLF001

        child_environment = run.call_args.kwargs["env"]
        self.assertEqual(
            child_environment,
            {"HOME": "/tmp/ciw-home", "PATH": "/usr/bin"},
        )
        self.assertNotIn(token, child_environment.values())

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
            )

            def fake_run(argv, *, input_bytes=None, capture=True, check=True):
                authfile = Path(argv[argv.index("--authfile") + 1])
                authfile.chmod(0o644)
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            with patch.object(
                runtime.shutil, "which", return_value="/usr/bin/skopeo"
            ), patch.object(runtime, "_run", side_effect=fake_run):
                with self.assertRaisesRegex(OciPublishError, "registry_auth_invalid"):
                    runtime.authenticate(plan, env, "publisher", "bounded-token")

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

            def fake_run(argv, *, input_bytes=None, capture=True, check=True):
                seen["argv"] = tuple(argv)
                seen["stdin"] = input_bytes
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
                result = guards.authenticate(plan, env, "publisher", token)
            self.assertEqual(result["result"], "authenticated")
            self.assertEqual(seen["stdin"], token.encode())
            self.assertNotIn(token, " ".join(seen["argv"]))
            self.assertIs(seen["authfile_is_file"], True)
            self.assertIs(seen["authfile_is_symlink"], False)
            self.assertEqual(seen["authfile_contents"], b"{}\n")
            self.assertEqual(seen["authfile_mode"], 0o600)
            authfile = guards.publication_state_root(env) / "registry-auth.json"
            self.assertEqual(stat.S_IMODE(authfile.stat().st_mode), 0o600)
            state_text = (guards.publication_state_root(env) / "plan.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(token, state_text)
            guards.cleanup(env)
            guards.residue(env)
            self.assertFalse(guards.publication_state_root(env).exists())


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
