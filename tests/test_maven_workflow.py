from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/maven.yml"


class MavenWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)
        self.resolve = self.workflow["jobs"]["resolve_host"]
        self.job = self.workflow["jobs"]["publish"]

    def step(self, name: str) -> dict:
        return next(step for step in self.job["steps"] if step.get("name") == name)

    def resolve_step(self, name: str) -> dict:
        return next(step for step in self.resolve["steps"] if step.get("name") == name)

    def test_api_is_product_neutral_and_bounded(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "ref", "source_is_tag", "expected_source_sha", "trusted_capability_ci_run_id", "build_number", "ci_run_id", "upload_private_log"},
        )
        self.assertEqual(
            set(call["secrets"]),
            {
                "SOURCE_APP_ID",
                "SOURCE_APP_PRIVATE_KEY",
                "AGENT_STATE_SUPABASE_URL",
                "AGENT_STATE_SUPABASE_SECRET_KEY",
                "GOOGLE_DRIVE_CI_LOGS_FOLDER_ID",
                "GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID",
                "GOOGLE_DRIVE_CLIENT_ID",
                "GOOGLE_DRIVE_CLIENT_SECRET",
                "GOOGLE_DRIVE_REFRESH_TOKEN",
                "TS_OAUTH_CLIENT_ID",
                "TS_OAUTH_SECRET",
                "MAVEN_PUBLISH_USERNAME",
                "MAVEN_PUBLISH_TOKEN",
                "MAVEN_READ_TOKEN",
            },
        )
        self.assertTrue(all(not value["required"] for value in call["secrets"].values()))
        self.assertEqual(self.resolve["runs-on"], "ubuntu-24.04")
        self.assertEqual(self.job["needs"], "resolve_host")
        self.assertEqual(
            self.job["runs-on"],
            "${{ fromJSON(needs.resolve_host.outputs.runs_on) }}",
        )
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})

        self.assertNotRegex(self.text, r"(?:inputs\.repository|SOURCE_REPOSITORY).{0,120}(?:==|!=|=~)\s*[\'\"]StreamScapeTV/[A-Za-z0-9_.-]+")

        for forbidden in (
            "Forgejo",
            "arguments_json",
            "publishAllPublications",
            "gradlew",
            "runner_label",
            "registry_url",
            "script_path",
            "prepare_command",
            "build_command",
            "release_command",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_aggregate_parent_resolves_maven_to_reviewed_macos_release_class(self) -> None:
        step = self.resolve_step("Resolve Maven publication host")
        script = step["run"]
        self.assertIn("resolve_repository_ci_host_class_for_os", script)
        self.assertIn('p_operation:"release"', script)
        self.assertIn('p_host_os:"macos"', script)
        self.assertIn("release.library-package", script)
        self.assertIn("macos-high-capacity", script)

        def run(*, trusted: bool, host_class: str = "macos-high-capacity", workflow: str = "release.library-package"):
            trusted_id = "22222222-2222-4222-8222-222222222222"
            claim = {
                "ok": True,
                "code": "ok",
                "replayed": True,
                "run": {
                    "project_key": "fixture-project",
                    "repository": "ExampleOrg/example-repository",
                    "ref": "2.1.9",
                    "is_tag": True,
                    "workflow_key": workflow,
                    "test_profile": "publish",
                },
            }
            resolution = {
                "ok": True,
                "code": "ok",
                "project_key": "fixture-project",
                "repository": "ExampleOrg/example-repository",
                "ci_run_id": trusted_id,
                "operation": "release",
                "host_os": "macos",
                "host_class": host_class,
            }
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                fake_bin = root / "bin"
                fake_bin.mkdir()
                curl = fake_bin / "curl"
                curl.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, sys\n"
                    f"claim = {claim!r}\n"
                    f"resolution = {resolution!r}\n"
                    "url = sys.argv[-1]\n"
                    "if url.endswith('/claim_ci_run'):\n"
                    "    print(json.dumps(claim))\n"
                    "elif url.endswith('/resolve_repository_ci_host_class_for_os'):\n"
                    "    print(json.dumps(resolution))\n"
                    "else:\n"
                    "    raise SystemExit(97)\n",
                    encoding="utf-8",
                )
                curl.chmod(0o755)
                output = root / "github-output"
                output.write_text("", encoding="utf-8")
                completed = subprocess.run(
                    ["bash", "-c", script],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "TRUSTED_CAPABILITY_CI_RUN_ID": trusted_id if trusted else "",
                        "SOURCE_REPOSITORY": "ExampleOrg/example-repository",
                        "SOURCE_REF": "2.1.9",
                        "SOURCE_IS_TAG": "true",
                        "AGENT_STATE_SUPABASE_URL": "https://agent-state.invalid",
                        "AGENT_STATE_SUPABASE_SECRET_KEY": "fixture-secret",
                        "GITHUB_OUTPUT": str(output),
                    },
                    capture_output=True,
                    text=True,
                )
                values = {}
                for line in output.read_text(encoding="utf-8").splitlines():
                    key, value = line.split("=", 1)
                    values[key] = value
                return completed, values

        standalone, standalone_values = run(trusted=False)
        self.assertEqual(standalone.returncode, 0, standalone.stderr)
        self.assertEqual(standalone_values["host_class"], "linux-hosted")
        self.assertEqual(json.loads(standalone_values["runs_on"]), ["ubuntu-24.04"])

        aggregate, aggregate_values = run(trusted=True)
        self.assertEqual(aggregate.returncode, 0, aggregate.stderr)
        self.assertEqual(aggregate_values["host_class"], "macos-high-capacity")
        self.assertEqual(json.loads(aggregate_values["runs_on"]), ["macOS", "ARM64"])

        wrong_parent, _ = run(trusted=True, workflow="release.maven")
        self.assertNotEqual(wrong_parent.returncode, 0)
        self.assertIn("not the exact release.library-package/publish request", wrong_parent.stderr)

    def test_source_admission_and_fixed_wrapper_contract(self) -> None:
        checkout = self.step("Check out source")
        self.assertEqual(checkout["with"]["repository"], "${{ inputs.repository || github.repository }}")
        self.assertEqual(
            checkout["with"]["ref"],
            "${{ inputs.source_is_tag && format('refs/tags/{0}', inputs.ref) || inputs.ref || github.sha }}",
        )
        self.assertFalse(checkout["with"]["persist-credentials"])

        identity = self.step("Resolve observed source SHA")
        self.assertEqual(identity["env"]["DIRECT_CALLER"], "${{ inputs.repository == '' && inputs.ref == '' }}")
        self.assertIn('test "${source_sha}" = "${CALLER_SHA}"', identity["run"])

        validate = self.step("Validate fixed Maven wrapper")["run"]
        self.assertIn('wrapper="scripts/ci/run-maven-publication.sh"', validate)
        self.assertIn('test ! -L "${wrapper}"', validate)
        self.assertIn('git ls-files --error-unmatch -- "${wrapper}"', validate)

        context = self.step("Validate Maven release context")
        self.assertEqual(context["env"]["CI_MAVEN_BUILD_NUMBER"], "${{ inputs.build_number }}")
        self.assertNotIn("CI_MAVEN_SOURCE_REF", context["env"])

        commands = self.step("Run fixed Maven publication profile")
        self.assertEqual(commands["env"]["CI_MAVEN_PROFILE"], "publish")
        self.assertEqual(commands["env"]["CI_MAVEN_BUILD_NUMBER"], "${{ inputs.build_number }}")
        self.assertEqual(commands["env"]["CI_MAVEN_SOURCE_SHA"], "${{ steps.source_identity.outputs.source_sha }}")
        self.assertIn(
            'run_logged maven-publish "${{ steps.maven_bash.outputs.executable }}" scripts/ci/run-maven-publication.sh',
            commands["run"],
        )
        self.assertNotIn("bash -lc", commands["run"])

    def test_macos_maven_resolves_verified_modern_bash_before_wrapper(self) -> None:
        names = [step.get("name") for step in self.job["steps"]]
        self.assertLess(
            names.index("Resolve modern Bash for Maven publication"),
            names.index("Run fixed Maven publication profile"),
        )
        resolver = self.step("Resolve modern Bash for Maven publication")
        self.assertEqual(resolver["id"], "maven_bash")
        self.assertEqual(resolver["env"], {"CI_MAVEN_RUNNER_OS": "${{ runner.os }}"})
        script = resolver["run"]
        self.assertIn('test "${bash_major}" -ge 4', script)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            output = root / "github-output"
            marker = root / "brew-marker"
            prefix = root / "homebrew-bash"
            real_bash = shutil.which("bash")
            self.assertIsNotNone(real_bash)

            brew = fake_bin / "brew"
            brew.write_text(
                """#!/bin/sh
set -eu
case "$1:$2" in
  list:--versions)
    test "$3" = bash
    if test -x "$FAKE_BASH_PREFIX/bin/bash"; then
      printf 'bash 5 fixture\\n'
      exit 0
    fi
    exit 1
    ;;
  install:bash)
    mkdir -p "$FAKE_BASH_PREFIX/bin"
    ln -sf "$REAL_BASH" "$FAKE_BASH_PREFIX/bin/bash"
    printf 'install\\n' >> "$BREW_MARKER"
    ;;
  --prefix:bash)
    printf '%s\\n' "$FAKE_BASH_PREFIX"
    ;;
  *)
    exit 96
    ;;
esac
""",
                encoding="utf-8",
            )
            brew.chmod(0o755)

            base_env = {
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "FAKE_BASH_PREFIX": str(prefix),
                "REAL_BASH": real_bash,
                "BREW_MARKER": str(marker),
                "GITHUB_OUTPUT": str(output),
            }

            output.write_text("", encoding="utf-8")
            linux = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, "CI_MAVEN_RUNNER_OS": "Linux"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(linux.returncode, 0, linux.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "executable=bash\n")
            self.assertFalse(marker.exists())

            output.write_text("", encoding="utf-8")
            macos = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, "CI_MAVEN_RUNNER_OS": "macOS"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(macos.returncode, 0, macos.stderr)
            modern_bash = prefix / "bin/bash"
            self.assertTrue(modern_bash.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "install\n")
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"executable={modern_bash}\n",
            )

            modern_bash.unlink()
            modern_bash.write_text("#!/bin/sh\nprintf '3'\n", encoding="utf-8")
            modern_bash.chmod(0o755)
            output.write_text("", encoding="utf-8")
            outdated = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, "CI_MAVEN_RUNNER_OS": "macOS"},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(outdated.returncode, 0)
            self.assertIn("Bash 4 or newer", outdated.stderr)

            output.write_text("", encoding="utf-8")
            unavailable = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "PATH": "/usr/bin:/bin",
                    "GITHUB_OUTPUT": str(output),
                    "CI_MAVEN_RUNNER_OS": "macOS",
                },
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(unavailable.returncode, 0)
            self.assertIn("Homebrew is unavailable", unavailable.stderr)

    def test_build_number_is_bounded_and_reaches_wrapper_unchanged(self) -> None:
        context = self.step("Validate Maven release context")
        context_script = context["run"]
        for value in ("253", "Build.253+rc_1"):
            completed = subprocess.run(
                ["bash", "-c", context_script],
                env={**os.environ, "CI_MAVEN_BUILD_NUMBER": value},
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        for value in ("", "with space", "../escape", "x" * 65):
            completed = subprocess.run(
                ["bash", "-c", context_script],
                env={**os.environ, "CI_MAVEN_BUILD_NUMBER": value},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)

        commands = self.step("Run fixed Maven publication profile")
        script = commands["run"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wrapper = root / "scripts/ci/run-maven-publication.sh"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text(
                '#!/usr/bin/env bash\nprintf "%s" "$CI_MAVEN_BUILD_NUMBER" > "$CAPTURE"\n',
                encoding="utf-8",
            )
            capture = root / "captured.txt"
            ci_log = root / "ci.log"
            evidence = root / "evidence"
            result = root / "result.json"
            value = "Build.253+rc_1"
            completed = subprocess.run(
                ["bash", "-c", script],
                cwd=root,
                env={
                    **os.environ,
                    "CI_LOG": str(ci_log),
                    "CI_MAVEN_BUILD_NUMBER": value,
                    "CI_MAVEN_EVIDENCE_DIR": str(evidence),
                    "CI_MAVEN_RESULT_FILE": str(result),
                    "CAPTURE": str(capture),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(capture.read_text(), value)

    def test_central_dispatch_routes_bounded_maven_release_identity(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        jobs = workflow["jobs"]
        request_steps = jobs["request"]["steps"]
        validate = next(step for step in request_steps if step.get("name") == "Validate Maven release request")
        self.assertEqual(validate["if"], "${{ steps.claim.outputs.workflow_key == 'release.maven' }}")
        self.assertEqual(set(validate["env"]), {"TEST_PROFILE", "INPUTS_JSON"})
        self.assertEqual(validate["env"]["TEST_PROFILE"], "${{ steps.claim.outputs.test_profile }}")
        self.assertEqual(validate["env"]["INPUTS_JSON"], "${{ steps.claim.outputs.inputs_json }}")

        script = validate["run"]
        for profile, inputs in (("publish", '{"build_number":"253"}'), ("publish", '{"build_number":"Build.253+rc_1"}')):
            completed = subprocess.run(
                ["bash", "-c", script],
                env={**os.environ, "TEST_PROFILE": profile, "INPUTS_JSON": inputs},
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        for profile, inputs in (
            ("build", '{"build_number":"253"}'),
            ("publish", '{}'),
            ("publish", '{"build_number":""}'),
            ("publish", '{"build_number":253}'),
            ("publish", '{"build_number":"253","extra":"x"}'),
        ):
            completed = subprocess.run(
                ["bash", "-c", script],
                env={**os.environ, "TEST_PROFILE": profile, "INPUTS_JSON": inputs},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)

        job = jobs["maven"]
        self.assertEqual(
            job["if"],
            "${{ needs.request.outputs.workflow_key == 'release.maven' && needs.request.outputs.test_profile == 'publish' }}",
        )
        self.assertEqual(job["uses"], "./.github/workflows/maven.yml")
        self.assertEqual(set(job["with"]), {"repository", "ref", "source_is_tag", "build_number", "ci_run_id"})
        self.assertEqual(job["with"]["repository"], "${{ needs.request.outputs.repository }}")
        self.assertEqual(job["with"]["ref"], "${{ needs.request.outputs.ref }}")
        self.assertEqual(job["with"]["source_is_tag"], "${{ needs.request.outputs.is_tag == 'true' }}")
        self.assertEqual(
            job["with"]["build_number"],
            "${{ fromJSON(needs.request.outputs.inputs_json).build_number }}",
        )
        self.assertNotIn("ref", job["with"]["build_number"])
        self.assertNotIn("sha", job["with"]["build_number"].lower())
        self.assertEqual(job["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        self.assertEqual(set(job["secrets"]), set(self.workflow["on"]["workflow_call"]["secrets"]))
        self.assertEqual(job["secrets"]["MAVEN_PUBLISH_USERNAME"], "${{ secrets.FORGEJO_REGISTRY_USERNAME }}")
        self.assertEqual(job["secrets"]["MAVEN_PUBLISH_TOKEN"], "${{ secrets.FORGEJO_REGISTRY_TOKEN }}")
        self.assertEqual(job["secrets"]["MAVEN_READ_TOKEN"], "${{ secrets.CIW_MAVEN_PACKAGE_READ_TOKEN }}")
        self.assertEqual(
            job["concurrency"]["group"],
            "central-release-${{ needs.request.outputs.repository }}-${{ needs.request.outputs.workflow_key }}-${{ needs.request.outputs.test_profile }}",
        )
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

        settlement = jobs["settle_cancelled"]
        self.assertIn("maven", settlement["needs"])
        self.assertIn("needs.maven.result == 'cancelled'", settlement["if"])

        for name in ("android", "apple", "python"):
            self.assertNotIn("build_number", jobs[name]["with"])


    def test_publication_credentials_fail_closed_before_wrapper(self) -> None:
        credentials = self.step("Validate fixed Maven publication credentials")
        self.assertEqual(
            credentials["env"],
            {
                "CI_MAVEN_PUBLISH_USERNAME": "${{ secrets.MAVEN_PUBLISH_USERNAME }}",
                "CI_MAVEN_PUBLISH_TOKEN": "${{ secrets.MAVEN_PUBLISH_TOKEN }}",
                "CI_MAVEN_READ_TOKEN": "${{ secrets.MAVEN_READ_TOKEN }}",
            },
        )
        script = credentials["run"]
        values = {
            "CI_MAVEN_PUBLISH_USERNAME": "forgejo-user",
            "CI_MAVEN_PUBLISH_TOKEN": "publish-secret-value",
            "CI_MAVEN_READ_TOKEN": "read-secret-value",
        }
        success = subprocess.run(["bash", "-c", script], env={**os.environ, **values}, capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        for missing in values:
            env = {**os.environ, **values, missing: ""}
            failed = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn(missing, failed.stderr)
            for secret in values.values():
                self.assertNotIn(secret, failed.stdout + failed.stderr)

    def test_wrapper_validation_executes_fail_closed(self) -> None:
        script = self.step("Validate fixed Maven wrapper")["run"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            wrapper = root / "scripts/ci/run-maven-publication.sh"
            wrapper.parent.mkdir(parents=True)

            missing = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)

            wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            untracked = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(untracked.returncode, 0)

            wrapper.unlink()
            target = root / "wrapper-target.sh"
            target.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            wrapper.symlink_to(target)
            symlink = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(symlink.returncode, 0)

            wrapper.unlink()
            wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            subprocess.run(["git", "add", "scripts/ci/run-maven-publication.sh"], cwd=root, check=True)
            tracked = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True)
            self.assertEqual(tracked.returncode, 0, tracked.stderr)

    def test_publication_always_uses_fixed_private_git_boundary(self) -> None:
        names = [step.get("name") for step in self.job["steps"]]
        self.assertNotIn("Resolve optional private-Git scope", names)
        network = self.step("Connect to private Git service")
        self.assertNotIn("if", network)
        self.assertEqual(network["uses"], "StreamScapeTV/ci-workflows/actions/private-git@main")
        self.assertEqual(
            network["env"],
            {
                "TS_OAUTH_CLIENT_ID": "${{ secrets.TS_OAUTH_CLIENT_ID }}",
                "TS_OAUTH_SECRET": "${{ secrets.TS_OAUTH_SECRET }}",
            },
        )
        self.assertNotIn("tailscale/github-action", self.text)

    def test_product_credentials_are_fixed_and_scrubbed(self) -> None:
        commands = self.step("Run fixed Maven publication profile")
        self.assertEqual(
            {
                key: commands["env"][key]
                for key in ("CI_MAVEN_PUBLISH_USERNAME", "CI_MAVEN_PUBLISH_TOKEN", "CI_MAVEN_READ_TOKEN")
            },
            {
                "CI_MAVEN_PUBLISH_USERNAME": "${{ secrets.MAVEN_PUBLISH_USERNAME }}",
                "CI_MAVEN_PUBLISH_TOKEN": "${{ secrets.MAVEN_PUBLISH_TOKEN }}",
                "CI_MAVEN_READ_TOKEN": "${{ secrets.MAVEN_READ_TOKEN }}",
            },
        )
        scrub = self.step("Scrub configured CI secrets from private log")
        for name in (
            "CI_SECRET_MAVEN_PUBLISH_USERNAME",
            "CI_SECRET_MAVEN_PUBLISH_TOKEN",
            "CI_SECRET_MAVEN_READ_TOKEN",
            "CI_SECRET_TAILSCALE_OAUTH_CLIENT_ID",
            "CI_SECRET_TAILSCALE_OAUTH_SECRET",
        ):
            self.assertIn(name, scrub["env"])

    def test_evidence_convention_is_fixed_optional_and_immutable(self) -> None:
        evidence = self.step("Validate optional immutable publication evidence")
        run = evidence["run"]
        self.assertEqual(evidence["env"]["EVIDENCE_DIR"], "${{ runner.temp }}/maven-publication-evidence")
        self.assertEqual(evidence["env"]["RESULT_FILE"], "${{ runner.temp }}/maven-publication-result.json")
        for value in (
            "publication.zip",
            "manifest.json",
            '{"schema_version", "publication_id"}',
            "[A-Za-z0-9][A-Za-z0-9._+-]{0,127}",
            "Maven publication manifest must be a JSON object",
        ):
            self.assertIn(value, run)

        archive = self.step("Store immutable Maven publication archive")
        manifest = self.step("Store immutable Maven publication manifest")
        for step, file_name in ((archive, "publication.zip"), (manifest, "manifest.json")):
            self.assertEqual(step["uses"], "StreamScapeTV/ci-workflows/actions/google-drive@main")
            self.assertEqual(step["if"], "${{ steps.evidence.outputs.produced == 'true' }}")
            self.assertEqual(step["with"]["repository"], "${{ inputs.repository || github.repository }}")
            self.assertEqual(step["with"]["ref"], "maven-releases")
            self.assertEqual(step["with"]["subdirectory"], "${{ steps.evidence.outputs.publication_id }}")
            self.assertEqual(step["with"]["file_name"], file_name)
            self.assertTrue(step["with"]["immutable"])
        self.assertNotIn("latest", str(archive).lower())
        self.assertNotIn("latest", str(manifest).lower())

    def test_evidence_result_parser_executes_fail_closed(self) -> None:
        run = self.step("Validate optional immutable publication evidence")["run"]
        lines = run.splitlines()
        start = next(i for i, line in enumerate(lines) if 'python3 - "${RESULT_FILE}" "${manifest}"' in line)
        end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line == "PY")
        script = "\n".join(lines[start + 1 : end]) + "\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = root / "result.json"
            manifest = root / "manifest.json"
            manifest.write_text("{}\n", encoding="utf-8")

            result.write_text(
                json.dumps({"schema_version": 1, "publication_id": "1.2.3-test.abc"}) + "\n",
                encoding="utf-8",
            )
            valid = subprocess.run(
                [sys.executable, "-", str(result), str(manifest)],
                input=script,
                text=True,
                capture_output=True,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(valid.stdout.strip(), "1.2.3-test.abc")

            for invalid in (
                {"schema_version": 1, "publication_id": "../escape"},
                {"schema_version": 2, "publication_id": "1.2.3"},
                {"schema_version": 1, "publication_id": "1.2.3", "extra": True},
            ):
                result.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, "-", str(result), str(manifest)],
                    input=script,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(completed.returncode, 0)

            result.write_text(
                json.dumps({"schema_version": 1, "publication_id": "1.2.3"}) + "\n",
                encoding="utf-8",
            )
            manifest.write_text("[]\n", encoding="utf-8")
            non_object = subprocess.run(
                [sys.executable, "-", str(result), str(manifest)],
                input=script,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(non_object.returncode, 0)

    def test_lifecycle_private_log_and_cleanup_are_central_owned(self) -> None:
        names = [step.get("name") for step in self.job["steps"]]
        self.assertLess(names.index("Mark Agent State run as running"), names.index("Check out source"))
        self.assertLess(names.index("Check out source"), names.index("Record observed source SHA"))
        self.assertLess(names.index("Run fixed Maven publication profile"), names.index("Scrub configured CI secrets from private log"))
        self.assertLess(names.index("Scrub configured CI secrets from private log"), names.index("Upload CI log to Google Drive"))
        self.assertLess(names.index("Clean publication workspace"), names.index("Finish Agent State run"))

        commands = self.step("Run fixed Maven publication profile")["run"]
        self.assertIn('>> "${CI_LOG}" 2>&1', commands)
        self.assertNotIn("tee -a", commands)

        drive = self.step("Upload CI log to Google Drive")
        self.assertEqual(drive["uses"], "StreamScapeTV/ci-workflows/actions/google-drive@main")
        self.assertEqual(drive["with"]["file_name"], "${{ github.run_id }}-${{ github.run_attempt }}.txt")
        self.assertEqual(drive["with"]["mime_type"], "text/plain")

        cleanup = self.step("Clean publication workspace")
        self.assertEqual(cleanup["if"], "${{ always() }}")
        self.assertIn("git clean -ffdX", cleanup["run"])
        self.assertIn("git status --porcelain=v1 --untracked-files=all", cleanup["run"])

        finish = self.step("Finish Agent State run")
        self.assertEqual(finish["if"], "${{ always() && inputs.ci_run_id != '' }}")
        self.assertIn("steps.cleanup.outcome == 'success'", finish["with"]["status"])


if __name__ == "__main__":
    unittest.main()
