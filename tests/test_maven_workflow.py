from pathlib import Path
import json
import os
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
        self.job = self.workflow["jobs"]["publish"]

    def step(self, name: str) -> dict:
        return next(step for step in self.job["steps"] if step.get("name") == name)

    def test_api_is_product_neutral_and_bounded(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "ref", "build_number", "ci_run_id", "upload_private_log"},
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
        self.assertEqual(self.job["runs-on"], "ubuntu-24.04")
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})

        for forbidden in (
            "Streamscape Media",
            "streamscape-media",
            "git.faruqi.dev",
            "Forgejo",
            "arguments_json",
            "publishAllPublications",
            "gradlew",
            "com.streamscape",
            "runner_label",
            "registry_url",
            "script_path",
            "prepare_command",
            "build_command",
            "release_command",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_source_admission_and_fixed_wrapper_contract(self) -> None:
        checkout = self.step("Check out source")
        self.assertEqual(checkout["with"]["repository"], "${{ inputs.repository || github.repository }}")
        self.assertEqual(checkout["with"]["ref"], "${{ inputs.ref || github.sha }}")
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
        self.assertIn("run_logged maven-publish bash scripts/ci/run-maven-publication.sh", commands["run"])
        self.assertNotIn("bash -lc", commands["run"])

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
        self.assertEqual(set(job["with"]), {"repository", "ref", "build_number", "ci_run_id"})
        self.assertEqual(job["with"]["repository"], "${{ needs.request.outputs.repository }}")
        self.assertEqual(job["with"]["ref"], "${{ needs.request.outputs.ref }}")
        self.assertEqual(
            job["with"]["build_number"],
            "${{ fromJSON(needs.request.outputs.inputs_json).build_number }}",
        )
        self.assertNotIn("ref", job["with"]["build_number"])
        self.assertNotIn("sha", job["with"]["build_number"].lower())
        self.assertEqual(job["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        self.assertTrue(job["secrets"] == "inherit")
        self.assertEqual(job["concurrency"]["group"], "central-ci-${{ needs.request.outputs.workflow_key }}-${{ inputs.active_key }}")
        self.assertTrue(job["concurrency"]["cancel-in-progress"])

        settlement = jobs["settle_cancelled"]
        self.assertIn("maven", settlement["needs"])
        self.assertIn("needs.maven.result == 'cancelled'", settlement["if"])

        for name in ("android", "apple", "python", "node", "flutter"):
            self.assertNotIn("build_number", jobs[name]["with"])

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

    def test_private_git_scope_is_optional_fixed_and_fail_closed(self) -> None:
        scope = self.step("Resolve optional private-Git scope")
        self.assertEqual(
            set(scope["env"]),
            {"PRIVATE_GIT_CLIENT_ID", "PRIVATE_GIT_SECRET"},
        )
        network = self.step("Connect to private Git service")
        self.assertEqual(network["if"], "${{ steps.private_git_scope.outputs.enabled == 'true' }}")
        self.assertEqual(network["uses"], "StreamScapeTV/ci-workflows/actions/private-git@main")
        self.assertNotIn("tailscale/github-action", self.text)

        script = scope["run"]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            base_env = {**os.environ, "GITHUB_OUTPUT": str(output)}

            none = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, "PRIVATE_GIT_CLIENT_ID": "", "PRIVATE_GIT_SECRET": ""},
                capture_output=True,
                text=True,
            )
            self.assertEqual(none.returncode, 0, none.stderr)
            self.assertIn("enabled=false", output.read_text())

            output.write_text("")
            partial = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, "PRIVATE_GIT_CLIENT_ID": "id", "PRIVATE_GIT_SECRET": ""},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(partial.returncode, 0)

            output.write_text("")
            complete = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, "PRIVATE_GIT_CLIENT_ID": "id", "PRIVATE_GIT_SECRET": "secret"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(complete.returncode, 0, complete.stderr)
            self.assertIn("enabled=true", output.read_text())

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
        self.assertNotIn("latest", self.text.lower())

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
