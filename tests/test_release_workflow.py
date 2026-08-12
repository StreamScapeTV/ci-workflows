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
    # PyYAML 1.1 may decode the GitHub Actions `on` key as boolean True.
    if "on" not in payload and True in payload:
        payload["on"] = payload[True]
    return payload


class ReleaseWorkflowContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = load_workflow()
        self.jobs = self.workflow["jobs"]
        self.text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_workflow_call_is_generic_and_has_only_explicit_named_secrets(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            {
                "release_id",
                "release_mode",
                "release_version",
                "release_source_sha",
            },
            set(call["inputs"]),
        )
        self.assertEqual(
            {"registry_username", "registry_token", "github_release_token"},
            set(call["secrets"]),
        )
        self.assertNotIn("secrets: inherit", self.text)
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])

    def test_publication_jobs_consume_only_local_registered_dependency_workflows(self) -> None:
        image = self.jobs["publish-images"]
        chart = self.jobs["publish-charts-or-assets"]
        self.assertEqual(
            "./.github/workflows/reusable-oci-publish.yml",
            image["uses"],
        )
        self.assertEqual(
            "./.github/workflows/reusable-helm-publish.yml",
            chart["uses"],
        )
        self.assertEqual(
            {"registry_username", "registry_token"},
            set(image["secrets"]),
        )
        self.assertEqual(
            {"registry_username", "registry_token"},
            set(chart["secrets"]),
        )
        self.assertIn(
            "bind-published-images.outputs.required_image_references_json",
            str(chart["with"]["required_image_references_json"]),
        )
        self.assertNotIn("github_release_token", json.dumps(image))
        self.assertNotIn("github_release_token", json.dumps(chart))

    def test_image_binding_is_read_only_and_uses_registered_oci_outputs(self) -> None:
        bind = self.jobs["bind-published-images"]
        rendered = json.dumps(bind, sort_keys=True)
        self.assertIn("publish-images.outputs.image_digest", rendered)
        self.assertIn("publish-images.outputs.immutable_references_json", rendered)
        self.assertIn("required_image_references_json", bind["outputs"])
        self.assertNotIn("secrets.", rendered)
        self.assertEqual(["linux", "amd64", "general"], bind["runs-on"])

    def test_github_release_token_is_isolated_to_release_metadata_job(self) -> None:
        metadata = self.jobs["create-or-verify-github-release"]
        self.assertEqual({"contents": "write"}, metadata["permissions"])
        rendered = json.dumps(metadata, sort_keys=True)
        self.assertIn("secrets.github_release_token", rendered)
        self.assertNotIn("secrets.registry_username", rendered)
        self.assertNotIn("secrets.registry_token", rendered)

        for job_name, job in self.jobs.items():
            if job_name == "create-or-verify-github-release":
                continue
            self.assertNotIn(
                "secrets.github_release_token",
                json.dumps(job, sort_keys=True),
                msg=job_name,
            )

    def test_handoff_is_review_only_and_has_no_flux_or_cluster_credentials(self) -> None:
        handoff = json.dumps(self.jobs["request-reviewed-handoff"], sort_keys=True)
        self.assertNotIn("secrets.", handoff)
        self.assertNotIn("kubeconfig", handoff.casefold())
        self.assertNotIn("kubectl", handoff.casefold())
        self.assertNotIn("flux reconcile", handoff.casefold())
        self.assertNotIn("sops", handoff.casefold())
        self.assertNotIn("dispatch", handoff.casefold())

    def test_workflow_never_uploads_routine_actions_artifacts_or_uses_latest(self) -> None:
        lowered = self.text.casefold()
        self.assertNotIn("actions/upload-artifact", lowered)
        self.assertNotIn("actions/download-artifact", lowered)
        self.assertNotIn(":latest", lowered)
        self.assertNotIn("secrets: inherit", lowered)

    def test_tag_authority_is_revalidated_before_release_metadata_write(self) -> None:
        gates = json.dumps(self.jobs["run-release-gates"], sort_keys=True)
        metadata = json.dumps(
            self.jobs["create-or-verify-github-release"], sort_keys=True
        )
        self.assertIn("resolve-release-tag", gates)
        self.assertIn("phase", gates)
        self.assertIn("revalidate", gates)
        self.assertIn("resolve-release-tag", metadata)
        self.assertIn("revalidate", metadata)

    def test_all_control_plane_jobs_use_semantic_general_runner(self) -> None:
        reusable_call_jobs = {"publish-images", "publish-charts-or-assets"}
        for job_name, job in self.jobs.items():
            if job_name in reusable_call_jobs:
                continue
            self.assertEqual(
                ["linux", "amd64", "general"],
                job.get("runs-on"),
                msg=job_name,
            )
            self.assertGreater(int(job.get("timeout-minutes", 0)), 0, msg=job_name)

    def test_terminal_finalizer_runs_on_every_path_and_fails_closed(self) -> None:
        final = self.jobs["cleanup-and-finalize"]
        self.assertIn("always()", str(final["if"]))
        self.assertEqual(
            {
                "resolve-tag-source",
                "validate-release-contract",
                "run-release-gates",
                "publish-images",
                "bind-published-images",
                "publish-charts-or-assets",
                "verify-read-back",
                "create-release-manifest",
                "create-or-verify-github-release",
                "request-reviewed-handoff",
            },
            set(final["needs"]),
        )
        rendered = json.dumps(final, sort_keys=True)
        self.assertIn("publication_progress", rendered)
        self.assertIn("result=failure", rendered)
        self.assertIn("exit 1", rendered)

    def test_workflow_has_no_product_name_branching(self) -> None:
        forbidden = ("agent-state", "iptv-backend", "flux-runner-assets")
        for job_name, job in self.jobs.items():
            condition = str(job.get("if", "")).casefold()
            for product in forbidden:
                self.assertNotIn(product, condition, msg=f"{job_name}: {product}")


if __name__ == "__main__":
    unittest.main()
