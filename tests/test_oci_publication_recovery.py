from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import oci_publish as runtime
from ci_workflows import oci_publish_contract as public
from ci_workflows import oci_publish_guards as guards
from ci_workflows.oci_publish import OciPublishError, PublishRequest
from tests.test_oci_publication import SHA, _make_layout

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/oci-publish/oci-products.json"


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
        publication_root = guards.publication_state_root(self.env)
        publication_root.mkdir(mode=0o700)
        (publication_root / "registry-auth.json").write_text("{}\n", encoding="utf-8")
        (publication_root / "registry-auth.json").chmod(0o600)

    def test_layout_marker_is_required_before_registry_parity(self) -> None:
        marker = self.build_layout / "oci-layout"
        marker.write_text('{"imageLayoutVersion":"9.9"}\n', encoding="utf-8")
        with self.assertRaisesRegex(OciPublishError, "oci_layout_malformed"):
            guards.inspect_layout(self.build_layout, self.plan.targets[0], "validation")

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
                shutil.copytree(self.build_layout, destination_path)
                return
            raise AssertionError((source, destination))

        self.env["GITHUB_EVENT_NAME"] = "push"
        with patch.object(guards, "_inspect_remote_digest", side_effect=inspect), patch.object(
            runtime, "_copy", side_effect=copy
        ) as copy_call:
            published = guards.publish(
                self.plan, self.env, repository_root=self.root
            )
            self.assertEqual(published["result"], "published")
            self.assertEqual(copy_call.call_count, 2)
            readback = guards.read_back(
                self.plan, self.env, repository_root=self.root
            )
        self.assertEqual(readback["result"], "read-back")
        self.assertEqual(json.loads(readback["manifest_digests_json"])["backend"], digest)
        verified = public.verify(self.plan, self.env)
        self.assertEqual(verified["result"], "success")
        self.assertEqual(json.loads(verified["image_digest"])["backend"], digest)
        immutable = json.loads(verified["immutable_references_json"])
        self.assertEqual(immutable["release"], {"source_sha": SHA, "version": "1.2.3"})


class AuthenticationAndCleanupTests(unittest.TestCase):
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
            }
            Path(env["RUNNER_TEMP"]).mkdir()
            token = "bounded-secret-token"
            seen: dict[str, object] = {}

            def fake_run(argv, *, input_bytes=None, capture=True, check=True):
                seen["argv"] = tuple(argv)
                seen["stdin"] = input_bytes
                authfile = Path(argv[argv.index("--authfile") + 1])
                authfile.write_text("{}\n", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            with patch.object(runtime.shutil, "which", return_value="/usr/bin/skopeo"), patch.object(
                runtime, "_run", side_effect=fake_run
            ):
                result = guards.authenticate(plan, env, "publisher", token)
            self.assertEqual(result["result"], "authenticated")
            self.assertEqual(seen["stdin"], token.encode())
            self.assertNotIn(token, " ".join(seen["argv"]))
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
    def test_reusable_workflow_uses_called_identity_and_event_derived_authority(self) -> None:
        text = (
            ROOT / ".github/workflows/reusable-oci-publish.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("repository: ${{ job.workflow_repository }}", text)
        self.assertIn("ref: ${{ job.workflow_sha }}", text)
        self.assertNotIn("ref: ${{ github.workflow_sha }}", text)
        self.assertIn("github.event_name == 'workflow_dispatch'", text)
        self.assertIn("'existing-tag' || 'tag-push'", text)
        self.assertIn("Verify authority matches the public release request", text)
        self.assertIn("Publish or verify immutable version and source identities", text)


if __name__ == "__main__":
    unittest.main()
