"""Every legacy Central CI pair and capability must map to a new workflow."""
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
PARITY = yaml.safe_load((ROOT / "docs/ci-parity.yaml").read_text())
CONTRACT = json.loads((ROOT / "contracts/repository-ci-v1.json").read_text())
VALIDATION_PROFILES = {"lint", "build", "test", "full", "ui-test", "device-test"}


def legacy_pairs():
    """Pairs gated by a profile, plus (key, "*") for keys never gated by one."""
    specific = set()
    for expr in re.findall(r"\bif:\s*\$\{\{(.*?)\}\}", DISPATCH, re.S):
        keys = re.findall(r"workflow_key == '([^']+)'", expr)
        profiles = re.findall(r"test_profile == '([^']+)'", expr)
        if len(keys) == 1 and profiles:
            specific.update((keys[0], p) for p in profiles)
    pairs = set(specific)
    for key in set(re.findall(r"workflow_key == '([^']+)'", DISPATCH)):
        if not any(k == key for k, _ in specific):
            pairs.add((key, "*"))
    pairs.discard(("validation.repository", "*"))
    pairs.update(("validation.repository", p) for p in VALIDATION_PROFILES)
    return pairs


class ParityTest(unittest.TestCase):
    def test_every_legacy_pair_is_mapped_once(self):
        mapped = [(e["key"], e["profile"]) for e in PARITY["legacy_pairs"]]
        self.assertEqual(len(mapped), len(set(mapped)), "duplicate mapping")
        for key, profile in legacy_pairs():
            ok = (key, profile) in mapped or (key, "*") in mapped
            self.assertTrue(ok, f"unmapped legacy pair {key}/{profile}")

    def test_no_mapping_for_unknown_key(self):
        keys = {k for k, _ in legacy_pairs()}
        for e in PARITY["legacy_pairs"]:
            self.assertIn(e["key"], keys, f"mapping for unknown key {e['key']}")

    def test_every_capability_is_mapped(self):
        wanted = {c["capability"] for c in CONTRACT["legacyCapabilityParity"]["capabilities"]}
        self.assertEqual(wanted, set(PARITY["capabilities"]))

    def test_entries_declare_status_and_tailscale(self):
        for e in PARITY["legacy_pairs"]:
            self.assertIn(e["status"], {"implemented", "planned"})
            self.assertIsInstance(e["tailscale"], bool)
            self.assertTrue(e["target"])

    def test_implemented_workflow_targets_exist(self):
        for e in PARITY["legacy_pairs"]:
            if e["status"] == "implemented" and e["target"].startswith("ci-") and " " not in e["target"]:
                self.assertTrue((ROOT / ".github/workflows" / f"{e['target']}.yml").exists(), e["target"])


if __name__ == "__main__":
    unittest.main()
