from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ci" / "release_tag_contract.py"

spec = importlib.util.spec_from_file_location("release_tag_contract", MODULE_PATH)
assert spec is not None and spec.loader is not None
release_tag_contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release_tag_contract
spec.loader.exec_module(release_tag_contract)


class ReleaseTagContractTests(unittest.TestCase):
    def resolve(
        self,
        workflow_key: str,
        profile: str,
        *,
        is_tag: bool,
        ref: str,
        inputs: dict[str, object],
    ) -> dict[str, str]:
        value = release_tag_contract.resolve_release_identity(
            workflow_key=workflow_key,
            profile=profile,
            is_tag=is_tag,
            ref=ref,
            inputs=inputs,
        )
        return value.as_dict()

    def test_android_mobile_tag_resolves_source_matched_identity(self) -> None:
        self.assertEqual(
            self.resolve("release.android", "play", is_tag=True, ref="1.0.0_257", inputs={}),
            {"mode": "mobile_tag", "version": "1.0.0", "build_number": "257"},
        )

    def test_apple_mobile_tag_resolves_source_matched_identity(self) -> None:
        self.assertEqual(
            self.resolve("release.apple", "testflight", is_tag=True, ref="1.0_17", inputs={}),
            {"mode": "mobile_tag", "version": "1.0", "build_number": "17"},
        )

    def test_mobile_tag_rejects_v_prefix_zero_and_extra_text(self) -> None:
        for tag in ("v1.0.0_257", "1.0.0_0", "1.0.0_build257", "1.0.0_257_extra"):
            with self.subTest(tag=tag), self.assertRaises(release_tag_contract.ContractError):
                release_tag_contract.parse_mobile_tag(tag, android=False)

    def test_android_mobile_tag_rejects_version_code_above_platform_max(self) -> None:
        with self.assertRaises(release_tag_contract.ContractError):
            release_tag_contract.parse_mobile_tag("1.0.0_2100000001", android=True)

    def test_manual_android_release_keeps_existing_build_number_contract(self) -> None:
        self.assertEqual(
            self.resolve(
                "release.android",
                "play",
                is_tag=False,
                ref="develop",
                inputs={"build_number": "257"},
            ),
            {"mode": "explicit", "version": "", "build_number": "257"},
        )

    def test_manual_apple_release_keeps_existing_build_number_contract(self) -> None:
        self.assertEqual(
            self.resolve(
                "release.apple",
                "testflight",
                is_tag=False,
                ref="main",
                inputs={"build_number": "257"},
            ),
            {"mode": "explicit", "version": "", "build_number": "257"},
        )

    def test_maven_plain_tag_becomes_release_version(self) -> None:
        self.assertEqual(
            self.resolve("release.maven", "publish", is_tag=True, ref="2.4.1", inputs={}),
            {"mode": "version_tag", "version": "2.4.1", "build_number": "2.4.1"},
        )

    def test_maven_plain_tag_rejects_mobile_build_counter_form(self) -> None:
        with self.assertRaises(release_tag_contract.ContractError):
            release_tag_contract.parse_plain_version_tag("2.4.1_257")

    def test_non_testflight_apple_profiles_remain_zero_input_passthrough(self) -> None:
        self.assertEqual(
            self.resolve("release.apple", "swiftpm-package", is_tag=True, ref="2.4.1", inputs={}),
            {"mode": "passthrough", "version": "", "build_number": ""},
        )

    def test_tag_driven_mobile_release_rejects_legacy_build_number_input(self) -> None:
        with self.assertRaises(release_tag_contract.ContractError):
            self.resolve(
                "release.android",
                "play",
                is_tag=True,
                ref="1.0.0_257",
                inputs={"build_number": "257"},
            )

    def test_tag_driven_maven_release_rejects_legacy_build_number_input(self) -> None:
        with self.assertRaises(release_tag_contract.ContractError):
            self.resolve(
                "release.maven",
                "publish",
                is_tag=True,
                ref="2.4.1",
                inputs={"build_number": "2.4.1"},
            )

    def test_cli_emits_bounded_json_identity(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "resolve",
                "--workflow-key",
                "release.android",
                "--profile",
                "play",
                "--is-tag",
                "true",
                "--ref",
                "1.2.3_41",
                "--inputs-json",
                "{}",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {"mode": "mobile_tag", "version": "1.2.3", "build_number": "41"},
        )


if __name__ == "__main__":
    unittest.main()
