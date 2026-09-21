from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "github_issue_dependency_projection.py"
spec = importlib.util.spec_from_file_location("github_issue_dependency_projection", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FakeGitHub:
    def __init__(self) -> None:
        self.ids: dict[mod.IssueIdentity, int] = {}
        self.blockers: dict[mod.IssueIdentity, set[int]] = {}
        self.added: list[tuple[mod.IssueIdentity, int, bool]] = []
        self.removed: list[tuple[mod.IssueIdentity, int, bool]] = []
        self.fail_add: mod.ProjectionError | None = None
        self.keep_after_add = False

    def resolve_open_issue(self, identity: mod.IssueIdentity) -> int:
        if identity not in self.ids:
            raise mod.ProjectionError("not_found", retryable=False)
        return self.ids[identity]

    def blocked_by_ids(self, dependent: mod.IssueIdentity) -> set[int]:
        return set(self.blockers.get(dependent, set()))

    def add_blocker(self, dependent: mod.IssueIdentity, blocker_id: int, *, cross_repository: bool) -> None:
        if self.fail_add is not None:
            raise self.fail_add
        self.added.append((dependent, blocker_id, cross_repository))
        if not self.keep_after_add:
            self.blockers.setdefault(dependent, set()).add(blocker_id)

    def remove_blocker(self, dependent: mod.IssueIdentity, blocker_id: int, *, cross_repository: bool) -> None:
        self.removed.append((dependent, blocker_id, cross_repository))
        self.blockers.setdefault(dependent, set()).discard(blocker_id)


def projection(
    operation: str = "add",
    *,
    dependent_repository: str = "StreamScapeTV/a",
    blocker_repository: str = "StreamScapeTV/a",
) -> mod.Projection:
    return mod.Projection(
        projection_id="11111111-1111-4111-8111-111111111111",
        claim_token="22222222-2222-4222-8222-222222222222",
        operation=operation,
        dependent=mod.IssueIdentity("a", 10, dependent_repository),
        blocker=mod.IssueIdentity("b", 20, blocker_repository),
    )


class ProjectionBehaviorTests(unittest.TestCase):
    def test_same_repository_add_and_replay_are_idempotent(self) -> None:
        item = projection()
        github = FakeGitHub()
        github.ids[item.dependent] = 101
        github.ids[item.blocker] = 202
        self.assertEqual(mod.execute_projection(item, github), {"outcome": "succeeded"})
        self.assertEqual(github.added, [(item.dependent, 202, False)])

        github.added.clear()
        self.assertEqual(mod.execute_projection(item, github), {"outcome": "succeeded"})
        self.assertEqual(github.added, [])

    def test_cross_repository_remove_is_bounded_to_claimed_pair(self) -> None:
        item = projection(
            "remove",
            dependent_repository="StreamScapeTV/dependent",
            blocker_repository="StreamScapeTV/blocker",
        )
        github = FakeGitHub()
        github.ids[item.dependent] = 303
        github.ids[item.blocker] = 404
        github.blockers[item.dependent] = {404}
        self.assertEqual(mod.execute_projection(item, github), {"outcome": "succeeded"})
        self.assertEqual(github.removed, [(item.dependent, 404, True)])
        self.assertEqual(github.blockers[item.dependent], set())

    def test_missing_identity_fails_without_mutation(self) -> None:
        item = projection()
        github = FakeGitHub()
        github.ids[item.dependent] = 101
        self.assertEqual(
            mod.execute_projection(item, github),
            {"outcome": "failed", "error_code": "not_found"},
        )
        self.assertEqual(github.added, [])

    def test_permission_failure_is_terminal_and_readback_mismatch_retries(self) -> None:
        item = projection()
        github = FakeGitHub()
        github.ids[item.dependent] = 101
        github.ids[item.blocker] = 202
        github.fail_add = mod.ProjectionError("permission_denied", retryable=False)
        self.assertEqual(
            mod.execute_projection(item, github),
            {"outcome": "failed", "error_code": "permission_denied"},
        )

        github.fail_add = None
        github.keep_after_add = True
        self.assertEqual(
            mod.execute_projection(item, github),
            {"outcome": "retry", "error_code": "readback_mismatch"},
        )

    def test_claim_parser_accepts_only_fixed_streamscape_projection_shape(self) -> None:
        raw = {
            "projection_id": "11111111-1111-4111-8111-111111111111",
            "claim_token": "22222222-2222-4222-8222-222222222222",
            "operation": "add",
            "desired_active": True,
            "status": "claimed",
            "attempt_count": 1,
            "max_attempts": 8,
            "dependent": {
                "project_key": "one",
                "issue_number": 1,
                "repository_full_name": "StreamScapeTV/one",
            },
            "blocker": {
                "project_key": "two",
                "issue_number": 2,
                "repository_full_name": "StreamScapeTV/two",
            },
        }
        parsed = mod.parse_projection(raw)
        self.assertEqual(parsed.dependent.repository_full_name, "StreamScapeTV/one")
        self.assertEqual(parsed.blocker.repository_full_name, "StreamScapeTV/two")
        raw["dependent"]["repository_full_name"] = "outside/example"
        with self.assertRaises(mod.ProjectionError):
            mod.parse_projection(raw)

        raw["dependent"]["repository_full_name"] = "StreamScapeTV/one"
        raw["operation"] = []
        with self.assertRaises(mod.ProjectionError):
            mod.parse_projection(raw)


class ProjectionSourceContractTests(unittest.TestCase):
    def test_workflow_has_no_mutation_inputs_and_uses_fixed_issues_permission(self) -> None:
        path = ROOT / ".github" / "workflows" / "github-issue-dependency-projection.yml"
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["on"]["workflow_dispatch"], None)
        self.assertEqual(workflow["on"]["schedule"][0]["cron"], "*/5 * * * *")
        steps = {step.get("name"): step for step in workflow["jobs"]["project"]["steps"]}
        token = steps["Prepare fixed Issues-write token"]
        self.assertEqual(token["uses"], "actions/create-github-app-token@v2")
        self.assertEqual(token["with"]["owner"], "StreamScapeTV")
        self.assertEqual(token["with"]["permission-issues"], "write")
        self.assertNotIn("repositories", token["with"])
        self.assertNotIn("permission-contents", token["with"])
        self.assertNotIn("inputs", workflow["on"]["workflow_dispatch"] or {})

    def test_script_exposes_only_claim_and_apply_commands_and_fixed_native_endpoints(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("claim_github_issue_dependency_projection", text)
        self.assertIn("finish_github_issue_dependency_projection", text)
        self.assertIn("/dependencies/blocked_by", text)
        self.assertIn('payload={"issue_id": blocker_id}', text)
        self.assertIn('"X-GitHub-Api-Version": "2026-03-10"', text)
        self.assertIn('argv == ["claim"]', text)
        self.assertIn('argv == ["apply"]', text)
        self.assertNotIn("argparse", text)
        self.assertNotIn("labels", text)
        self.assertNotIn("milestones", text)
        self.assertNotIn("assignees", text)

    def test_inventory_and_self_check_track_worker(self) -> None:
        inventory = (ROOT / "INVENTORY.yaml").read_text(encoding="utf-8")
        self.assertIn(
            "github_issue_dependency_projection: .github/workflows/github-issue-dependency-projection.yml",
            inventory,
        )
        self.assertIn(
            "github_issue_dependency_projection: scripts/ci/github_issue_dependency_projection.py",
            inventory,
        )
        self_check = (ROOT / ".github/workflows/self-check.yml").read_text(encoding="utf-8")
        self.assertIn("tests.test_github_issue_dependency_projection", self_check)


if __name__ == "__main__":
    unittest.main()
