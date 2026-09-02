from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

import yaml

from scripts.ci import streamscape_media_release as release

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
COORDINATOR = ROOT / ".github/workflows/streamscape-media-release.yml"
APPLE = ROOT / ".github/workflows/streamscape-media-apple-binary.yml"


class StreamscapeMediaReleaseTests(unittest.TestCase):
    def test_dispatch_accepts_any_exact_tag_name_and_has_no_release_inputs(self) -> None:
        workflow = yaml.safe_load(DISPATCH.read_text())
        steps = workflow["jobs"]["request"]["steps"]
        admission = next(s for s in steps if s.get("name") == "Validate Streamscape Media release request")
        self.assertEqual(admission["if"], "${{ steps.claim.outputs.workflow_key == 'release.streamscape-media' }}")
        script = admission["run"]
        env = {
            **os.environ,
            "TEST_PROFILE": "publish",
            "SOURCE_REPOSITORY": "StreamScapeTV/streamscape-media",
            "REQUEST_REF": "2.0.0",
            "REQUEST_IS_TAG": "true",
            "INPUTS_JSON": "{}",
        }
        for supported_ref in ("2.0.0", "2.0.0-RC.1", "alpha-3", "release/candidate-7", "tag'with-quote"):
            ok = subprocess.run(
                ["bash", "-c", script],
                env={**env, "REQUEST_REF": supported_ref},
                capture_output=True,
                text=True,
            )
            self.assertEqual(ok.returncode, 0, (supported_ref, ok.stderr))
        for patch in (
            {"REQUEST_REF": ""}, {"REQUEST_IS_TAG": "false"},
            {"INPUTS_JSON": '{"version":"2.0.0"}'},
            {"INPUTS_JSON": '{"sha":"' + "a" * 40 + '"}'},
        ):
            result = subprocess.run(["bash", "-c", script], env={**env, **patch}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, patch)
        job = workflow["jobs"]["streamscape_media_release"]
        self.assertEqual(job["uses"], "./.github/workflows/streamscape-media-release.yml")
        self.assertEqual(set(job["with"]), {"repository", "ref", "source_is_tag", "ci_run_id"})
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_coordinator_resolves_exact_tag_and_reads_product_version(self) -> None:
        workflow = yaml.safe_load(COORDINATOR.read_text())
        call = workflow["on"]["workflow_call"]
        self.assertEqual(set(call["inputs"]), {"repository", "ref", "source_is_tag", "ci_run_id"})
        jobs = workflow["jobs"]
        prepare = {s.get("name"): s for s in jobs["prepare"]["steps"] if s.get("name")}
        validation = prepare["Validate Streamscape Media tag request"]["run"]
        self.assertIn('test -n "${TAG}"', validation)
        self.assertNotIn('^v?', validation)
        checkout = next(s for s in jobs["prepare"]["steps"] if s.get("name") == "Check out immutable release tag")
        self.assertEqual(checkout["with"]["ref"], "refs/tags/${{ inputs.ref }}")
        identity = prepare["Resolve tag-selected release identity"]["run"]
        self.assertIn("< VERSION", identity)
        self.assertIn('[A-Za-z0-9._+-]', identity)
        self.assertNotIn('version#v', identity)
        self.assertIn('source_sha="$(git rev-parse HEAD)"', identity)
        self.assertEqual(prepare["Record tag-resolved source provenance"]["with"]["phase"], "observe-source")
        self.assertEqual(jobs["android_maven"]["with"]["ref"], "${{ needs.prepare.outputs.source_sha }}")
        self.assertEqual(jobs["android_maven"]["with"]["build_number"], "${{ needs.prepare.outputs.version }}")
        self.assertEqual(jobs["android_maven"]["with"]["ci_run_id"], "")
        self.assertEqual(jobs["apple_binary"]["with"]["ref"], "${{ needs.prepare.outputs.source_sha }}")
        publish = {s.get("name"): s for s in jobs["finalize"]["steps"] if s.get("name")}["Create and publish immutable cross-platform publication manifest"]
        self.assertEqual(publish["env"]["RELEASE_TAG"], "${{ inputs.ref }}")
        self.assertIn('--release-tag "${RELEASE_TAG}"', publish["run"] )
        self.assertEqual(COORDINATOR.read_text().count("Finish parent Agent State run"), 1)

    def test_apple_child_is_separate_fixed_publication_pipeline(self) -> None:
        workflow = yaml.safe_load(APPLE.read_text())
        self.assertEqual(set(workflow["on"]["workflow_call"]["inputs"]), {"repository", "ref", "version"})
        job = workflow["jobs"]["publish"]
        self.assertEqual(job["runs-on"], "macos-latest")
        steps = {s.get("name"): s for s in job["steps"] if s.get("name")}
        names = set(steps)
        self.assertIn("Check out pinned Central publication helper", names)
        self.assertEqual(steps["Check out pinned Central publication helper"]["with"]["repository"], "StreamScapeTV/ci-workflows")
        self.assertEqual(steps["Check out pinned Central publication helper"]["with"]["path"], "central")
        self.assertEqual(steps["Check out pinned release source"]["with"]["path"], "source")
        self.assertIn("git -C source rev-parse HEAD", steps["Validate pinned Apple release source"]["run"])
        self.assertIn("source/scripts/release/create-apple-binary-consumer-bundle.py", steps["Build deterministic Apple binary bundle"]["run"])
        self.assertIn("central/scripts/ci/streamscape_media_release.py apple", steps["Publish immutable Apple generic package files"]["run"])
        self.assertIn("--compatibility source/apple/ConsumerCompatibility.json", steps["Publish immutable Apple generic package files"]["run"])
        self.assertIn("Build deterministic Apple binary bundle", names)
        self.assertIn("Publish immutable Apple generic package files", names)
        self.assertIn("Upload private Apple publication log", names)
        self.assertNotIn("Finish parent Agent State run", names)

    def _apple_fixture(self, root: Path, version: str = "2.0.0") -> tuple[Path, Path]:
        archive = root / f"streamscape-media-{version}-apple-binary.zip"
        distribution = {
            "version": version, "commitSha": "a" * 40,
            "platforms": {"ios": {}, "tvos": {}, "macos": {}},
            "nativeRuntimeBoundary": [{"engine": "avfoundation", "included": True}],
        }
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("StreamscapeMediaApple/Distribution.json", json.dumps(distribution))
        compatibility = root / "ConsumerCompatibility.json"
        compatibility.write_text('{"ok":true}\n')
        return archive, compatibility

    def test_apple_publication_preflights_every_file_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); archive, compatibility = self._apple_fixture(root)
            calls: list[tuple[str, str]] = []
            stored: dict[str, bytes] = {}
            def fake(method: str, url: str, *, data=None):
                calls.append((method, url))
                if method == "GET":
                    return (200, stored[url]) if url in stored else (404, b"")
                stored[url] = data
                return 201, b""
            with mock.patch.object(release, "request", side_effect=fake), mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(root / "out")}):
                release.apple(type("Args", (), {
                    "version": "2.0.0", "source_sha": "a" * 40, "archive": str(archive),
                    "compatibility": str(compatibility), "output_dir": str(root / "evidence"),
                })())
            first_put = next(i for i, value in enumerate(calls) if value[0] == "PUT")
            self.assertEqual(sum(1 for method, _ in calls[:first_put] if method == "GET"), 4)
            self.assertFalse(any(method in {"DELETE", "PATCH"} for method, _ in calls))
            self.assertTrue(all("streamscape-media-apple/2.0.0/" in url for _, url in calls))

    def test_prerelease_version_is_package_safe_for_apple_publication(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); archive, compatibility = self._apple_fixture(root, "2.0.0-RC.1")
            stored: dict[str, bytes] = {}
            def fake(method: str, url: str, *, data=None):
                if method == "GET":
                    return (200, stored[url]) if url in stored else (404, b"")
                stored[url] = data
                return 201, b""
            with mock.patch.object(release, "request", side_effect=fake):
                release.apple(type("Args", (), {
                    "version": "2.0.0-RC.1", "source_sha": "a" * 40, "archive": str(archive),
                    "compatibility": str(compatibility), "output_dir": str(root / "evidence"),
                })())
            self.assertTrue(stored)
            self.assertTrue(all("streamscape-media-apple/2.0.0-RC.1/" in url for url in stored))

    def test_existing_conflicting_apple_file_fails_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); archive, compatibility = self._apple_fixture(root)
            calls = []
            def fake(method: str, url: str, *, data=None):
                calls.append(method)
                return 200, b"wrong"
            with mock.patch.object(release, "request", side_effect=fake):
                with self.assertRaises(release.ReleaseError):
                    release.apple(type("Args", (), {
                        "version": "2.0.0", "source_sha": "a" * 40, "archive": str(archive),
                        "compatibility": str(compatibility), "output_dir": str(root / "evidence"),
                    })())
            self.assertEqual(calls, ["GET"])

    def test_final_manifest_requires_same_source_and_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); manifest = root / "release-manifest.json"
            args = type("Args", (), {
                "version": "2.0.0", "release_tag": "2.0.0", "source_sha": "a"*40, "maven_source_sha": "a"*40,
                "apple_source_sha": "a"*40, "maven_publication_id": "streamscape-2.0.0",
                "maven_archive_sha256": "1"*64, "maven_manifest_sha256": "2"*64,
                "apple_archive_sha256": "3"*64, "apple_compatibility_sha256": "4"*64,
                "apple_manifest_sha256": "5"*64, "apple_platforms_json": '["ios","tvos"]',
                "apple_boundary_json": '[{"engine":"avfoundation","included":true}]', "manifest": str(manifest),
            })()
            calls=[]
            stored: dict[str, bytes] = {}
            def fake(method: str, url: str, *, data=None):
                calls.append((method,url))
                if method == "GET":
                    return (200, stored[url]) if url in stored else (404, b"")
                stored[url] = data
                return 201, b""
            with mock.patch.object(release, "request", side_effect=fake):
                release.final(args)
            value=json.loads(manifest.read_text())
            self.assertEqual(value["tag"], "2.0.0")
            self.assertEqual(value["source_sha"], "a"*40)
            self.assertEqual(value["android"]["consumer_scope"], ["android-mobile","android-tv"])
            self.assertEqual(value["apple"]["distribution"], "forgejo-generic")
            self.assertFalse(any(method in {"DELETE","PATCH"} for method,_ in calls))
            args.apple_source_sha="b"*40
            with self.assertRaises(release.ReleaseError): release.final(args)
            args.apple_source_sha="a"*40
            args.release_tag="candidate/2.0.0"
            with mock.patch.object(release, "request", side_effect=fake):
                with self.assertRaises(release.ReleaseError):
                    release.final(args)

    def test_fresh_arbitrary_tag_is_preserved_without_version_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); manifest = root / "release-manifest.json"
            args = type("Args", (), {
                "version": "2.0.0-RC.1", "release_tag": "candidate/round-1", "source_sha": "a"*40,
                "maven_source_sha": "a"*40, "apple_source_sha": "a"*40,
                "maven_publication_id": "streamscape-2.0.0-RC.1", "maven_archive_sha256": "1"*64,
                "maven_manifest_sha256": "2"*64, "apple_archive_sha256": "3"*64,
                "apple_compatibility_sha256": "4"*64, "apple_manifest_sha256": "5"*64,
                "apple_platforms_json": '["ios","tvos"]',
                "apple_boundary_json": '[{"engine":"avfoundation","included":true}]',
                "manifest": str(manifest),
            })()
            stored: dict[str, bytes] = {}
            def fake(method: str, url: str, *, data=None):
                if method == "GET":
                    return (200, stored[url]) if url in stored else (404, b"")
                stored[url] = data
                return 201, b""
            with mock.patch.object(release, "request", side_effect=fake):
                release.final(args)
            value = json.loads(manifest.read_text())
            self.assertEqual(value["tag"], "candidate/round-1")
            self.assertEqual(value["version"], "2.0.0-RC.1")

    def test_inventory_and_self_check_cover_new_surface(self) -> None:
        inventory=yaml.safe_load((ROOT/"INVENTORY.yaml").read_text())
        self.assertEqual(inventory["workflows"]["streamscape_media_release"], ".github/workflows/streamscape-media-release.yml")
        self.assertEqual(inventory["workflows"]["streamscape_media_apple_binary"], ".github/workflows/streamscape-media-apple-binary.yml")
        self.assertEqual(inventory["scripts"]["streamscape_media_release"], "scripts/ci/streamscape_media_release.py")
        self.assertIn("tests.test_streamscape_media_release", (ROOT/".github/workflows/self-check.yml").read_text())


if __name__ == "__main__": unittest.main()
