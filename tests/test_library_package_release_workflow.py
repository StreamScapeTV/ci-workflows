from __future__ import annotations

from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/library-package-release.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
REPOSITORY = ROOT / ".github/workflows/repository.yml"
SWIFTPM = ROOT / ".github/workflows/apple-swiftpm.yml"
MAVEN = ROOT / ".github/workflows/maven.yml"


class LibraryPackageReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.dispatch = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))

    def test_aggregate_api_is_one_tag_transaction_without_product_identity(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(set(call["inputs"]), {"repository", "ref", "source_is_tag", "ci_run_id"})
        text = WORKFLOW.read_text(encoding="utf-8").lower()
        self.assertNotIn("streamscape-media", text)
        self.assertNotIn("com.streamscape", text)
        self.assertNotIn("workflow_path", text)
        self.assertNotIn("runner_label", text)
        self.assertNotIn("script_path", text)

    def test_plan_owns_agent_state_and_observes_one_exact_plain_semver_tag_source(self) -> None:
        plan = self.workflow["jobs"]["plan"]
        names = [step.get("name") for step in plan["steps"]]
        self.assertLess(names.index("Mark Agent State run as running"), names.index("Check out exact tagged source"))
        self.assertLess(names.index("Resolve exact tagged source identity"), names.index("Record observed source SHA"))
        validate = next(step for step in plan["steps"] if step.get("name") == "Validate aggregate package release request")["run"]
        self.assertIn("plain semantic-version tag", validate)
        self.assertIn("refs/tags/${SOURCE_REF}", validate)
        checkout = next(step for step in plan["steps"] if step.get("name") == "Check out exact tagged source")
        self.assertEqual(checkout["with"]["ref"], "refs/tags/${{ inputs.ref }}")
        self.assertEqual(plan["outputs"]["source_sha"], "${{ steps.source_identity.outputs.source_sha }}")

    def test_shared_readiness_and_release_prepare_gate_both_publications(self) -> None:
        jobs = self.workflow["jobs"]
        for name, host in (("readiness_macos", "macos"), ("readiness_linux", "linux")):
            job = jobs[name]
            self.assertEqual(job["uses"], "./.github/workflows/repository.yml")
            self.assertEqual(job["with"]["operation"], "full")
            self.assertEqual(job["with"]["host_os"], host)
            self.assertEqual(job["with"]["semantic_inputs_json"], "{}")
            self.assertEqual(job["with"]["ci_run_id"], "")
            self.assertEqual(job["with"]["expected_source_sha"], "${{ needs.plan.outputs.source_sha }}")
        prepare = jobs["release_prepare"]
        self.assertEqual(set(prepare["needs"]), {"plan", "readiness_macos", "readiness_linux"})
        self.assertEqual(prepare["with"]["operation"], "release")
        self.assertEqual(prepare["with"]["host_os"], "macos")
        self.assertTrue(prepare["with"]["release_authorized"])
        self.assertIn('"release_kind":"prepare"', prepare["with"]["semantic_inputs_json"])
        self.assertIn("inputs.ref", prepare["with"]["semantic_inputs_json"])
        apple = jobs["apple_swiftpm"]
        self.assertIn("release_prepare", apple["needs"])
        self.assertEqual(apple["with"]["ci_run_id"], "")
        self.assertEqual(apple["with"]["expected_source_sha"], "${{ needs.plan.outputs.source_sha }}")
        maven = jobs["maven"]
        self.assertIn("apple_swiftpm", maven["needs"])
        self.assertEqual(maven["with"]["build_number"], "${{ inputs.ref }}")
        self.assertEqual(maven["with"]["ci_run_id"], "")
        self.assertEqual(maven["with"]["expected_source_sha"], "${{ needs.plan.outputs.source_sha }}")

    def test_only_aggregate_finishes_outer_agent_state_row(self) -> None:
        finish = self.workflow["jobs"]["finish"]
        self.assertEqual(finish["if"], "${{ always() }}")
        status = finish["steps"][0]["with"]["status"]
        for name in ("plan", "readiness_macos", "readiness_linux", "release_prepare", "apple_swiftpm", "maven"):
            self.assertIn(f"needs.{name}.result == 'success'", status)
        self.assertEqual(finish["steps"][0]["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")

    def test_children_fence_to_aggregate_source_before_execution_or_publication(self) -> None:
        for path in (REPOSITORY, SWIFTPM, MAVEN):
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIn("expected_source_sha", workflow["on"]["workflow_call"]["inputs"])
            text = path.read_text(encoding="utf-8")
            self.assertIn("EXPECTED_SOURCE_SHA", text)
            self.assertIn('test "${source_sha}" = "${EXPECTED_SOURCE_SHA}"', text)

    def test_dispatch_exposes_one_stable_inputless_aggregate_route(self) -> None:
        jobs = self.dispatch["jobs"]
        aggregate = jobs["library_package_release"]
        self.assertEqual(aggregate["uses"], "./.github/workflows/library-package-release.yml")
        self.assertFalse(aggregate["concurrency"]["cancel-in-progress"])
        text = DISPATCH.read_text(encoding="utf-8")
        self.assertIn("release.library-package supports only the publish profile", text)
        self.assertIn("release.library-package requires one plain semantic-version tag", text)
        self.assertIn("steps.claim.outputs.workflow_key == 'release.library-package'", text)
        settle = jobs["settle_cancelled"]
        self.assertIn("library_package_release", settle["needs"])
        self.assertIn("needs.library_package_release.result == 'cancelled'", settle["if"])

    def test_existing_manual_publication_routes_still_own_their_rows(self) -> None:
        jobs = self.dispatch["jobs"]
        self.assertEqual(jobs["apple_swiftpm"]["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        self.assertEqual(jobs["maven"]["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")


if __name__ == "__main__":
    unittest.main()
