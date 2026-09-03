from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class NodeFlutterObservedSourceTests(unittest.TestCase):
    def _workflow(self, name: str) -> tuple[dict, list[dict], dict[str, dict]]:
        workflow = yaml.safe_load((ROOT / ".github/workflows" / f"{name}.yml").read_text())
        steps = workflow["jobs"]["ci"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        return workflow, steps, by_name

    def test_node_and_flutter_record_exact_checked_out_sha_before_profile(self) -> None:
        expected_inputs = {
            "node": {"repository", "ref", "test_profile", "ci_run_id", "upload_private_log"},
            "flutter": {
                "repository",
                "ref",
                "test_profile",
                "flutter_version",
                "ci_run_id",
                "upload_private_log",
            },
        }
        setup_names = {
            "node": ("Set up fixed Node 22.18.0", "Set up repository-pinned Node"),
            "flutter": ("Validate Flutter version",),
        }

        for name in ("node", "flutter"):
            with self.subTest(workflow=name):
                workflow, steps, by_name = self._workflow(name)
                names = [step.get("name") for step in steps]
                self.assertEqual(set(workflow["on"]["workflow_call"]["inputs"]), expected_inputs[name])
                self.assertEqual(workflow["jobs"]["ci"]["runs-on"], "ubuntu-24.04")

                identity = by_name["Resolve observed source SHA"]
                record = by_name["Record observed source SHA"]
                scrub = by_name["Scrub configured CI secrets from private log"]
                drive = by_name["Upload CI log to Google Drive"]
                finish = by_name["Finish Agent State run"]

                self.assertNotIn("if", identity)
                self.assertIn('source_sha="$(git rev-parse HEAD)"', identity["run"])
                self.assertIn('[[ "${source_sha}" =~ ^[0-9A-Fa-f]{40}$ ]] || exit 2', identity["run"])
                self.assertNotIn("github.sha", identity["run"])

                self.assertEqual(record["if"], "${{ inputs.ci_run_id != '' }}")
                self.assertEqual(record["uses"], "StreamScapeTV/ci-workflows/actions/agent-state@main")
                self.assertEqual(record["with"]["phase"], "observe-source")
                self.assertEqual(record["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
                self.assertEqual(
                    record["with"]["observed_source_sha"],
                    "${{ steps.source_identity.outputs.source_sha }}",
                )

                self.assertLess(names.index("Check out source"), names.index("Resolve observed source SHA"))
                self.assertLess(names.index("Resolve observed source SHA"), names.index("Record observed source SHA"))
                for setup_name in setup_names[name]:
                    self.assertLess(names.index("Record observed source SHA"), names.index(setup_name))
                profile_name = "Run fixed Node profile" if name == "node" else "Run fixed Flutter profile"
                for setup_name in setup_names[name]:
                    self.assertLess(names.index(setup_name), names.index(profile_name))
                self.assertLess(names.index(profile_name), names.index("Scrub configured CI secrets from private log"))
                self.assertLess(names.index("Scrub configured CI secrets from private log"), names.index("Upload CI log to Google Drive"))
                self.assertLess(names.index("Upload CI log to Google Drive"), names.index("Finish Agent State run"))

                self.assertEqual(scrub["if"], "${{ always() }}")
                self.assertEqual(
                    drive["with"]["file_name"],
                    "${{ github.run_id }}-${{ github.run_attempt }}.txt",
                )
                self.assertEqual(drive["with"]["mime_type"], "text/plain")
                self.assertNotIn("gzip", drive["with"])
                self.assertEqual(finish["if"], "${{ always() && inputs.ci_run_id != '' }}")
                if name == "node":
                    self.assertEqual(
                        finish["with"]["status"],
                        "${{ steps.commands.outcome == 'success' && (steps.node_modules_cache_save.outcome == 'success' || steps.node_modules_cache_save.outcome == 'skipped') && steps.scrub.outcome == 'success' && steps.drive.outcome == 'success' && 'succeeded' || 'failed' }}",
                    )
                else:
                    self.assertEqual(
                        finish["with"]["status"],
                        "${{ steps.commands.outcome == 'success' && steps.scrub.outcome == 'success' && steps.drive.outcome == 'success' && 'succeeded' || 'failed' }}",
                    )

    def test_fixed_node_profiles_and_lock_refresh_policy(self) -> None:
        _, _, by_name = self._workflow("node")
        script = by_name["Run fixed Node profile"]["run"]
        for command in (
            "run_logged npm-ci npm ci",
            "ensure_node_modules",
            "node_modules cache hit; npm ci skipped",
            "run_logged test npm test",
            "run_logged typecheck npm run typecheck",
            "run_logged build npm run build",
            "run_logged check npm run check",
            "run_logged repository-clean git diff --exit-code",
        ):
            self.assertIn(command, script)

        source = by_name["Prepare source token"]
        checkout = by_name["Check out source"]
        fixed = by_name["Set up fixed Node 22.18.0"]
        repository_pinned = by_name["Set up repository-pinned Node"]

        self.assertEqual(
            source["with"]["permission-contents"],
            "${{ inputs.test_profile == 'static-web-lock-refresh' && 'write' || 'read' }}",
        )
        self.assertEqual(
            checkout["with"]["persist-credentials"],
            "${{ inputs.test_profile == 'static-web-lock-refresh' }}",
        )
        self.assertEqual(fixed["if"], "${{ inputs.test_profile == 'frontend-full' }}")
        self.assertEqual(fixed["uses"], "actions/setup-node@v6")
        self.assertEqual(fixed["with"]["node-version"], "22.18.0")
        self.assertEqual(fixed["with"]["cache"], "")
        self.assertEqual(
            repository_pinned["if"],
            "${{ inputs.test_profile == 'static-web' || inputs.test_profile == 'static-web-lock-refresh' }}",
        )
        self.assertEqual(repository_pinned["uses"], "actions/setup-node@v6")
        self.assertEqual(repository_pinned["with"]["node-version-file"], ".nvmrc")
        self.assertEqual(repository_pinned["with"]["cache"], "")
        self.assertNotIn("node-version", repository_pinned["with"])

        for invariant in (
            "static-web-lock-refresh)",
            "npm install --package-lock-only --ignore-scripts --no-audit",
            'test "${manifest_after}" = "${manifest_before}"',
            'test "${#changed_paths[@]}" -eq 1 && test "${changed_paths[0]}" = package-lock.json',
            "npm audit --json",
            "run_logged audit-production npm audit --omit=dev --audit-level=low --json",
            'git add -- package-lock.json',
            'git push origin "HEAD:refs/heads/${TARGET_REF}"',
        ):
            self.assertIn(invariant, script)
        self.assertNotIn("--force", script)
        self.assertNotIn("--legacy-peer-deps", script)

    def test_node_cache_is_repository_scoped_and_default_branch_write_only(self) -> None:
        _, _, by_name = self._workflow("node")
        scope = by_name["Resolve Node default-branch cache scope"]
        restore = by_name["Restore Node modules cache"]
        save = by_name["Save Node modules cache"]
        script = scope["run"]

        self.assertIn("https://api.github.com/repos/${SOURCE_REPOSITORY}", script)
        self.assertIn('get("default_branch", "")', script)
        self.assertIn('test "${normalized_ref}" = "${default_branch}"', script)
        self.assertIn('test "${checkout_branch}" = "${default_branch}"', script)
        self.assertIn('test "${TEST_PROFILE}" != "static-web-lock-refresh"', script)
        self.assertIn("node-modules-v1-${repository_key}-${RUNNER_OS_NAME}-${RUNNER_ARCH_NAME}", script)
        self.assertIn("sha256sum package.json", script)
        self.assertIn("sha256sum package-lock.json", script)
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(restore["with"]["path"], "node_modules")
        self.assertEqual(restore["with"]["key"], "${{ steps.node_cache_scope.outputs.key }}")
        self.assertEqual(save["uses"], "actions/cache/save@v4")
        self.assertEqual(save["with"]["path"], "node_modules")
        self.assertEqual(save["with"]["key"], "${{ steps.node_modules_cache.outputs.cache-primary-key }}")
        self.assertIn("steps.node_cache_scope.outputs.save_enabled == 'true'", save["if"])
        self.assertIn("steps.node_modules_cache.outputs.cache-hit != 'true'", save["if"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "CI"], cwd=root, check=True)
            (root / "package.json").write_text('{"name":"fixture","version":"1.0.0"}\n', encoding="utf-8")
            (root / "package-lock.json").write_text('{"name":"fixture","version":"1.0.0","lockfileVersion":3,"packages":{}}\n', encoding="utf-8")
            subprocess.run(["git", "add", "package.json", "package-lock.json"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                "#!/bin/sh\nprintf '{\"default_branch\":\"%s\"}\\n' \"${FAKE_DEFAULT_BRANCH:-main}\"\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)

            def flags(ref: str, default_branch: str, profile: str = "static-web") -> dict[str, str]:
                output = root / "github-output"
                output.write_text("", encoding="utf-8")
                env = os.environ.copy()
                env.update(
                    {
                        "SOURCE_REPOSITORY": "StreamScapeTV/StreamScapeWeb",
                        "REQUESTED_REF": ref,
                        "TEST_PROFILE": profile,
                        "SOURCE_TOKEN": "token",
                        "RUNNER_OS_NAME": "Linux",
                        "RUNNER_ARCH_NAME": "X64",
                        "FAKE_DEFAULT_BRANCH": default_branch,
                        "GITHUB_OUTPUT": str(output),
                        "PATH": f"{fake_bin}:{env['PATH']}",
                    }
                )
                result = subprocess.run(
                    ["bash"], cwd=root, env=env, input=script, text=True, capture_output=True, check=False
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return dict(line.split("=", 1) for line in output.read_text().splitlines())

            main = flags("main", "main")
            self.assertEqual((main["restore_enabled"], main["save_enabled"]), ("true", "true"))
            self.assertIn("StreamScapeTV-StreamScapeWeb", main["key"])
            self.assertIn("npm", main["key"])

            subprocess.run(["git", "switch", "-c", "feature/cache"], cwd=root, check=True, capture_output=True)
            feature = flags("feature/cache", "main")
            self.assertEqual((feature["restore_enabled"], feature["save_enabled"]), ("true", "false"))

            lock_refresh = flags("feature/cache", "main", profile="static-web-lock-refresh")
            self.assertEqual(lock_refresh["save_enabled"], "false")

    def test_fixed_flutter_profile_and_version_policy_are_unchanged(self) -> None:
        workflow, _, by_name = self._workflow("flutter")
        script = by_name["Run fixed Flutter profile"]["run"]
        for command in (
            "run_logged pub-get flutter pub get --enforce-lockfile",
            "run_logged analyze flutter analyze",
            "run_logged test flutter test",
        ):
            self.assertIn(command, script)
        self.assertIn("3.41.4|3.44.6", by_name["Validate Flutter version"]["run"])
        self.assertEqual(workflow["on"]["workflow_call"]["inputs"]["flutter_version"]["default"], "3.44.6")
        self.assertFalse(by_name["Set up Flutter"]["with"]["cache"])
        self.assertFalse(by_name["Set up Flutter"]["with"]["pub-cache"])


if __name__ == "__main__":
    unittest.main()
