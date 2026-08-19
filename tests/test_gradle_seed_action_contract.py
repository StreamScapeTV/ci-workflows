from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import ciw_gradle_seed, gradle_seed, gradle_seed_internal
from ci_workflows.ciw_types import CIWContext, CIWError

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions" / "upload-gradle-seed" / "action.yml"
INTERNAL_MODULE = ROOT / "src" / "ci_workflows" / "gradle_seed_internal.py"
CLI = ROOT / "scripts" / "ci" / "ciw.py"


class GradleSeedActionContractTests(unittest.TestCase):
    def test_action_is_thin_internal_sync_delegate_without_cleanup_or_oidc(self) -> None:
        source = ACTION.read_text(encoding="utf-8")
        input_block = source.split("inputs:\n", 1)[1].split("\noutputs:\n", 1)[0]
        self.assertIn("  source_sha:\n", input_block)
        self.assertEqual(
            1,
            sum(
                1
                for line in input_block.splitlines()
                if line.startswith("  ")
                and not line.startswith("    ")
                and line.endswith(":")
            ),
        )
        for forbidden in (
            "permissions:",
            "id-token: write",
            "ACTIONS_ID_TOKEN",
            "authorization",
            "workspace cleanup",
            "if: always()",
            "endpoint:",
            "audience:",
            "token:",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('scripts/ci/ciw.py" gradle-seed upload', source)
        wrapper = CLI.read_text(encoding="utf-8")
        self.assertIn("from ci_workflows.ciw import main", wrapper)

    def test_fixed_transport_is_cluster_local_and_has_no_github_identity_dependency(self) -> None:
        self.assertEqual(
            "arc-gradle-seed-promoter.github-actions-runners.svc.cluster.local",
            gradle_seed_internal.FLUX_HOST,
        )
        self.assertEqual(8080, gradle_seed_internal.FLUX_PORT)
        self.assertEqual("/v1/gradle-seed", gradle_seed_internal.FLUX_PATH)
        self.assertEqual(
            "application/vnd.faruqi.gradle-seed-v1",
            gradle_seed_internal.CONTENT_TYPE,
        )
        self.assertEqual(4 * 1024 * 1024 * 1024, gradle_seed.MAX_UPLOAD_BYTES)
        combined = ACTION.read_text(encoding="utf-8") + "\n" + INTERNAL_MODULE.read_text(encoding="utf-8")
        lowered = combined.lower()
        for forbidden in (
            "actions_id_token_request_url",
            "actions_id_token_request_token",
            "openid",
            "jwks",
            "bearer",
            "github_token",
            "personal_access_token",
            "deploy_key",
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "s3://",
            "ghcr.io",
        ):
            self.assertNotIn(forbidden, lowered)
        for product_policy in (
            "StreamScapeTV/iptv-android",
            "1310373430",
            "refs/heads/develop",
        ):
            self.assertNotIn(product_policy, combined)

    def test_action_outputs_only_bounded_sync_evidence(self) -> None:
        source = ACTION.read_text(encoding="utf-8")
        output_block = source.split("outputs:\n", 1)[1].split("\nruns:\n", 1)[0]
        names = {
            line.strip().removesuffix(":")
            for line in output_block.splitlines()
            if line.startswith("  ")
            and not line.startswith("    ")
            and line.endswith(":")
        }
        self.assertEqual(
            {"result", "source_sha", "generation", "file_count", "total_bytes", "evidence_id"},
            names,
        )
        self.assertNotIn("cleanup", output_block.lower())

    def test_cli_adapter_requires_registered_gradle_workspace_home(self) -> None:
        state_id = "workspace-test"
        root = f"/tmp/ci-workflows-state/{state_id}"
        environment = {
            "GITHUB_OUTPUT": "/tmp/output",
            "INPUT_SOURCE_SHA": "a" * 40,
            "CI_WORKFLOW_STATE_ID": state_id,
            "CI_WORKFLOW_ROOT": root,
            "GRADLE_USER_HOME": f"{root}/gradle",
        }
        context = CIWContext(
            root=ROOT,
            environment=environment,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        result = mock.Mock()
        result.output_values.return_value = {"result": "promoted"}
        with mock.patch.object(
            ciw_gradle_seed,
            "sync_gradle_seed",
            return_value=result,
        ) as sync:
            projected = ciw_gradle_seed.execute_gradle_seed_upload(
                argparse.Namespace(source_sha=None),
                context,
            )
        self.assertEqual("promoted", projected.outputs["result"])
        sync.assert_called_once()
        self.assertEqual("a" * 40, sync.call_args.kwargs["source_sha"])
        self.assertIs(environment, sync.call_args.kwargs["environment"])
        self.assertTrue(callable(sync.call_args.kwargs["report_selection"]))

        bad_environments = (
            {**environment, "CI_WORKFLOW_ROOT": ""},
            {**environment, "GRADLE_USER_HOME": "/tmp/other"},
            {**environment, "CI_WORKFLOW_ROOT": "relative-root"},
            {**environment, "CI_WORKFLOW_STATE_ID": "other-state"},
        )
        for bad_environment in bad_environments:
            with self.subTest(environment=bad_environment):
                bad_context = CIWContext(
                    root=ROOT,
                    environment=bad_environment,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
                with self.assertRaises(CIWError) as raised:
                    ciw_gradle_seed.execute_gradle_seed_upload(
                        argparse.Namespace(source_sha=None),
                        bad_context,
                    )
                self.assertEqual("gradle_seed_home_rejected", raised.exception.code)

    def test_cli_main_emits_bounded_outputs_and_projects_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "output"
            state_id = "workspace-test"
            root = f"{directory}/{state_id}"
            environment = {
                "GITHUB_OUTPUT": str(output_path),
                "INPUT_SOURCE_SHA": "a" * 40,
                "CI_WORKFLOW_STATE_ID": state_id,
                "CI_WORKFLOW_ROOT": root,
                "GRADLE_USER_HOME": f"{root}/gradle",
            }
            result = mock.Mock()
            result.output_values.return_value = {"result": "promoted", "source_sha": "a" * 40}
            with mock.patch.object(ciw_gradle_seed, "sync_gradle_seed", return_value=result):
                self.assertEqual(
                    0,
                    ciw_gradle_seed.main(
                        ["--root", str(ROOT)],
                        environment=environment,
                        stdout=io.StringIO(),
                        stderr=io.StringIO(),
                    ),
                )
            emitted = output_path.read_text(encoding="utf-8")
            self.assertIn("result=promoted", emitted)
            self.assertIn("source_sha=" + "a" * 40, emitted)

            errors = io.StringIO()
            self.assertNotEqual(
                0,
                ciw_gradle_seed.main(
                    ["--root", str(ROOT)],
                    environment={**environment, "GITHUB_OUTPUT": ""},
                    stdout=io.StringIO(),
                    stderr=errors,
                ),
            )
            self.assertIn("github_output_missing", errors.getvalue())
            self.assertNotIn(directory, errors.getvalue())


if __name__ == "__main__":
    unittest.main()
