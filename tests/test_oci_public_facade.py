from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from ci_workflows import ciw, oci

ROOT = Path(__file__).resolve().parents[1]


class OciPublicFacadeTests(unittest.TestCase):
    def test_resolved_input_evidence_is_projected_through_every_public_facade(self) -> None:
        action = (ROOT / "actions/validate-oci/action.yml").read_text(encoding="utf-8")
        action_outputs = action.split("outputs:\n", 1)[1].split("\nruns:\n", 1)[0]
        self.assertIn("resolved_inputs_json:", action_outputs)

        workflow = (
            ROOT / ".github/workflows/reusable-oci-build.yml"
        ).read_text(encoding="utf-8")
        public_outputs = workflow.split("permissions:", 1)[0].split("outputs:", 1)[1]
        self.assertIn("resolved_inputs_json:", public_outputs)

        registry = json.loads(
            (ROOT / "contracts/public-workflows/products.json").read_text(
                encoding="utf-8"
            )
        )
        oci_api = next(
            item for item in registry["workflows"] if item["api_name"] == "oci.build"
        )
        self.assertIn("resolved_inputs_json", oci_api["outputs"])

    def test_build_delegates_to_hardened_safe_executor(self) -> None:
        sentinel = object()
        repository_root = Path("repo")
        source_root = Path("source")
        plan = object()
        environment = {"GITHUB_RUN_ID": "1"}
        secret_files = {"token": Path("secret")}
        with patch("ci_workflows.oci._execute_plan", return_value=sentinel) as execute:
            result = oci.build(
                repository_root,
                source_root,
                plan,  # type: ignore[arg-type]
                environment,
                secret_files,
            )
        self.assertIs(sentinel, result)
        execute.assert_called_once_with(
            repository_root,
            source_root,
            plan,
            environment,
            secret_files,
        )

    def test_inspect_delegates_to_strict_layout_inspector(self) -> None:
        sentinel = object()
        layout = Path("layout")
        target = object()
        labels = {"org.opencontainers.image.revision": "a" * 40}
        with patch("ci_workflows.oci._inspect_layout", return_value=sentinel) as inspect:
            result = oci.inspect(
                layout,
                target,  # type: ignore[arg-type]
                labels,
            )
        self.assertIs(sentinel, result)
        inspect.assert_called_once_with(layout, target, labels)

    def test_ciw_plan_dispatches_contract_owned_oci_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            errors = io.StringIO()
            code = ciw.main(
                [
                    "--root",
                    str(ROOT),
                    "oci",
                    "validate",
                    "--phase",
                    "plan",
                ],
                environment={
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
                    "GITHUB_EVENT_NAME": "push",
                    "INPUT_ADMITTED_SHA": "a" * 40,
                    "INPUT_PRODUCT_ID": "ciw-oci-smoke",
                    "INPUT_PLATFORM_SET": "linux-amd64",
                },
                stdout=io.StringIO(),
                stderr=errors,
            )
            self.assertEqual(0, code, errors.getvalue())
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual("planned", values["result"])
            self.assertEqual("buildah-tiny", values["runner_profile"])
            self.assertEqual(
                '["linux","amd64","buildah","tiny"]',
                values["runs_on_json"],
            )
            self.assertEqual("", values["failure_code"])
            self.assertNotIn("artifact_exception_used", values)


if __name__ == "__main__":
    unittest.main()
