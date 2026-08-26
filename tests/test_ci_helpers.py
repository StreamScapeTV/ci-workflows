from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class CiHelperTests(unittest.TestCase):
    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "android", "python", "node", "flutter", "gitops", "central_dispatch", "self_check", "broker_release", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "r2_upload"})
        self.assertFalse((ROOT / "PYTHON_INVENTORY.yml").exists())
        self.assertFalse((ROOT / "contracts").exists())

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 10)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        for name in ("apple.yml", "android.yml", "python.yml", "node.yml", "flutter.yml", "gitops.yml"):
            self.assertIn(name, names)

    def test_only_two_custom_actions_exist(self) -> None:
        actions = {p.name for p in (ROOT / "actions").iterdir() if p.is_dir()}
        self.assertEqual(actions, {"agent-state", "r2-upload"})

    def test_agent_state_action_has_only_claim_start_finish_lifecycle(self) -> None:
        text = (ROOT / "actions/agent-state/action.yml").read_text()
        self.assertIn("claim_ci_run", text)
        self.assertIn("transition_ci_run", text)
        self.assertNotIn("observed_source_sha", text)
        self.assertNotIn("diagnostic_", text)

    def test_r2_action_is_one_put_without_readback(self) -> None:
        text = (ROOT / "actions/r2-upload/action.yml").read_text()
        self.assertIn("ci-runs/${CI_RUN_ID}/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.log.gz", text)
        self.assertEqual(text.count("--upload-file"), 1)
        self.assertNotIn("sha256", text.lower())
        self.assertNotIn("read-back", text.lower())


if __name__ == "__main__":
    unittest.main()
