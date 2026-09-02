from pathlib import Path
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
                self.assertEqual(
                    finish["with"]["status"],
                    "${{ steps.commands.outcome == 'success' && steps.scrub.outcome == 'success' && steps.drive.outcome == 'success' && 'succeeded' || 'failed' }}",
                )

    def test_fixed_node_profiles_and_lock_refresh_policy(self) -> None:
        _, _, by_name = self._workflow("node")
        script = by_name["Run fixed Node profile"]["run"]
        for command in (
            "run_logged npm-ci npm ci",
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
