from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/maven.yml"

ARTIFACTS = (
    "streamscape-playback-api",
    "streamscape-playback-runtime",
    "streamscape-platform-android",
    "streamscape-engine-media3",
    "streamscape-engine-mpv",
    "streamscape-engine-vlc",
    "streamscape-playback-android",
)
FORGEJO_TASKS = (
    ":playback-api:publishAllPublicationsToForgejoRepository",
    ":playback-runtime:publishAllPublicationsToForgejoRepository",
    ":platform-android:publishAllPublicationsToForgejoRepository",
    ":engine-media3:publishAllPublicationsToForgejoRepository",
    ":engine-mpv:publishAllPublicationsToForgejoRepository",
    ":engine-vlc:publishAllPublicationsToForgejoRepository",
    ":playback-android:publishAllPublicationsToForgejoRepository",
)


class MavenWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)
        self.job = self.workflow["jobs"]["publish"]

    def step(self, name: str) -> dict:
        return next(step for step in self.job["steps"] if step.get("name") == name)

    def test_api_is_media_fixed_and_has_no_arbitrary_execution_inputs(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertNotIn("inputs", call)
        self.assertEqual(
            set(call["secrets"]),
            {
                "FORGEJO_REGISTRY_USERNAME",
                "FORGEJO_REGISTRY_TOKEN",
                "CIW_MAVEN_PACKAGE_READ_TOKEN",
                "TS_OAUTH_CLIENT_ID",
                "TS_OAUTH_SECRET",
                "GOOGLE_DRIVE_CLIENT_ID",
                "GOOGLE_DRIVE_CLIENT_SECRET",
                "GOOGLE_DRIVE_REFRESH_TOKEN",
                "GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID",
            },
        )
        self.assertTrue(all(value["required"] for value in call["secrets"].values()))
        self.assertEqual(self.job["runs-on"], "ubuntu-24.04")
        checkout = self.step("Check out exact Streamscape Media source")
        self.assertEqual(checkout["with"]["repository"], "StreamScapeTV/streamscape-media")
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertNotIn("arguments_json", self.text)
        self.assertNotIn("runner_label", self.text)
        self.assertNotIn("registry_url", self.text)

    def test_publication_identity_is_develop_exact_tag_or_manual_branch_only(self) -> None:
        script = self.step("Resolve exact publication identity")["run"]
        self.assertIn('test "${CALLER_REPOSITORY}" = "StreamScapeTV/streamscape-media"', script)
        self.assertIn("refs/heads/develop)", script)
        self.assertIn('"refs/tags/v${base_version}")', script)
        self.assertIn('test "${CALLER_EVENT}" = "workflow_dispatch"', script)
        self.assertIn('release_version="${base_version}-develop.${source_sha:0:12}"', script)
        self.assertIn('release_version="${base_version}-manual.${source_sha:0:12}"', script)
        self.assertIn('test "${source_sha}" = "${CALLER_SHA}"', script)

    def test_private_network_and_write_auth_are_bounded_to_publication(self) -> None:
        network = self.step("Connect to private Forgejo service")
        self.assertEqual(network["uses"], "StreamScapeTV/ci-workflows/actions/private-git@main")
        publish = self.step("Publish seven modules to Forgejo and local Maven staging")
        self.assertEqual(
            set(publish["env"]),
            {"RELEASE_VERSION", "FORGEJO_REGISTRY_USERNAME", "FORGEJO_REGISTRY_TOKEN"},
        )
        script = publish["run"]
        for task in FORGEJO_TASKS:
            self.assertEqual(script.count(task), 1)
            self.assertIn(task.replace("Forgejo", "StreamscapeLocal"), script)
        self.assertEqual(script.count("./gradlew --no-daemon"), 1)

        readback = self.step("Read back every staged Maven file with bounded read auth")
        self.assertEqual(set(readback["env"]), {"MANIFEST", "CIW_MAVEN_PACKAGE_READ_TOKEN"})
        self.assertNotIn("FORGEJO_REGISTRY_TOKEN", readback["env"])
        self.assertIn('Authorization: token ${CIW_MAVEN_PACKAGE_READ_TOKEN}', readback["run"])
        self.assertIn('test "${actual_sha256}" = "${expected_sha256}"', readback["run"])

    def test_existing_forgejo_version_is_rejected_before_write_auth(self) -> None:
        steps = self.job["steps"]
        names = [step.get("name") for step in steps]
        preflight = self.step("Reject pre-existing Forgejo Maven version")
        publish = self.step("Publish seven modules to Forgejo and local Maven staging")

        self.assertLess(names.index("Connect to private Forgejo service"), names.index(preflight["name"]))
        self.assertLess(names.index(preflight["name"]), names.index(publish["name"]))
        self.assertEqual(
            set(preflight["env"]),
            {"RELEASE_VERSION", "CIW_MAVEN_PACKAGE_READ_TOKEN"},
        )
        self.assertNotIn("FORGEJO_REGISTRY_TOKEN", preflight["env"])
        self.assertNotIn("FORGEJO_REGISTRY_USERNAME", preflight["env"])
        script = preflight["run"]
        for artifact in ARTIFACTS:
            self.assertIn(artifact, script)
        self.assertIn('Authorization: token ${CIW_MAVEN_PACKAGE_READ_TOKEN}', script)
        self.assertIn("Maven version already exists; refusing overwrite", script)
        self.assertIn("404) ;;", script)
        self.assertIn("200)", script)
        self.assertIn("Forgejo Maven version preflight failed with HTTP", script)

    def test_drive_mirror_is_nested_versioned_and_immutable(self) -> None:
        archive = self.step("Store immutable Maven-layout archive in Google Drive")
        manifest = self.step("Store immutable release manifest in Google Drive")
        for step, file_name in ((archive, "maven-layout.zip"), (manifest, "manifest.json")):
            self.assertEqual(
                step["uses"],
                "StreamScapeTV/ci-workflows/actions/google-drive@agent2/issue-637-media-maven-release",
            )
            self.assertEqual(step["with"]["repository"], "StreamScapeTV/streamscape-media")
            self.assertEqual(step["with"]["ref"], "releases")
            self.assertEqual(step["with"]["subdirectory"], "${{ steps.identity.outputs.release_version }}")
            self.assertEqual(step["with"]["file_name"], file_name)
            self.assertTrue(step["with"]["immutable"])
        self.assertNotIn("latest", self.text.lower())

    def test_mirror_builder_is_deterministic_and_records_every_file_digest(self) -> None:
        run = self.step("Build deterministic Maven-layout mirror and manifest")["run"]
        lines = run.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith('python3 - "${archive}"'))
        end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line == "PY")
        script = "\n".join(lines[start + 1 : end]) + "\n"

        version = "1.2.3-develop.0123456789ab"
        source_sha = "0123456789abcdef0123456789abcdef01234567"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for artifact in ARTIFACTS:
                version_dir = root / "dist/maven/com/streamscape/media" / artifact / version
                version_dir.mkdir(parents=True)
                (version_dir / f"{artifact}-{version}.pom").write_text(
                    f"<project><artifactId>{artifact}</artifactId></project>\n",
                    encoding="utf-8",
                )
                (version_dir / f"{artifact}-{version}.jar").write_bytes(
                    f"binary:{artifact}:{version}".encode()
                )

            outputs = []
            manifests = []
            for suffix in ("a", "b"):
                archive = root / f"mirror-{suffix}.zip"
                manifest = root / f"manifest-{suffix}.json"
                completed = subprocess.run(
                    [sys.executable, "-", str(archive), str(manifest), version, source_sha],
                    input=script,
                    text=True,
                    cwd=root,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                outputs.append(archive.read_bytes())
                manifests.append(manifest.read_bytes())

            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(manifests[0], manifests[1])
            value = json.loads(manifests[0])
            self.assertEqual(value["source_sha"], source_sha)
            self.assertEqual(value["maven_version"], version)
            self.assertEqual(
                value["module_coordinates"],
                [f"com.streamscape.media:{artifact}:{version}" for artifact in ARTIFACTS],
            )
            self.assertEqual(len(value["files"]), len(ARTIFACTS) * 2)
            self.assertTrue(all(len(item["sha256"]) == 64 for item in value["files"]))
            self.assertEqual(len(value["archive"]["sha256"]), 64)
            with zipfile.ZipFile(root / "mirror-a.zip") as zf:
                self.assertEqual(len(zf.namelist()), len(ARTIFACTS) * 2)


if __name__ == "__main__":
    unittest.main()
