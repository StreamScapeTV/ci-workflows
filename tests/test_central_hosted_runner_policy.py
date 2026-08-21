from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
HOSTED = ["ubuntu-latest"]
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


class CentralHostedRunnerPolicyTests(unittest.TestCase):
    def test_every_repository_local_runnable_job_is_github_hosted(self) -> None:
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
                if "runs-on" in job and job["runs-on"] != HOSTED:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{job_name} uses {job['runs-on']!r}"
                    )
                called = _self_reusable(job.get("uses"))
                if called is not None:
                    inspect(called)

        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
            if _events(workflow) - {"workflow_call"}:
                inspect(path)

        self.assertEqual(failures, [], "Central runnable jobs must use ubuntu-latest:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
