from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple-swiftpm.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
HELPER = ROOT / "scripts/ci/swiftpm_binary.py"


class AppleSwiftPMWorkflowTests(unittest.TestCase):
    def test_workflow_is_reusable_github_tagged_binary_lane(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        call = workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "ref", "source_is_tag", "ci_run_id", "upload_private_log"},
        )
        self.assertEqual(
            set(call["secrets"]),
            {
                "SOURCE_APP_ID", "SOURCE_APP_PRIVATE_KEY", "AGENT_STATE_SUPABASE_URL",
                "AGENT_STATE_SUPABASE_SECRET_KEY", "GOOGLE_DRIVE_CI_LOGS_FOLDER_ID",
                "GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "GOOGLE_DRIVE_REFRESH_TOKEN",
                "TS_OAUTH_CLIENT_ID", "TS_OAUTH_SECRET", "PACKAGE_PUBLISH_USERNAME",
                "PACKAGE_PUBLISH_TOKEN", "PACKAGE_READ_TOKEN",
            },
        )
        job = workflow["jobs"]["publish"]
        self.assertEqual(job["runs-on"], ["macOS", "ARM64"])
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("VERSION, Package.swift and fixed product wrapper", text)
        self.assertIn("scripts/ci/run-swiftpm-binary.sh", text)
        self.assertIn("CI_SWIFTPM_BINARY_PROFILE: prepare", text)
        self.assertIn("CI_SWIFTPM_BINARY_PROFILE=consumer", text)
        self.assertIn("https://github.com/%s.git", text)
        self.assertIn("refs/tags/${{ steps.request.outputs.version }}", text)
        self.assertIn("for attempt in 1 2", text)
        self.assertNotIn("CI_APPLE_SWIFTPM_VERSION", text)
        self.assertNotIn("CI_APPLE_SWIFTPM_SDK_SOURCE_SHA", text)
        self.assertNotIn("CI_APPLE_SWIFTPM_TOOLING_SHA", text)
        self.assertNotIn("streamscape-media-2.1.5-apple-binary.zip", text)
        self.assertNotIn("https://git.faruqi.dev/mimranfaruqi/streamscape-media.git", text)
        self.assertNotIn("Vendor/", text)
        self.assertFalse({"package_url", "artifact_base_url", "package_host", "runner", "command"} & set(call["inputs"]))

    def test_gitea_is_registry_only_and_never_a_git_mutation_target(self) -> None:
        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        helper_text = HELPER.read_text(encoding="utf-8")
        combined = workflow_text + "\n" + helper_text
        self.assertIn('REGISTRY_HOST = "git.faruqi.dev"', helper_text)
        self.assertIn('REGISTRY_PREFIX = ("api", "packages", "mimranfaruqi", "generic")', helper_text)
        self.assertIn('connection.putrequest("PUT", parsed.path)', helper_text)
        for forbidden in (
            "git push",
            'self.run(["git", "tag"',
            'self.run(["git", "commit"',
            "ci@git.faruqi.dev",
            "gitea-askpass",
            "git.faruqi.dev/mimranfaruqi/streamscape-media.git",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertNotIn("PublicationRepository", combined)
        self.assertNotIn("PackageRepository", combined)

    def test_central_owns_artifact_transport_and_product_wrapper_gets_no_registry_secret(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        prepare = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["publish"]["steps"]
        prepare_step = next(step for step in prepare if step.get("name") == "Prepare exact-source binary artifacts")
        env = prepare_step["env"]
        self.assertNotIn("PACKAGE_PUBLISH_TOKEN", env)
        self.assertNotIn("PACKAGE_READ_TOKEN", env)
        self.assertNotIn("CI_SWIFTPM_BINARY_PACKAGE_TOKEN", env)
        self.assertIn("CI_SWIFTPM_BINARY_EVIDENCE_DIR", env)
        self.assertIn("CI_SWIFTPM_BINARY_RESULT_FILE", env)
        self.assertIn("swiftpm_binary.py publish", text)
        self.assertIn("Publish and read back immutable binary cohort", text)

    def _private_controls_script(self) -> str:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        return next(
            step["run"]
            for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Prove unauthenticated private GitHub and artifact reads fail"
        )

    def _run_private_controls(self, anonymous_mode: str) -> tuple[subprocess.CompletedProcess[str], str, str, str]:
        artifact = b"private-swift-binary-artifact"
        checksum = hashlib.sha256(artifact).hexdigest()
        artifact_b64 = base64.b64encode(artifact).decode("ascii")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            curl_trace = root / "curl-trace.txt"
            git_trace = root / "git-trace.txt"

            curl_script = fake_bin / "curl"
            curl_script.write_text(
                "#!/usr/bin/env python3\n"
                "import base64\n"
                "import os\n"
                "from pathlib import Path\n"
                "import sys\n"
                "args = sys.argv[1:]\n"
                "if not args or args[0] != '--disable':\n"
                "    raise SystemExit(99)\n"
                f"trace = Path({str(curl_trace)!r})\n"
                "with trace.open('a', encoding='utf-8') as handle:\n"
                "    handle.write('HOME=' + os.environ.get('HOME', '') + '\\n')\n"
                "    handle.write('CURL_HOME=' + os.environ.get('CURL_HOME', '') + '\\n')\n"
                "    handle.write('XDG_CONFIG_HOME=' + os.environ.get('XDG_CONFIG_HOME', '') + '\\n')\n"
                "    handle.write('AMBIENT_HEADER=' + os.environ.get('AMBIENT_HEADER', '<unset>') + '\\n')\n"
                "out = Path(args[args.index('--output') + 1])\n"
                "if '--dump-header' in args:\n"
                "    Path(args[args.index('--dump-header') + 1]).write_text('HTTP/1.1 401 Unauthorized\\n', encoding='utf-8')\n"
                "authenticated = '--netrc-file' in args\n"
                f"artifact = base64.b64decode({artifact_b64!r})\n"
                "if authenticated:\n"
                "    out.write_bytes(artifact)\n"
                "    sys.stdout.write('200')\n"
                "    raise SystemExit(0)\n"
                f"mode = {anonymous_mode!r}\n"
                "if mode == 'deny':\n"
                "    out.write_bytes(b'authentication required')\n"
                "    sys.stdout.write('401')\n"
                "    raise SystemExit(0)\n"
                "if mode == 'exact-public':\n"
                "    out.write_bytes(artifact)\n"
                "    sys.stdout.write('200')\n"
                "    raise SystemExit(0)\n"
                "if mode == 'unexpected-2xx':\n"
                "    out.write_bytes(b'login page')\n"
                "    sys.stdout.write('200')\n"
                "    raise SystemExit(0)\n"
                "if mode == 'transport':\n"
                "    sys.stdout.write('000')\n"
                "    raise SystemExit(7)\n"
                "raise SystemExit(98)\n",
                encoding="utf-8",
            )
            curl_script.chmod(0o755)

            git_script = fake_bin / "git"
            git_script.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "from pathlib import Path\n"
                "import sys\n"
                f"Path({str(git_trace)!r}).write_text(\n"
                "    'HOME=' + os.environ.get('HOME', '') + '\\n'\n"
                "    + 'GIT_CONFIG_COUNT=' + os.environ.get('GIT_CONFIG_COUNT', '<unset>') + '\\n'\n"
                "    + 'AMBIENT_GIT_AUTH=' + os.environ.get('AMBIENT_GIT_AUTH', '<unset>') + '\\n',\n"
                "    encoding='utf-8',\n"
                ")\n"
                "print(\"fatal: could not read Username for 'https://github.com': terminal prompts disabled\", file=sys.stderr)\n"
                "raise SystemExit(128)\n",
                encoding="utf-8",
            )
            git_script.chmod(0o755)

            sha256sum_script = fake_bin / "sha256sum"
            sha256sum_script.write_text(
                "#!/bin/sh\n"
                "echo 'sha256sum must not be required by the macOS-portable control' >&2\n"
                "exit 97\n",
                encoding="utf-8",
            )
            sha256sum_script.chmod(0o755)

            receipt = root / "receipt.json"
            url = "https://git.faruqi.dev/api/packages/mimranfaruqi/generic/streamscape-media-apple/2.1.7/fixture.zip"
            receipt.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "packageURL": "https://github.com/StreamScapeTV/streamscape-media.git",
                        "version": "2.1.7",
                        "packageRevision": "a" * 40,
                        "artifactCount": 1,
                        "replayed": True,
                        "artifacts": [
                            {
                                "target": "FixtureXCFramework",
                                "url": url,
                                "checksum": checksum,
                                "size": len(artifact),
                            }
                        ],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            ci_log = root / "ci.log"
            ci_log.write_text("", encoding="utf-8")
            ambient_home = root / "ambient-home"
            ambient_home.mkdir()
            (ambient_home / ".curlrc").write_text(
                'header = "Authorization: Basic ambient"\n', encoding="utf-8"
            )
            result = subprocess.run(
                ["bash", "-c", self._private_controls_script()],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "HOME": str(ambient_home),
                    "CURL_HOME": str(ambient_home),
                    "XDG_CONFIG_HOME": str(ambient_home),
                    "AMBIENT_HEADER": "Authorization: Basic ambient",
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": "Authorization: Basic ambient",
                    "AMBIENT_GIT_AUTH": "present",
                    "RUNNER_TEMP": str(root),
                    "CI_LOG": str(ci_log),
                    "PUBLICATION_RECEIPT": str(receipt),
                    "PACKAGE_URL": "https://github.com/StreamScapeTV/streamscape-media.git",
                    "VERSION": "2.1.7",
                    "CI_PACKAGE_USERNAME": "fixture-user",
                    "CI_PACKAGE_READ_TOKEN": "fixture-read-token",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            return (
                result,
                ci_log.read_text(encoding="utf-8", errors="replace"),
                curl_trace.read_text(encoding="utf-8", errors="replace") if curl_trace.exists() else "",
                git_trace.read_text(encoding="utf-8", errors="replace") if git_trace.exists() else "",
            )

    def test_private_controls_cover_github_git_and_generic_artifact_reads(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        controls = self._private_controls_script()
        self.assertIn("Prove unauthenticated private GitHub and artifact reads fail", text)
        self.assertIn("PUBLICATION_RECEIPT", text)
        self.assertIn("publication_receipt=", controls)
        self.assertIn("env -i", controls)
        self.assertIn("curl --disable --silent --show-error", controls)
        self.assertNotIn("|| true", controls)
        self.assertIn("classification=authentication_refusal", controls)
        self.assertIn("classification=transport_error", controls)
        self.assertIn("classification=unexpected_2xx", controls)
        self.assertIn("artifact_authenticated_readback classification=exact", controls)
        self.assertIn("hashlib.sha256()", controls)
        self.assertIn("sha256_file", controls)
        self.assertNotIn("sha256sum", controls)
        self.assertIn("git -c credential.helper= -c core.askPass=/usr/bin/false", controls)
        self.assertIn("Private GitHub Swift package is readable without authentication", controls)
        self.assertIn("Private Swift binary artifact is readable without authentication", controls)
        self.assertIn("machine github.com", text)
        self.assertIn("machine git.faruqi.dev", text)
        self.assertIn("x-access-token", text)
        self.assertIn('"${RUNNER_TEMP}/swiftpm-private-controls"', text)

    def test_private_controls_isolate_ambient_auth_and_verify_authenticated_exact_bytes(self) -> None:
        result, private_log, curl_trace, git_trace = self._run_private_controls("deny")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("publication_receipt=", private_log)
        self.assertIn("artifact_anonymous_control classification=authentication_refusal", private_log)
        self.assertIn("artifact_authenticated_readback classification=exact", private_log)
        self.assertIn("github_unauthenticated_control git_rc=128 classification=authentication_required", private_log)
        self.assertNotIn("AMBIENT_HEADER=Authorization", curl_trace)
        self.assertGreaterEqual(curl_trace.count("AMBIENT_HEADER=<unset>"), 2)
        self.assertIn("GIT_CONFIG_COUNT=<unset>", git_trace)
        self.assertIn("AMBIENT_GIT_AUTH=<unset>", git_trace)

    def test_private_controls_fail_closed_on_transport_error(self) -> None:
        result, private_log, _, _ = self._run_private_controls("transport")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("artifact_anonymous_control classification=transport_error", private_log)
        self.assertIn("transport failure", result.stderr)

    def test_private_controls_reject_exact_anonymous_artifact_access(self) -> None:
        result, private_log, _, _ = self._run_private_controls("exact-public")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("artifact_anonymous_control classification=exact_artifact_access", private_log)
        self.assertIn("readable without authentication", result.stderr)

    def test_private_controls_reject_nonartifact_http_2xx(self) -> None:
        result, private_log, _, _ = self._run_private_controls("unexpected-2xx")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("artifact_anonymous_control classification=unexpected_2xx", private_log)
        self.assertIn("without the exact artifact", result.stderr)

    def test_release_dispatch_accepts_only_empty_swiftpm_semantics(self) -> None:
        workflow = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        validation = next(step for step in jobs["request"]["steps"] if step.get("name") == "Validate Apple release request")
        script = validation["run"]
        accepted = subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, "TEST_PROFILE": "swiftpm-package", "INPUTS_JSON": "{}"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        rejected = subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, "TEST_PROFILE": "swiftpm-package", "INPUTS_JSON": '{"url":"https://example.invalid"}'},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(rejected.returncode, 0)
        job = jobs["apple_swiftpm"]
        self.assertEqual(job["uses"], "./.github/workflows/apple-swiftpm.yml")
        self.assertEqual(set(job["with"]), {"repository", "ref", "source_is_tag", "ci_run_id"})
        self.assertEqual(job["secrets"]["PACKAGE_PUBLISH_USERNAME"], "${{ secrets.FORGEJO_REGISTRY_USERNAME }}")
        self.assertEqual(job["secrets"]["PACKAGE_PUBLISH_TOKEN"], "${{ secrets.FORGEJO_REGISTRY_TOKEN }}")
        self.assertEqual(job["secrets"]["PACKAGE_READ_TOKEN"], "${{ secrets.CIW_MAVEN_PACKAGE_READ_TOKEN }}")
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_inventory_and_self_check_publish_supported_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text(encoding="utf-8"))
        self.assertEqual(inventory["workflows"]["apple_swiftpm"], ".github/workflows/apple-swiftpm.yml")
        self.assertEqual(inventory["scripts"]["swiftpm_binary"], "scripts/ci/swiftpm_binary.py")
        self_check = (ROOT / ".github/workflows/self-check.yml").read_text(encoding="utf-8")
        self.assertIn("tests.test_apple_swiftpm_workflow", self_check)
        self.assertIn("tests.test_swiftpm_binary", self_check)


if __name__ == "__main__":
    unittest.main()
