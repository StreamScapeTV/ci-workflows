from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"expected one replacement target in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


apple = Path('.github/workflows/apple.yml')
replace_once(
    apple,
    "    outputs:\n      matrix: ${{ steps.profile.outputs.matrix }}\n",
    "    outputs:\n      matrix: ${{ steps.profile.outputs.matrix }}\n      runs_on: ${{ steps.profile.outputs.runs_on }}\n",
)
replace_once(
    apple,
    "          TEST_PROFILE: ${{ inputs.test_profile }}\n          TEST_SELECTORS: ${{ inputs.test_selectors }}\n          TEST_PLATFORM: ${{ inputs.test_platform }}\n",
    "          TEST_PROFILE: ${{ inputs.test_profile }}\n          TEST_SELECTORS: ${{ inputs.test_selectors }}\n          TEST_PLATFORM: ${{ inputs.test_platform }}\n          SOURCE_REPOSITORY: ${{ inputs.repository }}\n          CALLER_REPOSITORY: ${{ github.repository }}\n",
)
replace_once(
    apple,
    '          set -Eeuo pipefail\n          case "${TEST_PROFILE}" in\n',
    '          set -Eeuo pipefail\n          source_repository="${SOURCE_REPOSITORY:-${CALLER_REPOSITORY}}"\n          runs_on=\'["macos-latest"]\'\n          if test "${source_repository}" = StreamScapeTV/streamscape-media &&\n            test "${TEST_PROFILE}" = build; then\n            runs_on=\'["macOS","ARM64"]\'\n          fi\n          case "${TEST_PROFILE}" in\n',
)
replace_once(
    apple,
    "          printf 'matrix=%s\\n' \"${matrix}\" >> \"${GITHUB_OUTPUT}\"\n\n  execute:\n",
    "          printf 'matrix=%s\\n' \"${matrix}\" >> \"${GITHUB_OUTPUT}\"\n          printf 'runs_on=%s\\n' \"${runs_on}\" >> \"${GITHUB_OUTPUT}\"\n\n  execute:\n",
)
replace_once(
    apple,
    "    runs-on: macos-latest\n    timeout-minutes: 120\n",
    "    runs-on: ${{ fromJSON(needs.plan.outputs.runs_on) }}\n    timeout-minutes: 120\n",
)

binary = Path('.github/workflows/apple-binary.yml')
replace_once(
    binary,
    "  publish:\n    runs-on: macos-latest\n    timeout-minutes: 120\n",
    "  publish:\n    runs-on: ${{ fromJSON((inputs.repository || github.repository) == 'StreamScapeTV/streamscape-media' && '[\"macOS\",\"ARM64\"]' || '[\"macos-latest\"]') }}\n    timeout-minutes: 120\n",
)

apple_test = Path('tests/test_apple_workflow.py')
replace_once(
    apple_test,
    '        self.assertEqual(execute["runs-on"], "macos-latest")\n',
    '        self.assertEqual(execute["runs-on"], "${{ fromJSON(needs.plan.outputs.runs_on) }}")\n\n\n    def test_media_native_build_uses_fixed_self_hosted_mac_only(self) -> None:\n        plan = self.workflow["jobs"]["plan"]\n        self.assertEqual(plan["outputs"]["runs_on"], "${{ steps.profile.outputs.runs_on }}")\n        profile = next(\n            step for step in plan["steps"] if step.get("name") == "Resolve fixed Apple execution lanes"\n        )\n        self.assertEqual(profile["env"]["SOURCE_REPOSITORY"], "${{ inputs.repository }}")\n        self.assertEqual(profile["env"]["CALLER_REPOSITORY"], "${{ github.repository }}")\n        script = profile["run"]\n        self.assertIn("runs_on=\'[\\\"macos-latest\\\"]\'", script)\n        self.assertIn(\'test "${source_repository}" = StreamScapeTV/streamscape-media\', script)\n        self.assertIn(\'test "${TEST_PROFILE}" = build\', script)\n        self.assertIn("runs_on=\'[\\\"macOS\\\",\\\"ARM64\\\"]\'", script)\n        self.assertIn("printf \'runs_on=%s\\\\n\'", script)\n        self.assertNotIn("self-hosted", script)\n        self.assertNotIn("runner_label", self.text)\n\n        cache_scope = next(\n            step\n            for step in self.workflow["jobs"]["execute"]["steps"]\n            if step.get("name") == "Resolve Apple default-branch dependency cache scope"\n        )\n        self.assertIn("inputs.test_profile != \'build\'", cache_scope["if"])\n',
)

binary_test = Path('tests/test_apple_binary_workflow.py')
replace_once(
    binary_test,
    '        self.assertEqual(self.job["runs-on"], "macos-latest")\n',
    '        self.assertEqual(\n            self.job["runs-on"],\n            "${{ fromJSON((inputs.repository || github.repository) == \'StreamScapeTV/streamscape-media\' && \'[\\\"macOS\\\",\\\"ARM64\\\"]\' || \'[\\\"macos-latest\\\"]\') }}",\n        )\n',
)
replace_once(
    binary_test,
    '        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})\n\n        for forbidden in (\n',
    '        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})\n        self.assertIn("StreamScapeTV/streamscape-media", self.job["runs-on"])\n        self.assertIn("[\\\"macOS\\\",\\\"ARM64\\\"]", self.job["runs-on"])\n        self.assertIn("[\\\"macos-latest\\\"]", self.job["runs-on"])\n        self.assertNotIn("self-hosted", self.job["runs-on"])\n        self.assertNotIn("actions/cache", self.text)\n\n        for forbidden in (\n',
)
replace_once(
    binary_test,
    '            "Streamscape Media",\n            "streamscape-media",\n            "StreamscapePlaybackApple",\n',
    '            "Streamscape Media",\n            "StreamscapePlaybackApple",\n',
)
