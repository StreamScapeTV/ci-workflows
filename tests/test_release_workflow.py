from __future__ import annotations

import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-release.yml"


def load_workflow():
    payload = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("workflow root must be a mapping")
    if "on" not in payload and True in payload:
        payload["on"] = payload[True]
    return payload


class ReleaseWorkflowContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = load_workflow()
        self.jobs = self.workflow["jobs"]
        self.text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_workflow_call_matches_registered_public_contract(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            {
                "admitted_sha",
                "release_contract",
                "release_tag",
                "release_version",
                "request_id",
                "target_id",
            },
            set(call["inputs"]),
        )
        self.assertEqual(
            {"registry_username", "registry_token", "flux_handoff_token"},
            set(call["secrets"]),
        )
        self.assertEqual(
            {
                "result",
                "immutable_references_json",
                "release_manifest_sha256",
                "handoff_state",
                "request_id",
            },
            set(call["outputs"]),
        )
        self.assertEqual(
            {"actions": "read", "contents": "write"},
            self.workflow["permissions"],
        )
        self.assertNotIn("concurrency", self.workflow)
        self.assertNotIn("secrets: inherit", self.text)

    def test_public_graph_is_bounded_and_has_stable_terminal_check(self) -> None:
        self.assertLessEqual(len(self.jobs), 7)
        self.assertEqual(6, len(self.jobs))
        self.assertEqual(
            "Release / Verified products",
            self.jobs["request_handoff_and_finalize"]["name"],
        )
        for job_name, job in self.jobs.items():
            self.assertRegex(job_name, r"^[a-z][a-z0-9_]{1,63}$")
            self.assertGreater(int(job.get("timeout-minutes", 0)), 0, msg=job_name)
            self.assertNotIn("uses", job, msg=f"nested reusable workflow: {job_name}")

    def test_dependency_composition_uses_reviewed_actions_and_scripts_directly(self) -> None:
        image = json.dumps(self.jobs["publish_images"], sort_keys=True)
        chart = json.dumps(self.jobs["publish_charts_or_assets"], sort_keys=True)
        self.assertIn("./.ciw/actions/publish-oci", image)
        self.assertIn("./.ciw/actions/validate-oci", image)
        self.assertIn("helm_release.py execute", chart)
        self.assertIn("ci_workflows.ciw_helm", chart)
        self.assertNotIn("reusable-oci-publish.yml", self.text)
        self.assertNotIn("reusable-helm-publish.yml", self.text)

    def test_helm_image_evidence_is_gated_by_checked_in_binding_flag(self) -> None:
        self.assertEqual(
            "${{ steps.release.outputs.chart_requires_image_identity }}",
            self.jobs["plan"]["outputs"]["chart_requires_image_identity"],
        )
        helm = next(
            step
            for step in self.jobs["publish_charts_or_assets"]["steps"]
            if step.get("id") == "helm"
        )
        image_digest = str(helm["env"]["INPUT_IMAGE_DIGEST"])
        immutable = str(helm["env"]["INPUT_IMMUTABLE_REFERENCES_JSON"])
        for value in (image_digest, immutable):
            self.assertIn("chart_requires_image_identity == 'true'", value)
            self.assertIn("|| ''", value)
        self.assertIn("needs.publish_images.outputs.image_digest", image_digest)
        self.assertIn(
            "needs.publish_images.outputs.immutable_references_json", immutable
        )

    def test_tag_push_and_trusted_replay_share_one_resolved_authority_tuple(self) -> None:
        plan = self.jobs["plan"]
        steps = {step.get("id"): step for step in plan["steps"] if step.get("id")}
        tag = steps["authority_tag"]
        replay = steps["authority_replay"]
        bind = steps["authority"]

        self.assertEqual("${{ github.ref_type == 'tag' }}", str(tag["if"]))
        self.assertEqual("tag-push", tag["with"]["release_mode"])
        self.assertNotIn("release_version", tag["with"])
        self.assertNotIn("release_source_sha", tag["with"])

        self.assertEqual("${{ github.ref_type != 'tag' }}", str(replay["if"]))
        self.assertEqual("existing-tag", replay["with"]["release_mode"])
        self.assertEqual(
            "${{ steps.release.outputs.release_version }}",
            replay["with"]["release_version"],
        )
        self.assertEqual(
            "${{ steps.release.outputs.admitted_sha }}",
            replay["with"]["release_source_sha"],
        )

        rendered = json.dumps(bind, sort_keys=True)
        self.assertIn('test "${REQUESTED_TAG}" = "${version}"', rendered)
        self.assertIn('test "${REQUESTED_VERSION}" = "${version}"', rendered)
        self.assertIn('test "${REQUESTED_SHA}" = "${source_sha}"', rendered)
        self.assertIn('test "${REQUESTED_SHA}" = "${commit_sha}"', rendered)
        self.assertEqual(
            "${{ steps.authority.outputs.release_mode }}",
            plan["outputs"]["release_mode"],
        )

        for job_name in (
            "run_release_gates",
            "publish_images",
            "publish_charts_or_assets",
            "verify_and_record",
        ):
            revalidations = [
                step
                for step in self.jobs[job_name]["steps"]
                if "resolve-release-tag" in str(step.get("uses", ""))
            ]
            self.assertEqual(1, len(revalidations), msg=job_name)
            self.assertEqual(
                "${{ needs.plan.outputs.release_mode }}",
                revalidations[0]["with"]["release_mode"],
                msg=job_name,
            )

    def test_publication_jobs_use_only_trusted_planner_runner_output(self) -> None:
        expected = "${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
        self.assertEqual(expected, self.jobs["publish_images"]["runs-on"])
        self.assertEqual(expected, self.jobs["publish_charts_or_assets"]["runs-on"])
        for job_name in (
            "plan",
            "run_release_gates",
            "verify_and_record",
            "request_handoff_and_finalize",
        ):
            self.assertEqual(
                ["linux", "amd64", "general"],
                self.jobs[job_name]["runs-on"],
            )

    def test_registry_and_flux_secrets_are_isolated(self) -> None:
        image = json.dumps(self.jobs["publish_images"], sort_keys=True)
        chart = json.dumps(self.jobs["publish_charts_or_assets"], sort_keys=True)
        verify = json.dumps(self.jobs["verify_and_record"], sort_keys=True)
        final = json.dumps(self.jobs["request_handoff_and_finalize"], sort_keys=True)

        self.assertIn("secrets.registry_username", image)
        self.assertIn("secrets.registry_token", image)
        self.assertIn("secrets.registry_username", chart)
        self.assertIn("secrets.registry_token", chart)
        self.assertNotIn("flux_handoff_token", image)
        self.assertNotIn("flux_handoff_token", chart)
        self.assertNotIn("registry_username", verify)
        self.assertNotIn("registry_token", verify)
        self.assertIn("github.token", verify)
        self.assertIn("secrets.flux_handoff_token", final)
        self.assertNotIn("secrets.registry_username", final)
        self.assertNotIn("secrets.registry_token", final)

    def test_publication_cleanup_and_residue_checks_are_unconditional(self) -> None:
        for job_name, step_ids in {
            "publish_images": {
                "publication_cleanup",
                "publication_residue",
                "build_cleanup",
                "build_residue",
                "workspace_cleanup",
            },
            "publish_charts_or_assets": {
                "helm_cleanup",
                "helm_residue",
                "workspace_cleanup",
            },
        }.items():
            steps = {
                step.get("id"): step
                for step in self.jobs[job_name]["steps"]
                if step.get("id")
            }
            for step_id in step_ids:
                self.assertIn(step_id, steps)
                self.assertEqual("always()", str(steps[step_id]["if"]))

    def test_tag_authority_is_revalidated_at_every_privileged_boundary(self) -> None:
        for job_name in (
            "run_release_gates",
            "publish_images",
            "publish_charts_or_assets",
            "verify_and_record",
        ):
            rendered = json.dumps(self.jobs[job_name], sort_keys=True)
            self.assertIn("resolve-release-tag", rendered, msg=job_name)
            self.assertIn("revalidate", rendered, msg=job_name)
            self.assertIn("tag_object_sha", rendered, msg=job_name)
            self.assertIn("tag_commit_sha", rendered, msg=job_name)

    def test_handoff_is_review_only_and_never_contains_cluster_authority(self) -> None:
        rendered = json.dumps(
            self.jobs["request_handoff_and_finalize"], sort_keys=True
        ).casefold()
        self.assertIn("dispatch-handoff", rendered)
        self.assertIn("flux_handoff_token", rendered)
        self.assertNotIn("kubeconfig", rendered)
        self.assertNotIn("kubectl", rendered)
        self.assertNotIn("flux reconcile", rendered)
        self.assertNotIn("sops", rendered)

    def test_workflow_never_uses_routine_actions_artifacts_or_latest(self) -> None:
        lowered = self.text.casefold()
        self.assertNotIn("actions/upload-artifact", lowered)
        self.assertNotIn("actions/download-artifact", lowered)
        self.assertNotIn(":latest", lowered)
        self.assertNotIn("secrets: inherit", lowered)

    def test_terminal_finalizer_runs_on_every_path_and_fails_closed(self) -> None:
        final = self.jobs["request_handoff_and_finalize"]
        self.assertIn("always()", str(final["if"]))
        self.assertEqual(
            {
                "plan",
                "publish_images",
                "publish_charts_or_assets",
                "verify_and_record",
            },
            set(final["needs"]),
        )
        rendered = json.dumps(final, sort_keys=True)
        self.assertIn("result=failure", rendered)
        self.assertIn('test "${result}" = success', rendered)

    def test_workflow_has_no_product_name_branching(self) -> None:
        forbidden = ("agent-state", "iptv-backend", "flux-runner-assets")
        for job_name, job in self.jobs.items():
            condition = str(job.get("if", "")).casefold()
            for product in forbidden:
                self.assertNotIn(product, condition, msg=f"{job_name}: {product}")


if __name__ == "__main__":
    unittest.main()
