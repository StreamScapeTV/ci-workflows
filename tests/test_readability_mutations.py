from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixture_builder import create_repository, write_text
from ci_workflows.validation_harness import validate_repository

ROOT = Path(__file__).resolve().parents[1]


def workflow_with_run(lines: int) -> str:
    body = "\n".join(f"          echo line-{index}" for index in range(lines))
    return f"""name: Inline script boundary
on:
  push:
permissions: {{}}
jobs:
  validate_inline:
    name: Validate inline script boundary
    runs-on: portable
    timeout-minutes: 10
    steps:
      - name: Execute bounded glue
        shell: bash
        run: |
{body}
"""


def workflow_with_matrix(size: int) -> str:
    values = ", ".join(f"case-{index}" for index in range(size))
    return f"""name: Matrix boundary
on:
  push:
permissions: {{}}
jobs:
  validate_matrix:
    name: Validate matrix boundary
    runs-on: portable
    timeout-minutes: 10
    strategy:
      matrix:
        case: [{values}]
    steps:
      - name: Execute matrix case
        shell: bash
        run: echo "${{{{ matrix.case }}}}"
"""


class ReadabilityMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        create_repository(
            self.root,
            config_overrides={
                "max_inline_run_lines": 40,
                "max_matrix_jobs": 16,
            },
        )

    def rules(self) -> set[str]:
        return {
            finding.rule
            for finding in validate_repository(
                self.root,
                include_public_api_validator=False,
            ).findings
        }

    def test_forty_line_inline_script_passes_and_forty_one_fails(self) -> None:
        path = self.root / ".github/workflows/inline-boundary.yml"
        write_text(path, workflow_with_run(40))
        self.assertNotIn("oversized-inline-run", self.rules())
        write_text(path, workflow_with_run(41))
        self.assertIn("oversized-inline-run", self.rules())

    def test_sixteen_entry_matrix_passes_and_seventeen_fails(self) -> None:
        path = self.root / ".github/workflows/matrix-boundary.yml"
        write_text(path, workflow_with_matrix(16))
        self.assertNotIn("unbounded-matrix", self.rules())
        write_text(path, workflow_with_matrix(17))
        self.assertIn("unbounded-matrix", self.rules())

    def test_duplicate_detection_starts_at_eight_nonempty_lines(self) -> None:
        first = self.root / ".github/workflows/duplicate-first.yml"
        second = self.root / ".github/workflows/duplicate-second.yml"
        seven = "\n".join(f"          echo shared-{index}" for index in range(7))
        eight = "\n".join(f"          echo shared-{index}" for index in range(8))

        def source(name: str, block: str) -> str:
            return f"""name: {name}
on:
  push:
permissions: {{}}
jobs:
  validate_duplicate:
    name: Validate duplicate threshold
    runs-on: portable
    timeout-minutes: 10
    steps:
      - name: Execute shared block
        shell: bash
        run: |
{block}
"""

        write_text(first, source("First duplicate fixture", seven))
        write_text(second, source("Second duplicate fixture", seven))
        self.assertNotIn("duplicated-implementation", self.rules())
        write_text(first, source("First duplicate fixture", eight))
        write_text(second, source("Second duplicate fixture", eight))
        self.assertIn("duplicated-implementation", self.rules())

    def test_reusable_workflow_cycle_fails_closed(self) -> None:
        write_text(
            self.root / ".github/workflows/internal-a.yml",
            """name: Internal A
on:
  workflow_call:
permissions: {}
jobs:
  call_b:
    name: Call internal B
    uses: ./.github/workflows/internal-b.yml
""",
        )
        write_text(
            self.root / ".github/workflows/internal-b.yml",
            """name: Internal B
on:
  workflow_call:
permissions: {}
jobs:
  call_a:
    name: Call internal A
    uses: ./.github/workflows/internal-a.yml
""",
        )
        rules = self.rules()
        self.assertIn("reusable-workflow-cycle", rules)
        self.assertIn("nested-internal-workflow", rules)

    def test_fixture_manifest_records_exact_positive_and_negative_cases(self) -> None:
        manifest = json.loads(
            (ROOT / "tests/fixtures/readability/cases.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], 1)
        positive = {item["id"] for item in manifest["positive"]}
        negative = {item["id"] for item in manifest["negative"]}
        self.assertEqual(
            positive,
            {
                "seven-job-public-workflow",
                "forty-line-inline-script",
                "sixteen-entry-matrix",
                "single-layer-composite",
                "complete-reviewed-exception",
            },
        )
        self.assertEqual(
            negative,
            {
                "eighth-public-job",
                "forty-one-line-inline-script",
                "seventeen-entry-matrix",
                "duplicate-orchestration",
                "opaque-job-name",
                "callback-like-input",
                "composite-depth-two",
                "workflow-cycle",
                "malformed-exception",
                "untested-exception",
            },
        )


if __name__ == "__main__":
    unittest.main()
