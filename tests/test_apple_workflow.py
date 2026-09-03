from pathlib import Path
import os
import subprocess
import tempfile
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

        self.assertIn("run_xcode_logged ios-build xcodebuild build", script)
        self.assertIn("run_xcode_logged tvos-build xcodebuild build", script)
        self.assertIn("run_xcode_logged macos-test xcodebuild test", script)
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
        cache_prepare = by_name["Resolve Apple default-branch dependency cache scope"]
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

    def test_testflight_uses_explicit_build_number_and_fixed_product_wrapper(self) -> None:
        workflow_inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertIn("build_number", workflow_inputs)
        self.assertFalse(workflow_inputs["build_number"]["required"])
        self.assertEqual(workflow_inputs["build_number"]["default"], "")

        jobs = self.workflow["jobs"]
        plan_script = next(
            step
            for step in jobs["plan"]["steps"]
            if step.get("name") == "Resolve fixed Apple execution lanes"
        )["run"]
        self.assertIn("testflight)", plan_script)
        self.assertIn(
            '{"include":[{"lane":"testflight","cache_save":false}]}',
            plan_script,
        )

        execute_steps = jobs["execute"]["steps"]
        by_name = {step.get("name"): step for step in execute_steps if step.get("name")}
        cache_prepare = by_name["Resolve Apple default-branch dependency cache scope"]
        self.assertIn("inputs.test_profile != 'testflight'", cache_prepare["if"])

        prepare = by_name["Prepare fixed TestFlight release context"]
        self.assertEqual(prepare["if"], "${{ inputs.test_profile == 'testflight' }}")
        self.assertEqual(prepare["env"]["BUILD_NUMBER"], "${{ inputs.build_number }}")
        prepare_script = prepare["run"]
        self.assertIn('test -n "${BUILD_NUMBER}"', prepare_script)
        self.assertIn('${#BUILD_NUMBER} > 64', prepare_script)
        self.assertIn('CI_APPLE_TESTFLIGHT_BUILD_NUMBER=%s', prepare_script)
        self.assertNotIn("GITHUB_RUN_NUMBER", prepare_script)
        self.assertNotIn("GITHUB_RUN_ID", prepare_script)
        self.assertNotIn("GITHUB_SHA", prepare_script)
        self.assertNotIn("CURRENT_PROJECT_VERSION", prepare_script)

        command = by_name["Run fixed Apple lane"]
        command_script = command["run"]
        start = command_script.index('testflight)')
        end = command_script.index('build|test|simulator)', start)
        testflight_block = command_script[start:end]
        self.assertIn('wrapper="scripts/ci/run-apple-testflight.sh"', testflight_block)
        self.assertIn('run_logged apple-testflight bash "${wrapper}"', testflight_block)
        self.assertIn('test ! -L "${wrapper}"', testflight_block)
        self.assertIn('git ls-files --error-unmatch -- "${wrapper}"', testflight_block)
        self.assertNotIn("xcodebuild", testflight_block)
        self.assertNotIn("streamscapetv", testflight_block.lower())
        self.assertNotIn("CFBundleVersion", testflight_block)
        self.assertNotIn("CURRENT_PROJECT_VERSION", testflight_block)
        self.assertNotIn("archivePath", testflight_block)
        self.assertNotIn("exportArchive", testflight_block)

    def test_testflight_wrapper_validation_executes_fail_closed(self) -> None:
        execute_steps = self.workflow["jobs"]["execute"]["steps"]
        command_script = next(
            step for step in execute_steps if step.get("name") == "Run fixed Apple lane"
        )["run"]
        start = command_script.index('testflight)')
        end = command_script.index('build|test|simulator)', start)
        block = command_script[start:end].split("\n", 1)[1]
        block = block.rsplit(";;", 1)[0]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "scripts/ci").mkdir(parents=True)
            auth_key = root / "AuthKey.p8"
            auth_key.write_text("test", encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "CI_APPLE_TESTFLIGHT_BUILD_NUMBER": "253",
                "CI_APPLE_TESTFLIGHT_AUTH_KEY_PATH": str(auth_key),
                "CI_APPLE_TESTFLIGHT_TEMP_DIR": str(root / "release"),
                "CI_APPLE_TESTFLIGHT_TEAM_ID": "TEAM",
                "CI_APPLE_TESTFLIGHT_KEY_ID": "KEY",
                "CI_APPLE_TESTFLIGHT_ISSUER_ID": "ISSUER",
                "CI_LOG": str(root / "ci.log"),
            }
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)

            prefix = """
set -Eeuo pipefail
run_logged() {
  shift
  "$@"
}
"""
            missing = subprocess.run(
                ["bash", "-c", prefix + block], cwd=root, env=env, text=True, capture_output=True
            )
            self.assertNotEqual(missing.returncode, 0)

            wrapper = root / "scripts/ci/run-apple-testflight.sh"
            target = root / "wrapper-target.sh"
            target.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            wrapper.symlink_to(target)
            symlink = subprocess.run(
                ["bash", "-c", prefix + block], cwd=root, env=env, text=True, capture_output=True
            )
            self.assertNotEqual(symlink.returncode, 0)
            wrapper.unlink()

            wrapper.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            untracked = subprocess.run(
                ["bash", "-c", prefix + block], cwd=root, env=env, text=True, capture_output=True
            )
            self.assertNotEqual(untracked.returncode, 0)

            subprocess.run(["git", "add", "scripts/ci/run-apple-testflight.sh"], cwd=root, check=True)
            tracked = subprocess.run(
                ["bash", "-c", prefix + block], cwd=root, env=env, text=True, capture_output=True
            )
            self.assertEqual(tracked.returncode, 0, tracked.stderr)

    def test_testflight_credentials_are_isolated_and_always_cleaned(self) -> None:
        workflow_call = self.workflow["on"]["workflow_call"]
        for name in (
            "APPLE_TEAM_ID",
            "APP_STORE_CONNECT_KEY_ID",
            "APP_STORE_CONNECT_ISSUER_ID",
            "APP_STORE_CONNECT_API_KEY_P8_BASE64",
        ):
            self.assertIn(name, workflow_call["secrets"])
            self.assertFalse(workflow_call["secrets"][name]["required"])

        execute_steps = self.workflow["jobs"]["execute"]["steps"]
        by_name = {step.get("name"): step for step in execute_steps if step.get("name")}
        prepare = by_name["Prepare fixed TestFlight release context"]
        prepare_script = prepare["run"]
        self.assertIn('release_root="${RUNNER_TEMP}/central-apple-testflight"', prepare_script)
        self.assertIn('chmod 700 "${release_root}"', prepare_script)
        self.assertIn("path.chmod(0o600)", prepare_script)
        self.assertIn("base64.b64decode(raw, validate=True)", prepare_script)

        command_env = by_name["Run fixed Apple lane"]["env"]
        self.assertEqual(
            command_env["CI_APPLE_TESTFLIGHT_TEAM_ID"],
            "${{ inputs.test_profile == 'testflight' && secrets.APPLE_TEAM_ID || '' }}",
        )
        self.assertEqual(
            command_env["CI_APPLE_TESTFLIGHT_KEY_ID"],
            "${{ inputs.test_profile == 'testflight' && secrets.APP_STORE_CONNECT_KEY_ID || '' }}",
        )
        self.assertEqual(
            command_env["CI_APPLE_TESTFLIGHT_ISSUER_ID"],
            "${{ inputs.test_profile == 'testflight' && secrets.APP_STORE_CONNECT_ISSUER_ID || '' }}",
        )
        self.assertNotIn("APP_STORE_CONNECT_API_KEY_P8_BASE64", command_env)

        cleanup = by_name["Clean TestFlight credential and release state"]
        self.assertEqual(cleanup["if"], "${{ always() && inputs.test_profile == 'testflight' }}")
        self.assertIn('rm -rf -- "${release_root}"', cleanup["run"])
        self.assertIn('test ! -e "${release_root}"', cleanup["run"])

        scrub_env = by_name["Scrub configured CI secrets from private log"]["env"]
        expected_scrub = {
            "CI_SECRET_APPLE_TEAM_ID": "${{ inputs.test_profile == 'testflight' && secrets.APPLE_TEAM_ID || '' }}",
            "CI_SECRET_APP_STORE_CONNECT_KEY_ID": "${{ inputs.test_profile == 'testflight' && secrets.APP_STORE_CONNECT_KEY_ID || '' }}",
            "CI_SECRET_APP_STORE_CONNECT_ISSUER_ID": "${{ inputs.test_profile == 'testflight' && secrets.APP_STORE_CONNECT_ISSUER_ID || '' }}",
            "CI_SECRET_APP_STORE_CONNECT_API_KEY": "${{ inputs.test_profile == 'testflight' && secrets.APP_STORE_CONNECT_API_KEY_P8_BASE64 || '' }}",
        }
        for name, expression in expected_scrub.items():
            self.assertEqual(scrub_env[name], expression)

    def test_testflight_build_number_validation_preserves_exact_value(self) -> None:
        execute_steps = self.workflow["jobs"]["execute"]["steps"]
        prepare = next(
            step
            for step in execute_steps
            if step.get("name") == "Prepare fixed TestFlight release context"
        )
        script = prepare["run"]
        validation_start = script.index('test -n "${BUILD_NUMBER}"')
        validation_end = script.index('test -n "${APP_STORE_CONNECT_KEY_ID}"', validation_start)
        validation = script[validation_start:validation_end]
        probe = f"""
set -Eeuo pipefail
validate() {{
  local BUILD_NUMBER="$1"
  {validation}
  printf '%s\\n' "$BUILD_NUMBER"
}}
validate '253'
validate '1.2.3+45'
if ( validate '' ); then exit 91; fi
if ( validate 'has space' ); then exit 92; fi
long="x$(printf '%064d' 0)"
if ( validate "$long" ); then exit 93; fi
"""
        result = subprocess.run(
            ["bash"],
            input=probe,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["253", "1.2.3+45"])

    def test_central_dispatch_routes_distinct_bounded_apple_release(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text(encoding="utf-8")
        )
        jobs = workflow["jobs"]
        validate = next(
            step
            for step in jobs["request"]["steps"]
            if step.get("name") == "Validate Apple release request"
        )
        self.assertEqual(
            validate["if"],
            "${{ steps.claim.outputs.workflow_key == 'release.apple' }}",
        )
        self.assertEqual(set(validate["env"]), {"TEST_PROFILE", "INPUTS_JSON"})
        validation = validate["run"]
        for profile, inputs in (
            ("testflight", '{"build_number":"253"}'),
            ("testflight", '{"build_number":"Build.253+rc_1"}'),
        ):
            result = subprocess.run(
                ["bash", "-c", validation],
                env={"PATH": "/usr/bin:/bin", "TEST_PROFILE": profile, "INPUTS_JSON": inputs},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        for profile, inputs in (
            ("host", '{"build_number":"253"}'),
            ("testflight", '{}'),
            ("testflight", '{"build_number":""}'),
            ("testflight", '{"build_number":253}'),
            ("testflight", '{"build_number":"253","extra":"x"}'),
        ):
            result = subprocess.run(
                ["bash", "-c", validation],
                env={"PATH": "/usr/bin:/bin", "TEST_PROFILE": profile, "INPUTS_JSON": inputs},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)

        release = jobs["apple_release"]
        self.assertEqual(
            release["if"],
            "${{ needs.request.outputs.workflow_key == 'release.apple' && needs.request.outputs.test_profile == 'testflight' }}",
        )
        self.assertEqual(release["uses"], "./.github/workflows/apple.yml")
        self.assertEqual(
            set(release["with"]),
            {"repository", "ref", "source_is_tag", "test_profile", "build_number", "ci_run_id"},
        )
        self.assertEqual(release["with"]["test_profile"], "testflight")
        self.assertEqual(
            release["with"]["build_number"],
            "${{ fromJSON(needs.request.outputs.inputs_json).build_number }}",
        )
        self.assertNotIn("ref", release["with"]["build_number"])
        self.assertNotIn("sha", release["with"]["build_number"].lower())
        self.assertEqual(
            release["with"]["source_is_tag"],
            "${{ needs.request.outputs.is_tag == 'true' }}",
        )
        self.assertEqual(release["concurrency"]["group"], "central-ci-${{ inputs.active_key }}")
        self.assertTrue(release["concurrency"]["cancel-in-progress"])
        self.assertEqual(release["secrets"], "inherit")

        validation_job = jobs["apple"]
        self.assertNotIn("build_number", validation_job["with"])
        self.assertEqual(
            validation_job["if"],
            "${{ needs.request.outputs.workflow_key == 'validation.apple' }}",
        )
        settlement = jobs["settle_cancelled"]
        self.assertIn("apple_release", settlement["needs"])
        self.assertIn("needs.apple_release.result == 'cancelled'", settlement["if"])

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
        self.assertIn("run_xcode_logged()", script)
        self.assertIn(
            "http.https://github.com/StreamScapeTV/streamscape-media.git.extraheader",
            script,
        )
        self.assertIn("GIT_CONFIG_COUNT=1", script)
        self.assertIn("GIT_CONFIG_VALUE_0=\"AUTHORIZATION: basic ${private_git_auth}\"", script)
        self.assertNotIn("git config --global", script)
        self.assertNotIn("git config --local", script)
        self.assertNotIn("insteadOf", script)
        self.assertEqual(script.count("materialize_historical_media_bootstrap"), 4)
        self.assertEqual(script.count("run_xcode_logged"), 4)
        self.assertEqual(
            script.count("run_logged prepare-media bash scripts/bootstrap-streamscape-media-binary.sh"),
            4,
        )
        release_start = script.index('release-build)')
        release_end = script.index('swift-package)', release_start)
        release_block = script[release_start:release_end]
        self.assertIn("test -f scripts/bootstrap-streamscape-media-binary.sh", release_block)
        self.assertNotIn("materialize_historical_media_bootstrap", release_block)
        self.assertNotIn("run_xcode_logged", release_block)

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
[[ "${{historical_recovery_active}}" == true ]]
[[ "$(cat "$CI_LOG")" == *'Materialized approved historical Apple bootstrap helper'* ]]

run_logged() {{
  local label="$1"
  shift
  "$@"
}}
export CI_GITHUB_TOKEN='test-token'
expected_auth="$(printf 'x-access-token:%s' "$CI_GITHUB_TOKEN" | /usr/bin/base64 | tr -d '\r\n')"
assert_historical_git_env() {{
  [[ "${{GIT_CONFIG_COUNT:-}}" == 1 ]]
  [[ "${{GIT_CONFIG_KEY_0:-}}" == 'http.https://github.com/StreamScapeTV/streamscape-media.git.extraheader' ]]
  [[ "${{GIT_CONFIG_VALUE_0:-}}" == "AUTHORIZATION: basic $expected_auth" ]]
}}
run_xcode_logged auth-probe assert_historical_git_env
[[ -z "${{GIT_CONFIG_COUNT:-}}" ]]
[[ -z "${{GIT_CONFIG_KEY_0:-}}" ]]
[[ -z "${{GIT_CONFIG_VALUE_0:-}}" ]]

# Current/non-historical Xcode receives no private Git config.
historical_recovery_active=false
assert_no_historical_git_env() {{
  [[ -z "${{GIT_CONFIG_COUNT:-}}" ]]
  [[ -z "${{GIT_CONFIG_KEY_0:-}}" ]]
  [[ -z "${{GIT_CONFIG_VALUE_0:-}}" ]]
}}
run_xcode_logged normal-probe assert_no_historical_git_env

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

    def test_parallel_lanes_record_source_and_keep_independent_readable_logs(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        steps = execute["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        identity = by_name["Resolve observed source SHA"]
        record = by_name["Record observed source SHA"]
        scrub = by_name["Scrub configured CI secrets from private log"]
        drive = by_name["Upload CI log to Google Drive"]

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
        self.assertLess(names.index("Record observed source SHA"), names.index("Run fixed Apple lane"))

        self.assertEqual(scrub["if"], "${{ always() }}")
        self.assertIn("steps.scrub.outcome == 'success'", drive["if"])
        self.assertLess(names.index("Run fixed Apple lane"), names.index("Scrub configured CI secrets from private log"))
        self.assertLess(names.index("Scrub configured CI secrets from private log"), names.index("Upload CI log to Google Drive"))
        self.assertEqual(
            drive["with"]["file_path"],
            "${{ runner.temp }}/central-ci-${{ matrix.lane }}.log",
        )
        self.assertEqual(
            drive["with"]["file_name"],
            "${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.lane }}.txt",
        )
        self.assertNotIn("gzip", drive["with"])
        self.assertEqual(drive["with"]["mime_type"], "text/plain")

    def test_dependency_cache_uses_native_github_cache_with_default_branch_only_writes(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        by_name = {step.get("name"): step for step in execute["steps"] if step.get("name")}
        scope = by_name["Resolve Apple default-branch dependency cache scope"]
        prepare = scope["run"]
        restore = by_name["Restore Apple default-branch dependency cache"]
        restore_record = by_name["Record Apple default-branch cache restore"]
        contents = by_name["Record Apple default-branch cache contents"]
        save = by_name["Save Apple default-branch dependency cache"]

        self.assertIn("restore_eligible=false", prepare)
        self.assertIn('test "${source_repository}" = "StreamScapeTV/iptv-apple"', prepare)
        self.assertIn('test "${source_is_tag}" != "true"', prepare)
        self.assertIn('refs/pull/*', prepare)
        self.assertIn("https://api.github.com/repos/${source_repository}", prepare)
        self.assertIn('get("default_branch", "")', prepare)
        self.assertIn('test "${source_ref}" = "${default_branch}"', prepare)
        self.assertIn('test "${checkout_branch}" = "${default_branch}"', prepare)
        self.assertIn('test "${source_is_pr}" != "true"', prepare)
        self.assertIn("fingerprint_ready=false", prepare)
        self.assertIn("test -f scripts/bootstrap-streamscape-media-binary.sh", prepare)
        self.assertIn("test -f streamscapetv.xcodeproj/project.pbxproj", prepare)
        self.assertIn("restore_enabled=false", prepare)
        self.assertIn("save_enabled=false", prepare)
        self.assertIn("iptv-apple-default-deps-v3-", prepare)
        self.assertNotIn("dependency-cache", prepare)
        self.assertNotIn("oauth2.googleapis.com", prepare)
        self.assertNotIn("GOOGLE_DRIVE", scope.get("env", {}))

        self.assertEqual(
            restore["if"],
            "${{ steps.apple_default_cache.outputs.restore_enabled == 'true' }}",
        )
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(
            restore["with"]["path"],
            "Vendor/StreamscapeMediaApple\n${{ steps.apple_default_cache.outputs.swiftpm_clones_dir }}\n",
        )
        self.assertEqual(restore["with"]["key"], "${{ steps.apple_default_cache.outputs.key }}")
        self.assertEqual(
            restore_record["if"],
            "${{ steps.apple_default_cache.outputs.restore_enabled == 'true' }}",
        )
        self.assertIn("steps.apple_default_cache.outputs.save_enabled == 'true'", contents["if"])
        self.assertIn("matrix.cache_save", contents["if"])
        self.assertEqual(save["uses"], "actions/cache/save@v4")
        self.assertIn("steps.apple_default_cache.outputs.save_enabled == 'true'", save["if"])
        self.assertIn("matrix.cache_save", save["if"])
        self.assertIn("steps.apple_default_cache_restore.outputs.cache-hit != 'true'", save["if"])
        self.assertEqual(save["with"]["key"], "${{ steps.apple_default_cache_restore.outputs.cache-primary-key }}")
        self.assertEqual(save["with"]["path"], restore["with"]["path"])
        self.assertNotIn("StreamScapeTV/ci-workflows/actions/google-drive", str(restore))
        self.assertNotIn("StreamScapeTV/ci-workflows/actions/google-drive", str(save))
        self.assertNotIn("DerivedData", self.text)
        self.assertNotIn("APPLE_DEVELOP_CACHE_ENABLED", self.text)
        self.assertIn("APPLE_DEFAULT_CACHE_ENABLED", self.text)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "scripts").mkdir()
            (root / "scripts/bootstrap-streamscape-media-binary.sh").write_text(
                "#!/bin/sh\n",
                encoding="utf-8",
            )
            (root / "streamscapetv.xcodeproj").mkdir()
            (root / "streamscapetv.xcodeproj/project.pbxproj").write_text(
                "// fixture\n",
                encoding="utf-8",
            )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_xcodebuild = fake_bin / "xcodebuild"
            fake_xcodebuild.write_text(
                "#!/bin/sh\nprintf 'Xcode 16.4\\nBuild version 16F6\\n'\n",
                encoding="utf-8",
            )
            fake_xcodebuild.chmod(0o755)
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                "#!/bin/sh\nprintf '{\"default_branch\":\"%s\"}\\n' \"${FAKE_DEFAULT_BRANCH:-develop}\"\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            subprocess.run(["git", "init", "-b", "develop"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "CI"], cwd=root, check=True)
            subprocess.run(["git", "add", "scripts/bootstrap-streamscape-media-binary.sh", "streamscapetv.xcodeproj/project.pbxproj"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)

            def cache_flags(
                repository: str,
                ref: str,
                *,
                is_tag: bool = False,
                default_branch: str = "develop",
            ) -> dict[str, str]:
                output = root / "github-output"
                github_env = root / "github-env"
                output.write_text("", encoding="utf-8")
                github_env.write_text("", encoding="utf-8")
                env = os.environ.copy()
                env.update(
                    {
                        "REQUEST_REPOSITORY": repository,
                        "REQUEST_REF": ref,
                        "REQUEST_IS_TAG": "true" if is_tag else "false",
                        "CALLER_REPOSITORY": "StreamScapeTV/ci-workflows",
                        "CALLER_REF": "refs/heads/main",
                        "SOURCE_TOKEN": "token",
                        "FAKE_DEFAULT_BRANCH": default_branch,
                        "RUNNER_TEMP": str(runner_temp),
                        "GITHUB_OUTPUT": str(output),
                        "GITHUB_ENV": str(github_env),
                        "PATH": f"{fake_bin}:{env['PATH']}",
                    }
                )
                result = subprocess.run(
                    ["bash"],
                    cwd=root,
                    env=env,
                    input=prepare,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return dict(
                    line.split("=", 1)
                    for line in output.read_text(encoding="utf-8").splitlines()
                )

            develop = cache_flags("StreamScapeTV/iptv-apple", "develop")
            main = cache_flags("StreamScapeTV/iptv-apple", "main")
            feature = cache_flags("StreamScapeTV/iptv-apple", "feature/cache")
            pull_request = cache_flags("StreamScapeTV/iptv-apple", "refs/pull/42/merge")
            tag = cache_flags("StreamScapeTV/iptv-apple", "refs/tags/1.2.3")
            unrelated = cache_flags("StreamScapeTV/other", "develop")

            self.assertEqual((develop["restore_enabled"], develop["save_enabled"]), ("true", "true"))
            self.assertEqual((main["restore_enabled"], main["save_enabled"]), ("true", "false"))
            self.assertEqual((feature["restore_enabled"], feature["save_enabled"]), ("true", "false"))
            self.assertEqual((pull_request["restore_enabled"], pull_request["save_enabled"]), ("true", "false"))
            self.assertEqual((tag["restore_enabled"], tag["save_enabled"]), ("false", "false"))
            self.assertEqual((unrelated["restore_enabled"], unrelated["save_enabled"]), ("false", "false"))

            subprocess.run(["git", "switch", "-c", "main"], cwd=root, check=True, capture_output=True)
            main_default = cache_flags("StreamScapeTV/iptv-apple", "main", default_branch="main")
            old_develop = cache_flags("StreamScapeTV/iptv-apple", "develop", default_branch="main")
            self.assertEqual((main_default["restore_enabled"], main_default["save_enabled"]), ("true", "true"))
            self.assertEqual((old_develop["restore_enabled"], old_develop["save_enabled"]), ("true", "false"))

            (root / "scripts/bootstrap-streamscape-media-binary.sh").unlink()
            historical = cache_flags(
                "StreamScapeTV/iptv-apple",
                "512db0f5b2513ad7d3a2b53bbc132ea29742bb63",
            )
            self.assertEqual(historical["restore_eligible"], "true")
            self.assertEqual((historical["restore_enabled"], historical["save_enabled"]), ("false", "false"))
            self.assertNotIn("key", historical)

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
