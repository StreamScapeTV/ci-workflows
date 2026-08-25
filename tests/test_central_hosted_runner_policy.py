from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
HOSTED_LINUX = ["ubuntu-latest"]
HOSTED_APPLE = ["macos-latest"]
BROKER_RELEASE = WORKFLOWS / "ci-broker-image.yml"
BACKEND_CONTRACT = ROOT / "contracts" / "runner-execution-backends.json"
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


def _local_reusable(uses: object) -> Path | None:
    if not isinstance(uses, str) or not uses.startswith("./.github/workflows/"):
        return None
    relative = uses.removeprefix("./")
    path = ROOT / relative
    return path if path.is_file() else None


def _backend_contract() -> dict:
    return json.loads(BACKEND_CONTRACT.read_text(encoding="utf-8"))


def _expected_repository_local_selector(path: Path, job_name: str) -> list[str]:
    relative = str(path.relative_to(ROOT))
    contract = _backend_contract()
    for exception in contract.get("repository_local_trusted_publication_exceptions", []):
        if exception["workflow"] == relative and exception["job"] == job_name:
            return list(exception["exact_selector"])
    for exception in contract["github-hosted"].get("repository_local_exceptions", []):
        if exception["workflow"] == relative and exception["job"] == job_name:
            return list(exception["runs_on"])
    return list(contract["github-hosted"]["runs_on"])


class CentralHostedRunnerPolicyTests(unittest.TestCase):
    def test_every_repository_local_runnable_job_uses_reviewed_capacity(self) -> None:
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
                expected = _expected_repository_local_selector(path, job_name)
                if "runs-on" in job and job["runs-on"] != expected:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{job_name} uses {job['runs-on']!r}; expected {expected!r}"
                    )
                called = _local_reusable(job.get("uses"))
                if called is not None:
                    inspect(called)

        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
            if _events(workflow) - {"workflow_call"}:
                inspect(path)

        self.assertEqual(
            failures,
            [],
            "Central runnable jobs must use their exact reviewed selector:\n"
            + "\n".join(failures),
        )

    def test_pull_request_jobs_reject_before_runner_allocation_unless_publicly_disabled(self) -> None:
        failures: list[str] = []
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
            if "pull_request" not in _events(workflow):
                continue
            for job_name, job in workflow.get("jobs", {}).items():
                if _cannot_run_in_public_central(job):
                    continue
                condition = " ".join(str(job.get("if", "")).split())
                if OWNER_GATE not in condition or REPOSITORY_GATE not in condition:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{job_name} lacks exact owner/same-repository admission"
                    )
        self.assertEqual(
            failures,
            [],
            "Public Central pull_request jobs must reject before runner allocation:\n"
            + "\n".join(failures),
        )

    def test_reviewed_macos_exceptions_are_bounded_and_contract_owned(self) -> None:
        contract = _backend_contract()
        self.assertEqual(contract["github-hosted"]["runs_on"], HOSTED_LINUX)
        self.assertEqual(
            contract["github-hosted"]["repository_local_exceptions"],
            [
                {
                    "workflow": ".github/workflows/apple-validation-smoke.yml",
                    "job": "apple",
                    "events": ["pull_request"],
                    "runs_on": HOSTED_APPLE,
                },
                {
                    "workflow": ".github/workflows/flutter-apple-validation-smoke.yml",
                    "job": "ios",
                    "events": ["pull_request"],
                    "runs_on": HOSTED_APPLE,
                },
                {
                    "workflow": ".github/workflows/central-ci-dispatch.yml",
                    "job": "private",
                    "events": ["workflow_dispatch"],
                    "runs_on": HOSTED_APPLE,
                },
            ],
        )
        dispatch = yaml.load(
            (WORKFLOWS / "central-ci-dispatch.yml").read_text(encoding="utf-8"),
            Loader=ActionsLoader,
        )
        self.assertEqual(set(dispatch["jobs"]), {"private"})
        self.assertEqual(dispatch["jobs"]["private"]["runs-on"], HOSTED_APPLE)
        self.assertNotIn("uses", dispatch["jobs"]["private"])
        for retired in ("apple-test.yml", "apple-certification-smoke.yml"):
            self.assertFalse((WORKFLOWS / retired).exists())

    def test_private_forgejo_broker_release_is_the_only_arc_repository_local_exception(self) -> None:
        workflow = yaml.load(BROKER_RELEASE.read_text(encoding="utf-8"), Loader=ActionsLoader)
        self.assertEqual(_events(workflow), {"push", "workflow_dispatch"})
        self.assertEqual(workflow["on"]["push"]["tags"], ["ci-broker-*"])
        self.assertEqual(set(workflow["on"]["workflow_dispatch"]["inputs"]), {"release_tag"})
        self.assertEqual(set(workflow["jobs"]), {"admit", "image", "chart"})

        reason = "private Forgejo registry is reachable only from organization ARC capacity"
        self.assertEqual(
            _backend_contract()["repository_local_trusted_publication_exceptions"],
            [
                {
                    "workflow": ".github/workflows/ci-broker-image.yml",
                    "job": "admit",
                    "events": ["push", "workflow_dispatch"],
                    "tag_prefix": "ci-broker-",
                    "exact_selector": ["linux", "amd64", "general", "tiny"],
                    "reason": reason,
                },
                {
                    "workflow": ".github/workflows/ci-broker-image.yml",
                    "job": "image",
                    "events": ["push", "workflow_dispatch"],
                    "tag_prefix": "ci-broker-",
                    "exact_selector": ["linux", "amd64", "buildah", "small"],
                    "reason": reason,
                },
                {
                    "workflow": ".github/workflows/ci-broker-image.yml",
                    "job": "chart",
                    "events": ["push", "workflow_dispatch"],
                    "tag_prefix": "ci-broker-",
                    "exact_selector": ["linux", "amd64", "general", "small"],
                    "reason": reason,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
