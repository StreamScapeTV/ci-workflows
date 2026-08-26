from __future__ import annotations

import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from ci_workflows.central_profile import CentralProfileResolution
from ci_workflows.ci_private import _execute_family

SHA = "a" * 40


class MixedFamilyPrivateExecutorTests(unittest.TestCase):
    def resolution(self, canonical_inputs: dict[str, str]) -> CentralProfileResolution:
        return CentralProfileResolution(
            project_key="streamscape-media",
            test_profile="host",
            workflow_key="validation.apple",
            capability="apple-hosted",
            source_repository="StreamScapeTV/streamscape-media",
            admitted_sha=SHA,
            validation_scope="legacy",
            validation_plan_json="",
            executor_family="macos",
            canonical_inputs_json=json.dumps(
                canonical_inputs,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def test_apple_v2_projects_only_bounded_inputs_into_existing_executor(self) -> None:
        request = SimpleNamespace(workflow_key="validation.apple")
        resolution = self.resolution(
            {
                "command_profile": "streamscape-media-apple",
                "validation_profile": "swift-package",
            }
        )
        base = {
            "RUNNER_OS": "macOS",
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            "DO_NOT_COPY": "preserved base variable",
            "INPUT_CI_RUN_ID": "11111111-2222-4333-8444-555555555555",
            "INPUT_PHASE": "execute",
        }
        log = io.StringIO()

        with mock.patch(
            "ci_workflows.ci_private._execute_apple_validation",
            return_value=(True, True),
        ) as execute:
            result = _execute_family(
                request=request,  # type: ignore[arg-type]
                source_sha=SHA,
                resolution=resolution,
                token_client=object(),
                environment=base,
                log=log,
            )

        self.assertEqual(result, (True, True))
        execute.assert_called_once()
        forwarded = execute.call_args.kwargs["environment"]
        self.assertEqual(forwarded["INPUT_COMMAND_PROFILE"], "streamscape-media-apple")
        self.assertEqual(forwarded["INPUT_VALIDATION_PROFILE"], "swift-package")
        self.assertEqual(forwarded["DO_NOT_COPY"], "preserved base variable")
        self.assertNotIn("INPUT_CI_RUN_ID", forwarded)
        self.assertNotIn("INPUT_PHASE", forwarded)
        self.assertNotIn("INPUT_EXECUTION_BACKEND", forwarded)
        self.assertNotIn("INPUT_COMMAND", forwarded)
        self.assertNotIn("INPUT_RUNNER", forwarded)

    def test_legacy_apple_empty_projection_remains_unchanged(self) -> None:
        request = SimpleNamespace(workflow_key="validation.apple")
        resolution = self.resolution({})
        base = {"RUNNER_OS": "macOS", "MARKER": "legacy"}

        with mock.patch(
            "ci_workflows.ci_private._execute_apple_validation",
            return_value=(True, True),
        ) as execute:
            _execute_family(
                request=request,  # type: ignore[arg-type]
                source_sha=SHA,
                resolution=resolution,
                token_client=object(),
                environment=base,
                log=io.StringIO(),
            )

        self.assertEqual(execute.call_args.kwargs["environment"], base)


if __name__ == "__main__":
    unittest.main()
