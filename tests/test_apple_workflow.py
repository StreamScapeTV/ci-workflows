from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class AppleWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = ROOT / ".github/workflows/apple.yml"
        self.text = self.path.read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)

    def test_host_is_fast_and_full_is_parallel_platform_gate(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"plan", "execute", "finish"})

        plan = jobs["plan"]
        profile_step = next(
            step for step in plan["steps"] if step.get("name") == "Resolve fixed Apple execution lanes"
        )
        script = profile_step["run"]
        self.assertIn("host)", script)
        self.assertIn(
            '{"include":[{"lane":"macos-test","cache_save":true}]}',
            script,
        )
        self.assertIn("full)", script)
        self.assertIn('"lane":"ios-build"', script)
        self.assertIn('"lane":"tvos-build"', script)
        self.assertIn('"lane":"macos-test"', script)
        self.assertIn("release-build)", script)
        self.assertIn("swift-package)", script)

        execute = jobs["execute"]
        self.assertFalse(execute["strategy"]["fail-fast"])
        self.assertEqual(
            execute["strategy"]["matrix"],
            "${{ fromJSON(needs.plan.outputs.matrix) }}",
        )
        self.assertEqual(execute["runs-on"], "macos-latest")

    def test_platform_gate_uses_compile_only_ios_tvos_and_direct_macos_test(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        command_step = next(
            step for step in execute["steps"] if step.get("name") == "Run fixed Apple lane"
        )
        script = command_step["run"]

        self.assertIn("run_logged ios-build xcodebuild build", script)
        self.assertIn("run_logged tvos-build xcodebuild build", script)
        self.assertIn("run_logged macos-test xcodebuild test", script)
        self.assertNotIn("build-for-testing", script)
        self.assertNotIn("macos-build", script)
        self.assertIn(
            "-only-testing:streamscapetvTests/SelectedBackendStateSyncRoutingIntegrationTests",
            script,
        )

    def test_optional_swiftpm_xcode_args_are_strict_bash_safe(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        command_step = next(
            step for step in execute["steps"] if step.get("name") == "Run fixed Apple lane"
        )
        script = command_step["run"]
        safe_expansion = '"${swiftpm_xcode_args[@]+"${swiftpm_xcode_args[@]}"}"'

        self.assertEqual(script.count(safe_expansion), 6)
        self.assertNotIn('"${swiftpm_xcode_args[@]}"', script.replace(safe_expansion, ""))

        probe = f"""
set -Eeuo pipefail
capture() {{
  printf 'argc=%s\\n' "$#"
  printf '<%s>\\n' "$@"
}}
swiftpm_xcode_args=()
capture before {safe_expansion} after
swiftpm_xcode_args=(-clonedSourcePackagesDirPath "/tmp/swift pm clones")
capture before {safe_expansion} after
"""
        result = subprocess.run(
            ["bash"],
            input=probe,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "argc=2",
                "<before>",
                "<after>",
                "argc=4",
                "<before>",
                "<-clonedSourcePackagesDirPath>",
                "</tmp/swift pm clones>",
                "<after>",
            ],
        )

    def test_agent_state_lifecycle_is_single_coordinator_and_single_finalizer(self) -> None:
        self.assertEqual(self.text.count("phase: start"), 1)
        self.assertEqual(self.text.count("phase: finish"), 1)

        finish = self.workflow["jobs"]["finish"]
        self.assertEqual(finish["needs"], ["plan", "execute"])
        self.assertEqual(finish["if"], "${{ always() && inputs.ci_run_id != '' }}")
        final_step = finish["steps"][0]
        status = final_step["with"]["status"]
        self.assertIn("needs.plan.result", status)
        self.assertIn("needs.execute.result", status)
        self.assertIn("'cancelled'", status)
        self.assertIn("'succeeded'", status)
        self.assertIn("'failed'", status)

    def test_parallel_lanes_keep_independent_scrubbed_drive_logs(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        by_name = {step.get("name"): step for step in execute["steps"] if step.get("name")}
        scrub = by_name["Scrub configured CI secrets from private log"]
        drive = by_name["Upload CI log to Google Drive"]

        self.assertEqual(scrub["if"], "${{ always() }}")
        self.assertIn("steps.scrub.outcome == 'success'", drive["if"])
        self.assertEqual(
            drive["with"]["file_path"],
            "${{ runner.temp }}/central-ci-${{ matrix.lane }}.log",
        )
        self.assertEqual(
            drive["with"]["file_name"],
            "${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.lane }}.log.gz",
        )
        self.assertEqual(drive["with"]["gzip"], "true")
        self.assertEqual(drive["with"]["mime_type"], "application/gzip")

    def test_dependency_cache_scope_is_preserved_and_only_one_full_lane_can_save(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        by_name = {step.get("name"): step for step in execute["steps"] if step.get("name")}
        prepare = by_name["Prepare Apple develop dependency cache"]["run"]
        restore = by_name["Restore Apple develop dependency cache"]
        archive = by_name["Prepare Apple develop dependency cache archive"]

        self.assertIn('test "${source_repository}" = "StreamScapeTV/iptv-apple"', prepare)
        self.assertIn('test "${source_ref}" = "develop"', prepare)
        self.assertIn(
            'allowed = ("Vendor/StreamscapeMediaApple", "streamscapetv-swiftpm-clones")',
            restore["run"],
        )
        self.assertIn("matrix.cache_save", archive["if"])

        plan_script = next(
            step
            for step in self.workflow["jobs"]["plan"]["steps"]
            if step.get("name") == "Resolve fixed Apple execution lanes"
        )["run"]
        full_line = next(line for line in plan_script.splitlines() if '"lane":"ios-build"' in line)
        self.assertEqual(full_line.count('"cache_save":true'), 1)
        self.assertEqual(full_line.count('"cache_save":false'), 2)


if __name__ == "__main__":
    unittest.main()
