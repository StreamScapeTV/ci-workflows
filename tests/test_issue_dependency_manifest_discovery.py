from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.issue_dependencies import ManifestValidationError, RepositoryRecord, discover_manifests

SCHEMA = json.loads((ROOT / "contracts" / "issue-dependencies.schema.json").read_text())


def text(repo: str) -> str:
    return f"version: 1\nrepository: {repo}\nissues:\n  1:\n    blocked_by: []\n"


class Gateway:
    def __init__(self, branch="main"):
        self.repo = "StreamScapeTV/demo"
        self.repositories = [RepositoryRecord(self.repo, branch)]
        self.files = {}
        self.reads = []

    def list_repositories(self):
        return tuple(self.repositories)

    def read_file(self, repository, path, ref):
        self.reads.append((repository, path, ref))
        return self.files.get((repository, path, ref))


class DiscoveryTests(unittest.TestCase):
    def test_yml_uses_default_branch_without_agents(self):
        gateway = Gateway("develop")
        gateway.files[(gateway.repo, "ISSUE_DEPENDENCIES.yml", "develop")] = text(gateway.repo)
        _, manifests = discover_manifests(gateway, SCHEMA)
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0].integration_branch, "develop")
        self.assertTrue(all(path != "AGENTS.md" for _, path, _ in gateway.reads))

    def test_yaml_supported_and_absence_is_unmanaged(self):
        gateway = Gateway()
        _, manifests = discover_manifests(gateway, SCHEMA)
        self.assertEqual(manifests, ())
        gateway.files[(gateway.repo, "ISSUE_DEPENDENCIES.yaml", "main")] = text(gateway.repo)
        _, manifests = discover_manifests(gateway, SCHEMA)
        self.assertEqual(len(manifests), 1)

    def test_both_suffixes_are_ambiguous(self):
        gateway = Gateway()
        for path in ("ISSUE_DEPENDENCIES.yml", "ISSUE_DEPENDENCIES.yaml"):
            gateway.files[(gateway.repo, path, "main")] = text(gateway.repo)
        with self.assertRaisesRegex(ManifestValidationError, "multiple issue dependency manifests"):
            discover_manifests(gateway, SCHEMA)


if __name__ == "__main__":
    unittest.main()
