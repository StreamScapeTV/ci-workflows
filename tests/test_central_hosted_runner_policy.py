from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
HOSTED = ["ubuntu-latest"]
OWNER_GATE = "github.event.pull_request.user.login == 'mimranfaruqi'"
REPOSITORY_GATE = "github.event.pull_request.head.repo.full_name == github.repository"


def _events(workflow: dict) -> set[str]:
    value = workflow.get("on", {})
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, list):
        return {str(item) for item in value}
    if isinstance(value, str):
        return {value}
    return set()


def _disabled(job: dict) -> bool:
    condition = "".join(str(job.get("if", "")).lower().split())
    return condition in {"false", "${{false}}"}


class CentralHostedRunnerPolicyTests(unittest.TestCase):
    def test_public_pr_jobs_allocate_only_after_exact_owner_same_repo_admission(self) -> None:
        failures: list[str] = []
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
            if "pull_request" not in _events(workflow):
                continue
            for job_name, job in workflow.get("jobs", {}).items():
                if _disabled(job):
                    continue
                condition = " ".join(str(job.get("if", "")).split())
                if OWNER_GATE not in condition or REPOSITORY_GATE not in condition:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{job_name} lacks exact owner/same-repo admission"
                    )
        self.assertEqual(
            failures,
            [],
            "Public Central PR jobs must reject before runner allocation:\n" + "\n".join(failures),
        )

    def test_trusted_pr_specialized_runners_do_not_depend_on_repository_privacy(self) -> None:
        failures: list[str] = []
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
            if "pull_request" not in _events(workflow):
                continue
            for job_name, job in workflow.get("jobs", {}).items():
                if _disabled(job) or job.get("runs-on") == HOSTED or "runs-on" not in job:
                    continue
                condition = str(job.get("if", ""))
                if "github.event.repository.private" in condition:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{job_name} still depends on repository.private"
                    )
        self.assertEqual(
            failures,
            [],
            "Trusted specialized Central runners must use explicit trust, not repository visibility:\n"
            + "\n".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
