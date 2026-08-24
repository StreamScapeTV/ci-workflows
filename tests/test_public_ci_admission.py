"""Regression coverage for public-repository Central self-CI admission."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.validation_harness import validate_repository
from ci_workflows.validation_helpers import _events, _iter_jobs
from ci_workflows.validation_model import load_actions_yaml
from fixture_builder import create_repository, write_json, write_text

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "contracts/repository-policy.json"
WORKFLOWS = ROOT / ".github/workflows"
OWNER = "mimranfaruqi"
OWNER_FRAGMENT = f"github.event.pull_request.user.login == '{OWNER}'"
REPOSITORY_FRAGMENT = "github.event.pull_request.head.repo.full_name == github.repository"


def _disabled(condition: object) -> bool:
    return "".join(str(condition).lower().split()) in {"false", "${{false}}"}


class PublicCiAdmissionRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(POLICY.read_text(encoding="utf-8"))
        cls.admission = payload["workflow_admission"]
        cls.records = cls.admission["workflows"]

    def test_policy_declares_public_repository_and_exact_owner(self) -> None:
        self.assertEqual("public", self.admission["repository_visibility"])
        self.assertEqual(OWNER, self.admission["exact_owner_login"])
        self.assertEqual("push", self.admission["preferred_self_ci_event"])
        self.assertIs(True, self.admission["external_contributor_approval_required"])

    def test_every_workflow_has_exact_reviewed_event_classification(self) -> None:
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in WORKFLOWS.glob("*.y*ml")
        }
        self.assertEqual(actual, set(self.records))
        for relative_path in sorted(actual):
            document = load_actions_yaml(ROOT / relative_path, ROOT)
            record = self.records[relative_path]
            self.assertEqual(set(record["allowed_events"]), _events(document), relative_path)
            self.assertTrue(record["trust_class"], relative_path)

    def test_reusable_and_internal_workflows_remain_call_only(self) -> None:
        for relative_path, record in self.records.items():
            name = Path(relative_path).name
            if name.startswith("reusable-"):
                self.assertEqual("reusable-call", record["trust_class"], relative_path)
                self.assertEqual(["workflow_call"], record["allowed_events"], relative_path)
            if name.startswith("internal-"):
                self.assertEqual("internal-call", record["trust_class"], relative_path)
                self.assertEqual(["workflow_call"], record["allowed_events"], relative_path)

    def test_every_retained_pr_job_is_exact_owner_and_same_repository_gated(self) -> None:
        for relative_path, record in self.records.items():
            if "pull_request" not in record["allowed_events"]:
                continue
            document = load_actions_yaml(ROOT / relative_path, ROOT)
            self.assertNotIn("github.event.repository.private", document.raw, relative_path)
            self.assertNotIn("author_association", document.raw, relative_path)
            for job_id, job in _iter_jobs(document):
                condition = job.get("if", "")
                if _disabled(condition):
                    continue
                normalized = " ".join(str(condition).split())
                self.assertIn(OWNER_FRAGMENT, normalized, f"{relative_path}:{job_id}")
                self.assertIn(REPOSITORY_FRAGMENT, normalized, f"{relative_path}:{job_id}")

    def test_tag_release_schedule_and_manual_classes_are_independent_of_prs(self) -> None:
        release = self.records[".github/workflows/runner-images-release.yml"]
        self.assertEqual("tag-release", release["trust_class"])
        self.assertEqual({"push", "workflow_dispatch"}, set(release["allowed_events"]))
        release_document = load_actions_yaml(WORKFLOWS / "runner-images-release.yml", ROOT)
        push = release_document.data["on"]["push"]
        self.assertEqual(["*", "!ci-broker-*"], push["tags"])

        broker_release = self.records[".github/workflows/ci-broker-image.yml"]
        self.assertEqual("tag-release", broker_release["trust_class"])
        self.assertEqual(
            {"push", "workflow_dispatch"},
            set(broker_release["allowed_events"]),
        )
        broker_document = load_actions_yaml(WORKFLOWS / "ci-broker-image.yml", ROOT)
        self.assertEqual(["ci-broker-*"], broker_document.data["on"]["push"]["tags"])
        self.assertEqual(
            {"release_tag"},
            set(broker_document.data["on"]["workflow_dispatch"]["inputs"]),
        )

        for relative_path in (
            ".github/workflows/apple-physical-device-lock-smoke.yml",
            ".github/workflows/service-runner-smoke.yml",
        ):
            self.assertEqual(["workflow_dispatch"], self.records[relative_path]["allowed_events"])


class PublicCiAdmissionNearMissTests(unittest.TestCase):
    def test_broad_same_repository_pr_gate_is_rejected_by_canonical_harness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_repository(root)
            broad_path = ".github/workflows/broad-pr.yml"
            write_text(
                root / broad_path,
                """name: Broad public PR smoke
on:
  pull_request:
permissions:
  contents: read
jobs:
  validate:
    name: Validate source
    if: ${{ github.event.pull_request.head.repo.full_name == github.repository }}
    runs-on: [linux, amd64, general, small]
    timeout-minutes: 10
    steps:
      - name: Validate without allocation authority
        shell: bash
        run: echo validation
""",
            )
            write_json(
                root / "contracts/repository-policy.json",
                {
                    "schema_version": 1,
                    "organization": "StreamScapeTV",
                    "workflow_admission": {
                        "repository_visibility": "public",
                        "exact_owner_login": OWNER,
                        "preferred_self_ci_event": "push",
                        "external_contributor_approval_required": True,
                        "workflows": {
                            ".github/workflows/reusable-sample.yml": {
                                "trust_class": "reusable-call",
                                "allowed_events": ["workflow_call"],
                            },
                            broad_path: {
                                "trust_class": "owner-pr-smoke",
                                "allowed_events": ["pull_request"],
                            },
                        },
                    },
                },
            )
            rules = {
                finding.rule
                for finding in validate_repository(
                    root, include_public_api_validator=False
                ).findings
            }
            self.assertIn("public-ci-pr-runner-admission", rules)

    def test_author_association_near_miss_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_repository(root)
            broad_path = ".github/workflows/association-pr.yml"
            write_text(
                root / broad_path,
                f"""name: Association public PR smoke
on:
  pull_request:
permissions:
  contents: read
jobs:
  validate:
    name: Validate source
    if: ${{{{ github.event.pull_request.author_association == 'MEMBER' && github.event.pull_request.user.login == '{OWNER}' && github.event.pull_request.head.repo.full_name == github.repository }}}}
    runs-on: [linux, amd64, general, small]
    timeout-minutes: 10
    steps:
      - name: Validate association near miss
        shell: bash
        run: echo validation
""",
            )
            write_json(
                root / "contracts/repository-policy.json",
                {
                    "schema_version": 1,
                    "organization": "StreamScapeTV",
                    "workflow_admission": {
                        "repository_visibility": "public",
                        "exact_owner_login": OWNER,
                        "preferred_self_ci_event": "push",
                        "external_contributor_approval_required": True,
                        "workflows": {
                            ".github/workflows/reusable-sample.yml": {
                                "trust_class": "reusable-call",
                                "allowed_events": ["workflow_call"],
                            },
                            broad_path: {
                                "trust_class": "owner-pr-smoke",
                                "allowed_events": ["pull_request"],
                            },
                        },
                    },
                },
            )
            rules = {
                finding.rule
                for finding in validate_repository(
                    root, include_public_api_validator=False
                ).findings
            }
            self.assertIn("broad-public-ci-pr-trust", rules)


if __name__ == "__main__":
    unittest.main()
