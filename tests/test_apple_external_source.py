from __future__ import annotations

import unittest

from ci_workflows.apple import request_from_environment


class AppleExternalSourceTests(unittest.TestCase):
    def base_environment(self) -> dict[str, str]:
        return {
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "INPUT_ADMITTED_SHA": "a" * 40,
            "INPUT_COMMAND_PROFILE": "synthetic-consumer",
            "INPUT_CONSUMER_CONTRACT": "",
            "INPUT_VALIDATION_PROFILE": "source-audit",
        }

    def test_explicit_external_repository_is_request_identity(self) -> None:
        environment = self.base_environment()
        environment["INPUT_SOURCE_REPOSITORY"] = "OtherOrg/private-app"

        request = request_from_environment(
            environment,
            {"forbidden_inputs": []},
        )

        self.assertEqual(request.repository, "OtherOrg/private-app")
        self.assertEqual(request.admitted_sha, "a" * 40)
        self.assertEqual(request.source_trust, "trusted-exact")

    def test_empty_external_repository_preserves_caller_repository(self) -> None:
        environment = self.base_environment()
        environment["INPUT_SOURCE_REPOSITORY"] = ""

        request = request_from_environment(
            environment,
            {"forbidden_inputs": []},
        )

        self.assertEqual(request.repository, "StreamScapeTV/ci-workflows")


if __name__ == "__main__":
    unittest.main()
