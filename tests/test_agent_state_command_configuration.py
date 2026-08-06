from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/agent-state-command.yml"


class AgentStateCommandConfigurationTests(unittest.TestCase):
    def test_private_endpoint_and_credentials_use_explicit_masked_secrets(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "AGENT_STATE_API_URL: ${{ secrets.AGENT_STATE_API_URL }}",
            "AGENT_STATE_API_TOKEN: ${{ secrets.AGENT_STATE_API_TOKEN }}",
            "TARGET_GITHUB_TOKEN: ${{ secrets.AGENT_STATE_GITHUB_TOKEN }}",
        ):
            self.assertIn(required, source)
        self.assertNotIn("vars.AGENT_STATE_API_URL", source)
        self.assertNotIn("secrets: inherit", source)


if __name__ == "__main__":
    unittest.main()
