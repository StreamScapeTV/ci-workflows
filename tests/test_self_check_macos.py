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

    def test_preinstalled_python_selection_is_exact_and_temporary(self) -> None:
        source = workflow_source()
        lock = json.loads(
            (ROOT / "contracts/action-tool-lock.json").read_text()
        )

        self.assertNotIn("actions/setup-python@", source)
        self.assertNotIn("sudo", source)
        self.assertNotIn("brew install", source)
        self.assertNotIn("for candidate", source)
        self.assertIn("Select preinstalled CPython 3.12", source)
        self.assertIn("selected=/opt/homebrew/bin/python3.12", source)
        self.assertIn("sys.version_info[:2]", source)
        self.assertIn("'(3, 12)'", source)
        self.assertIn('"${machine}" == arm64', source)
        self.assertIn(
            'runtime_bin="${RUNNER_TEMP}/ci-workflows-python-bin"',
            source,
        )
        self.assertIn(
            'ln -s "${resolved}" "${runtime_bin}/python3"',
            source,
        )
        self.assertIn(
            'printf \'PYTHON_RUNTIME_BIN=%s\\n\' "${runtime_bin}"',
            source,
        )
        self.assertIn('rm -rf "${PYTHON_RUNTIME_BIN}"', source)
        self.assertLess(
            source.index("Admit trusted workflow source"),
            source.index("Select preinstalled CPython 3.12"),
        )
        self.assertLess(
            source.index("Select preinstalled CPython 3.12"),
            source.index("Check out exact source"),
        )

        setup = [
            entry
            for entry in lock["third_party_actions"]
            if entry["uses"] == "actions/setup-python"
        ]
        self.assertEqual([], setup)

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
