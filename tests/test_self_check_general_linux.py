from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SELECTOR_LINE = "runs-on: [ubuntu-latest]"


def workflow_source() -> str:
    return (ROOT / ".github/workflows/self-check.yml").read_text(encoding="utf-8")


def named_script(name: str) -> str:
    steps = yaml.safe_load(workflow_source())["jobs"]["validate"]["steps"]
    matches = [step["run"] for step in steps if step.get("name") == name]
    if len(matches) != 1:
        raise AssertionError(f"workflow step {name!r} is not unique")
    return matches[0]


def run_admission(
    event: str,
    *,
    fork: bool = False,
    mismatch: bool = False,
    author: str = "mimranfaruqi",
) -> subprocess.CompletedProcess[str]:
    sha = "a" * 40
    environment = {
        **os.environ,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
        "GITHUB_SHA": "b" * 40 if mismatch else sha,
        "SOURCE_SHA": sha,
        "PR_AUTHOR": author,
        "PR_HEAD_REPOSITORY": "external/fork" if fork else "StreamScapeTV/ci-workflows",
        "PR_HEAD_SHA": "b" * 40 if mismatch else sha,
    }
    return subprocess.run(
        ["bash", "-c", named_script("Admit trusted workflow source")],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


class GeneralLinuxSelfCheckTest(unittest.TestCase):
    def test_same_repository_admission_and_runtime_check_precede_checkout(self) -> None:
        source = workflow_source()
        self.assertLess(
            source.index("Admit trusted workflow source"),
            source.index("Verify pre-provisioned general-Linux CPython 3.12"),
        )
        self.assertLess(
            source.index("Verify pre-provisioned general-Linux CPython 3.12"),
            source.index("Check out exact source"),
        )
        self.assertEqual(0, run_admission("pull_request").returncode)

    def test_non_owner_fork_and_mismatched_pr_head_are_rejected(self) -> None:
        self.assertNotEqual(
            0,
            run_admission("pull_request", author="external-user").returncode,
        )
        self.assertNotEqual(0, run_admission("pull_request", fork=True).returncode)
        self.assertNotEqual(
            0,
            run_admission("pull_request", mismatch=True).returncode,
        )

    def test_push_and_dispatch_require_exact_github_sha(self) -> None:
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event):
                self.assertEqual(0, run_admission(event).returncode)
                self.assertNotEqual(0, run_admission(event, mismatch=True).returncode)

    def test_central_uses_only_standard_github_hosted_linux(self) -> None:
        source = workflow_source()
        workflow = yaml.safe_load(source)
        self.assertEqual(workflow["jobs"]["validate"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(1, source.count(SELECTOR_LINE))
        for forbidden in (
            "runs-on: portable",
            "runs-on: [linux, amd64, general, small]",
            "runs-on: macOS",
            "runs-on: apple",
            "macos-latest",
            "windows-latest",
            "self-hosted",
            "runs-on: mobile",
            "runs-on: buildah",
            "runs-on: flux-control",
        ):
            self.assertNotIn(forbidden, source)

    def test_hosted_selector_is_bound_to_backend_contract(self) -> None:
        contract = json.loads(
            (ROOT / "contracts/runner-execution-backends.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["github-hosted"]["runs_on"], ["ubuntu-latest"])

    def test_preprovisioned_python_and_repository_requirements_drive_validation(self) -> None:
        source = workflow_source()
        self.assertIn("type -P python3.12", source)
        self.assertIn("os.path.realpath(sys.executable)", source)
        self.assertIn("test \"${implementation}\" = cpython", source)
        self.assertIn("test \"${version}\" = 3.12", source)
        self.assertIn("test \"${system}\" = Linux", source)
        self.assertIn("VERIFIED_PYTHON=%s", source)
        self.assertIn('"${VERIFIED_PYTHON}" -m pip install', source)
        self.assertIn("requirements/validation.txt", source)
        self.assertNotIn("bootstrap_validation_runtime.py", source)
        self.assertNotIn("action-tool-lock.json", source)
        requirements = (ROOT / "requirements/validation.txt").read_text(encoding="utf-8")
        self.assertIn("PyYAML", requirements)
        self.assertNotIn("sha256", requirements.casefold())
        self.assertNotIn("--hash", requirements)

    def test_verified_interpreter_runs_repository_validation_commands(self) -> None:
        source = workflow_source()
        checkout = source.index("- name: Check out exact source")
        after_checkout = source[checkout:]
        for command in (
            "validation_harness.py",
            "bootstrap_check.py",
            "inventory_contract.py",
            "public_api_contract.py",
            "-m unittest discover",
        ):
            self.assertIn(command, after_checkout)
        self.assertGreaterEqual(after_checkout.count("${VERIFIED_PYTHON}"), 6)

    def test_checkout_does_not_persist_credentials(self) -> None:
        workflow = yaml.safe_load(workflow_source())
        checkout = next(
            step
            for step in workflow["jobs"]["validate"]["steps"]
            if step.get("name") == "Check out exact source"
        )
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertTrue(checkout["with"]["clean"])
        self.assertEqual(checkout["with"]["fetch-depth"], 1)

    def test_hosted_self_check_has_no_private_or_privileged_authority(self) -> None:
        source = workflow_source().casefold()
        for forbidden in (
            "secrets.",
            "secrets: inherit",
            "signing",
            "provisioning",
            "app-store",
            "notary",
            "simctl",
            "xcodebuild",
            "kubeconfig",
            "registry_token",
            "agent_state",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
