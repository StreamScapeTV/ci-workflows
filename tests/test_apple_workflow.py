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


    def test_generic_hosted_profiles_use_fixed_product_wrapper(self) -> None:
        jobs = self.workflow["jobs"]
        plan_script = next(
            step
            for step in jobs["plan"]["steps"]
            if step.get("name") == "Resolve fixed Apple execution lanes"
        )["run"]
        self.assertIn("build)", plan_script)
        self.assertIn('{"include":[{"lane":"hosted-build","cache_save":false}]}', plan_script)
        self.assertIn("test)", plan_script)
        self.assertIn('{"include":[{"lane":"hosted-test","cache_save":false}]}', plan_script)
        self.assertIn("simulator)", plan_script)
        self.assertIn('{"include":[{"lane":"hosted-simulator","cache_save":false}]}', plan_script)

        execute_steps = jobs["execute"]["steps"]
        by_name = {step.get("name"): step for step in execute_steps if step.get("name")}
        cache_prepare = by_name["Prepare Apple develop dependency cache"]
        for profile in ("build", "test", "simulator"):
            self.assertIn(f"inputs.test_profile != '{profile}'", cache_prepare["if"])

        command_step = by_name["Run fixed Apple lane"]
        script = command_step["run"]
        self.assertIn('build|test|simulator)', script)
        self.assertIn('wrapper="scripts/ci/run-apple-hosted-validation.sh"', script)
        self.assertIn('export CI_APPLE_HOSTED_PROFILE="${TEST_PROFILE}"', script)
        self.assertIn('run_logged "apple-${TEST_PROFILE}" bash "${wrapper}"', script)
        generic_start = script.index('build|test|simulator)')
        generic_end = script.index('swiftpm_xcode_args=()', generic_start)
        generic_block = script[generic_start:generic_end]
        self.assertNotIn("streamscape-media", generic_block.lower())
        self.assertNotIn("streamscapetv.xcworkspace", generic_block)
        self.assertNotIn("xcodebuild", generic_block)

    def test_historical_host_materializes_only_the_exact_approved_bootstrap(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        command_step = next(
            step for step in execute["steps"] if step.get("name") == "Run fixed Apple lane"
        )
        script = command_step["run"]
        self.assertEqual(
            command_step["env"]["SOURCE_REPOSITORY"],
            "${{ inputs.repository || github.repository }}",
        )
        self.assertIn("materialize_historical_media_bootstrap()", script)
        self.assertIn("512db0f5b2513ad7d3a2b53bbc132ea29742bb63", script)
        self.assertIn("f610e568dabf621cf5e9e23d5541571e2feb7122", script)
        self.assertIn("dbe258b0487b4dfe023bfda1f27bf2cc013c2490", script)
        self.assertIn("492be28b492dd6bc3458cbd884893869e150818e", script)
        self.assertIn("application/vnd.github.raw+json", script)
        self.assertIn("git hash-object", script)
        self.assertEqual(script.count("materialize_historical_media_bootstrap"), 4)
        self.assertEqual(
            script.count("run_logged prepare-media bash scripts/bootstrap-streamscape-media-binary.sh"),
            4,
        )
        release_start = script.index('release-build)')
        release_end = script.index('swift-package)', release_start)
        release_block = script[release_start:release_end]
        self.assertIn("test -f scripts/bootstrap-streamscape-media-binary.sh", release_block)
        self.assertNotIn("materialize_historical_media_bootstrap", release_block)

        generic_start = script.index('build|test|simulator)')
        generic_end = script.index('swiftpm_xcode_args=()', generic_start)
        generic_block = script[generic_start:generic_end]
        self.assertNotIn("materialize_historical_media_bootstrap", generic_block)

    def test_historical_bootstrap_guard_executes_without_caller_authority(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        script = next(
            step for step in execute["steps"] if step.get("name") == "Run fixed Apple lane"
        )["run"]
        start = script.index("materialize_historical_media_bootstrap() {")
        end = script.index('\n\ncase "${TEST_PROFILE}"', start)
        function_text = script[start:end]
        probe = f"""
set -Eeuo pipefail
{function_text}
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
export CI_LOG="$work/ci.log"
export SOURCE_REPOSITORY="StreamScapeTV/iptv-apple"
gh_calls=0
git() {{
  case "$*" in
    "rev-parse HEAD") printf '%s\\n' '512db0f5b2513ad7d3a2b53bbc132ea29742bb63' ;;
    "rev-parse HEAD^{{tree}}") printf '%s\\n' 'f610e568dabf621cf5e9e23d5541571e2feb7122' ;;
    "hash-object scripts/bootstrap-streamscape-media-binary.sh") printf '%s\\n' '492be28b492dd6bc3458cbd884893869e150818e' ;;
    *) printf 'unexpected git call: %s\\n' "$*" >&2; return 97 ;;
  esac
}}
gh() {{
  gh_calls=$((gh_calls + 1))
  printf '#!/usr/bin/env bash\\necho approved-helper\\n'
}}
materialize_historical_media_bootstrap
[[ -f scripts/bootstrap-streamscape-media-binary.sh ]]
[[ "$gh_calls" -eq 1 ]]
[[ "$(cat "$CI_LOG")" == *'Materialized approved historical Apple bootstrap helper'* ]]

# Existing current-source helper remains authoritative and skips historical retrieval.
printf '#!/usr/bin/env bash\\n' > scripts/bootstrap-streamscape-media-binary.sh
export SOURCE_REPOSITORY="StreamScapeTV/other"
materialize_historical_media_bootstrap
[[ "$gh_calls" -eq 1 ]]

# Missing helper on any non-approved source fails closed.
rm -f scripts/bootstrap-streamscape-media-binary.sh
if materialize_historical_media_bootstrap; then
  echo 'unexpected historical fallback for an unapproved repository' >&2
  exit 98
fi
[[ "$gh_calls" -eq 1 ]]
"""
        result = subprocess.run(
            ["bash"],
            input=probe,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
