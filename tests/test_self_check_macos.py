from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_check",
    ROOT / "scripts/ci/bootstrap_check.py",
)
assert SPEC and SPEC.loader
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)
ORIGINAL_READ_TEXT = BOOTSTRAP.read_text


def workflow_source() -> str:
    return (ROOT / ".github/workflows/self-check.yml").read_text(
        encoding="utf-8"
    )


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
) -> subprocess.CompletedProcess[str]:
    sha = "a" * 40
    environment = {
        **os.environ,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
        "GITHUB_SHA": "b" * 40 if mismatch else sha,
        "SOURCE_SHA": sha,
        "PR_HEAD_REPOSITORY": (
            "external/fork" if fork else "StreamScapeTV/ci-workflows"
        ),
        "PR_HEAD_SHA": "b" * 40 if mismatch else sha,
    }
    return subprocess.run(
        ["bash", "-c", named_script("Admit trusted workflow source")],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def validate_mutation(mutated: str) -> None:
    def read_text(relative: str) -> str:
        if relative == ".github/workflows/self-check.yml":
            return mutated
        return ORIGINAL_READ_TEXT(relative)

    with mock.patch.object(
        BOOTSTRAP,
        "read_text",
        side_effect=read_text,
    ):
        BOOTSTRAP.validate_self_check()


class EmergencyMacOSSelfCheckTest(unittest.TestCase):
    def test_same_repository_pr_admission_precedes_checkout(self) -> None:
        source = workflow_source()
        self.assertLess(
            source.index("Admit trusted workflow source"),
            source.index("Verify pre-provisioned CPython 3.12.13"),
        )
        self.assertLess(
            source.index("Verify pre-provisioned CPython 3.12.13"),
            source.index("Check out exact source"),
        )
        self.assertEqual(0, run_admission("pull_request").returncode)

    def test_fork_pr_and_mismatched_pr_head_are_rejected(self) -> None:
        fork = run_admission("pull_request", fork=True)
        self.assertNotEqual(0, fork.returncode)
        self.assertIn("rejects fork pull-request source", fork.stderr)
        self.assertNotEqual(
            0,
            run_admission("pull_request", mismatch=True).returncode,
        )

    def test_push_and_dispatch_require_exact_github_sha(self) -> None:
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event):
                self.assertEqual(0, run_admission(event).returncode)
                self.assertNotEqual(
                    0,
                    run_admission(event, mismatch=True).returncode,
                )

    def test_selector_substitutions_are_rejected(self) -> None:
        original = workflow_source()
        substitutions = (
            "macos-latest",
            "ubuntu-latest",
            "windows-latest",
            "self-hosted",
            "[self-hosted, macOS]",
            "${{ inputs.runner }}",
            "portable",
            "mobile",
            "buildah",
            "buildah-tiny",
            "buildah-small",
            "buildah-medium",
            "buildah-high",
        )
        for selector in substitutions:
            with self.subTest(selector=selector):
                mutated = original.replace(
                    "runs-on: macOS",
                    f"runs-on: {selector}",
                )
                with self.assertRaises(SystemExit):
                    validate_mutation(mutated)

    def test_runtime_setup_and_privilege_escalation_are_rejected(self) -> None:
        original = workflow_source()
        forbidden = (
            "actions/setup-python@" + "a" * 40,
            "sudo mkdir /Users/runner/hostedtoolcache",
            "brew install python@3.12",
            "python -m pip install PyYAML",
            "virtualenv .venv",
            "pyenv install 3.12.13",
            "conda install python=3.12.13",
            "curl -fsSLo python.tar.gz https://example.invalid/python.tar.gz",
            "wget https://example.invalid/python.tar.gz",
        )
        for command in forbidden:
            with self.subTest(command=command):
                with self.assertRaises(SystemExit):
                    validate_mutation(original + f"\n# {command}\n")

    def test_exact_host_identity_mutations_are_rejected(self) -> None:
        original = workflow_source()
        mutations = (
            ('"${version}" == "3.12.13"', '"${version}" == "3.12.12"'),
            (
                '"${implementation}" == "cpython"',
                '"${implementation}" == "pypy"',
            ),
            ('"${system}" == "Darwin"', '"${system}" == "Linux"'),
            ("arm64|x86_64)", "arm64|ppc64)"),
        )
        for old, new in mutations:
            with self.subTest(new=new):
                with self.assertRaises(SystemExit):
                    validate_mutation(original.replace(old, new))

    def test_relative_or_unverified_interpreter_is_rejected(self) -> None:
        original = workflow_source()
        mutations = (
            (
                '"${resolved}" != /* || ! -x "${resolved}"',
                '! -x "${resolved}"',
            ),
            (
                'resolved="$("${candidate}" -c '
                "'import os,sys; print(os.path.realpath(sys.executable))')\"",
                'resolved="${candidate}"',
            ),
            (
                '"${VERIFIED_PYTHON}" scripts/ci/bootstrap_check.py',
                "python3 scripts/ci/bootstrap_check.py",
            ),
            (
                '"${VERIFIED_PYTHON}" -m unittest discover',
                "python3 -m unittest discover",
            ),
        )
        for old, new in mutations:
            with self.subTest(new=new):
                mutated = original.replace(old, new)
                self.assertNotEqual(original, mutated)
                with self.assertRaises(SystemExit):
                    validate_mutation(mutated)

    def test_action_lock_records_preprovisioned_host_not_setup_action(self) -> None:
        lock = json.loads(
            (ROOT / "contracts/action-tool-lock.json").read_text()
        )
        action_names = {
            entry["uses"] for entry in lock["third_party_actions"]
        }
        self.assertNotIn("actions/setup-python", action_names)
        self.assertNotIn("actions/setup-python@", workflow_source())
        self.assertEqual(
            {
                "implementation": "cpython",
                "version": "3.12.13",
                "system": "Darwin",
                "architectures": ["arm64", "x86_64"],
                "provisioning": "preinstalled",
                "runtime_installation": "forbidden",
                "privilege_elevation": "forbidden",
            },
            lock["python"]["emergency_macos_host"],
        )

    def test_verified_interpreter_is_used_for_every_later_python_command(
        self,
    ) -> None:
        source = workflow_source()
        checkout = source.index("- name: Check out exact source")
        after_checkout = source[checkout:]
        self.assertNotRegex(
            after_checkout,
            r"(?<![A-Za-z0-9_])python(?:3(?:\.[0-9]+)?)?(?=\s)",
        )
        for command in (
            "bootstrap_validation_runtime.py",
            "validation_harness.py",
            "bootstrap_check.py",
            "inventory_contract.py",
            "public_api_contract.py",
            "-m unittest discover",
        ):
            self.assertIn("${VERIFIED_PYTHON}", after_checkout)
            self.assertIn(command, after_checkout)

    def test_exception_grants_no_sensitive_apple_capability(self) -> None:
        source = workflow_source().lower()
        for forbidden in (
            "secrets.",
            "signing",
            "provisioning",
            "app-store",
            "notary",
            "simctl",
            "xcodebuild",
            "device",
            "kubeconfig",
            "registry_token",
            "agent_state",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
