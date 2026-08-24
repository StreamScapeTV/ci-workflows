from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECURITY_DOC = ROOT / "docs/architecture/security-and-artifacts.md"
PUBLIC_TYPES = ROOT / "contracts/public-workflow-types.json"
ROW = re.compile(
    r"^\| `(?P<name>[a-z][a-z0-9-]+)` \| "
    r"(?P<privileged>yes|no) \| (?P<executes>yes|no) \|"
)


class SecurityTrustClassDocumentationTests(unittest.TestCase):
    def test_security_table_matches_machine_trust_class_facts(self) -> None:
        contract = json.loads(PUBLIC_TYPES.read_text(encoding="utf-8"))
        guide = SECURITY_DOC.read_text(encoding="utf-8")
        trust_section = guide.split("## Trust classes\n", 1)[1].split(
            "## Source and credentials\n", 1
        )[0]

        documented: dict[str, tuple[bool, bool]] = {}
        for line in trust_section.splitlines():
            match = ROW.match(line)
            if match is None:
                continue
            documented[match.group("name")] = (
                match.group("privileged") == "yes",
                match.group("executes") == "yes",
            )

        expected = {
            name: (
                bool(shape["privileged"]),
                bool(shape["executes_caller_source"]),
            )
            for name, shape in contract["trust_classes"].items()
        }
        self.assertEqual(expected, documented)
        for retired in ("flux-authorized", "trusted-maintenance"):
            self.assertNotIn(f"| `{retired}` |", trust_section)

    def test_security_baseline_keeps_shared_secret_artifact_and_cleanup_rules(self) -> None:
        guide = SECURITY_DOC.read_text(encoding="utf-8")
        for phrase in (
            "persist-credentials: false",
            "`secrets: inherit` is forbidden",
            "Routine workflows retain zero Actions artifacts",
            "Cleanup runs on every terminal path",
        ):
            self.assertIn(phrase, guide)


if __name__ == "__main__":
    unittest.main()
