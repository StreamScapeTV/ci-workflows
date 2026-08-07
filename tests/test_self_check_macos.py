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


def admission_script() -> str:
    steps = yaml.safe_load(workflow_source())["jobs"]["validate"]["steps"]
    matches = [
        step["run"]
        for step in steps
        if step.get("name") == "Admit trusted workflow source"
    ]
    if len(matches) != 1:
        raise AssertionError("trusted-source admission step is not unique")
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
        ["bash", "-c", admission_script()],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


class EmergencyMacOSSelfCheckTest(unittest.TestCase):
    def test_same_repository_pr_admission_precedes_checkout(self) -> None:
        source = workflow_source()
        self.assertLess(
            source.index("Admit trusted workflow source"),
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
            contaminated = original.replace(
                "runs-on: macOS",
                f"runs-on: {selector}",
            )

            def read_text(relative: str) -> str:
                if relative == ".github/workflows/self-check.yml":
                    return contaminated
                return ORIGINAL_READ_TEXT(relative)

            with self.subTest(selector=selector), mock.patch.object(
                BOOTSTRAP,
                "read_text",
                side_effect=read_text,
            ), self.assertRaises(SystemExit):
                BOOTSTRAP.validate_self_check()

    def test_setup_python_and_lock_are_exact(self) -> None:
        source = workflow_source()
        lock = json.loads(
            (ROOT / "contracts/action-tool-lock.json").read_text()
        )
        self.assertIn(
            "actions/setup-python@"
            "5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0",
            source,
        )
        self.assertIn('python-version: "3.12.10"', source)
        setup = [
            entry
            for entry in lock["third_party_actions"]
            if entry["uses"] == "actions/setup-python"
        ]
        self.assertEqual(1, len(setup))
        self.assertEqual("v7.0.0", setup[0]["release"])
        self.assertEqual("node24", setup[0]["runtime"])

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
