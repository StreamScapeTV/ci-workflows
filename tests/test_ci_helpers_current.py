from __future__ import annotations

import yaml

from tests import test_ci_helpers as _prior


class CiHelperTests(_prior.CiHelperTests):
    """Current shared-helper contract while retaining the complete helper suite."""

    def test_agent_state_action_has_claim_start_observe_finish_lifecycle(self) -> None:
        path = _prior.ROOT / "actions/agent-state/action.yml"
        action = yaml.safe_load(path.read_text())
        text = path.read_text()
        self.assertIn("claim_ci_run", text)
        self.assertIn("transition_ci_run", text)
        self.assertIn("external_repository:$repository", text)
        self.assertIn("external_run_url:$run_url", text)
        self.assertIn("https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}", text)
        self.assertIn("observed_source_sha", action["inputs"])
        self.assertIn("observe-source", action["inputs"]["phase"]["description"])
        self.assertIn("cancel-if-active", action["inputs"]["phase"]["description"])
        self.assertIn('[[ "${OBSERVED_SOURCE_SHA}" =~ ^[0-9A-Fa-f]{40}$ ]] || exit 2', text)
        self.assertIn("p_patch:{observed_source_sha:$sha}", text)
        self.assertIn('p_patch:{status:"failed"}', text)
        self.assertIn("already_terminal", text)
        self.assertIn("succeeded|failed) exit 0", text)
        self.assertIn('cancelled) agent_state_status=failed', text)
        self.assertNotIn("timed_out", text)
        self.assertIn('succeeded|failed) agent_state_status="${TERMINAL_STATUS}"', text)
        self.assertNotIn('p_patch:{status:"cancelled"}', text)
        self.assertIn("Agent State cancellation settlement failed", text)
        self.assertNotIn("diagnostic_", text)
