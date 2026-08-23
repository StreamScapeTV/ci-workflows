from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
HOSTED_LINUX = ["ubuntu-latest"]
HOSTED_APPLE = ["macos-latest"]
APPLE_PILOT = WORKFLOWS / "apple-test.yml"
SELF_PREFIX = "StreamScapeTV/ci-workflows/.github/workflows/"


def _events(workflow: dict) -> set[str]:
    value = workflow.get("on", {})
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, list):
        return {str(item) for item in value}
    if isinstance(value, str):
        return {value}
    return set()


def _cannot_run_in_public_central(job: dict) -> bool:
    condition = str(job.get("if", "")).replace(" ", "").lower()
    if condition in {"false", "${{false}}"}:
        return True
    return (
        "github.event.repository.private" in condition
        and "!github.event.repository.private" not in condition
        and "github.event.repository.private==false" not in condition
        and "github.event.repository.private!=true" not in condition
    )


def _self_reusable(uses: object) -> Path | None:
    if not isinstance(uses, str):
        return None
    if uses.startswith("./.github/workflows/"):
        relative = uses.removeprefix("./")
    elif uses.startswith(SELF_PREFIX):
        relative = ".github/workflows/" + uses.removeprefix(SELF_PREFIX).split("@", 1)[0]
    else:
        return None
    path = ROOT / relative
    return path if path.is_file() else None


def _expected_hosted_selector(path: Path, job_name: str) -> list[str]:
    if path == APPLE_PILOT and job_name == "apple_test":
        return HOSTED_APPLE
    return HOSTED_LINUX


class CentralHostedRunnerPolicyTests(unittest.TestCase):
    def test_every_repository_local_runnable_job_uses_reviewed_github_hosted_capacity(self) -> None:
        visited: set[Path] = set()
        failures: list[str] = []

        def inspect(path: Path) -> None:
            if path in visited:
                return
            visited.add(path)
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
            for job_name, job in workflow.get("jobs", {}).items():
                if _cannot_run_in_public_central(job):
                    continue
                expected = _expected_hosted_selector(path, job_name)
                if "runs-on" in job and job["runs-on"] != expected:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{job_name} uses {job['runs-on']!r}; expected {expected!r}"
                    )
                called = _self_reusable(job.get("uses"))
                if called is not None:
                    inspect(called)

        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
            if _events(workflow) - {"workflow_call"}:
                inspect(path)

        self.assertEqual(
            failures,
            [],
            "Central runnable jobs must use their exact reviewed GitHub-hosted selector:\n"
            + "\n".join(failures),
        )

    def test_manual_apple_pilot_is_the_only_named_macos_exception(self) -> None:
        workflow = yaml.load(APPLE_PILOT.read_text(encoding="utf-8"), Loader=ActionsLoader)
        self.assertEqual(_events(workflow), {"workflow_dispatch"})
        self.assertEqual(set(workflow["jobs"]), {"apple_test"})
        self.assertEqual(workflow["jobs"]["apple_test"]["runs-on"], HOSTED_APPLE)
        self.assertNotIn("workflow_call", workflow["on"])


if __name__ == "__main__":
    unittest.main()
