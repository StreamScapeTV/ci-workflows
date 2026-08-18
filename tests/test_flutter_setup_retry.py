from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ROOT / ".github/workflows/reusable-flutter.yml"


class FlutterSetupRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = REUSABLE.read_text(encoding="utf-8")

    def _job_block(self, job: str, next_job: str) -> str:
        return self.workflow.split(f"  {job}:\n", 1)[1].split(
            f"  {next_job}:\n", 1
        )[0]

    def _reset_script(self, block: str) -> str:
        match = re.search(
            r"(?ms)- id: flutter_setup_reset\n.*?^        run: \|\n"
            r"(?P<script>(?:^          .*\n)+)",
            block,
        )
        self.assertIsNotNone(match)
        return textwrap.dedent(match.group("script"))

    def _simulate_interrupted_first_transfer(self, block: str) -> None:
        self.assertEqual(2, block.count("uses: subosito/flutter-action@"))
        self.assertEqual(1, block.count("- id: flutter_setup_primary"))
        self.assertEqual(1, block.count("- id: flutter_setup_retry"))
        self.assertIn("continue-on-error: true", block)
        condition = "if: ${{ steps.flutter_setup_primary.outcome == 'failure' }}"
        self.assertEqual(2, block.count(condition))

        reset_script = self._reset_script(block)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool_root = root / "tools"
            workflow_root = root / "workflow"
            flutter_cache = tool_root / "flutter-sdk-3.41.4"
            pub_cache = workflow_root / "tmp/flutter-validation/pub-cache"
            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True)
            fake_sleep = fake_bin / "sleep"
            fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_sleep.chmod(0o755)

            harness = f"""
            set -Eeuo pipefail
            attempt=0
            setup_attempt() {{
              attempt=$((attempt + 1))
              if [ "$attempt" -eq 1 ]; then
                mkdir -p "$FLUTTER_CACHE_PATH" "$PUB_CACHE_PATH"
                printf partial > "$FLUTTER_CACHE_PATH/interrupted.archive"
                printf partial > "$PUB_CACHE_PATH/interrupted.pub"
                return 18
              fi
              test ! -e "$FLUTTER_CACHE_PATH/interrupted.archive"
              test ! -e "$PUB_CACHE_PATH/interrupted.pub"
              mkdir -p "$FLUTTER_CACHE_PATH/flutter/bin"
              printf '#!/bin/sh\\nexit 0\\n' > "$FLUTTER_CACHE_PATH/flutter/bin/flutter"
              chmod +x "$FLUTTER_CACHE_PATH/flutter/bin/flutter"
            }}

            if ! setup_attempt; then
            {textwrap.indent(reset_script, '  ')}
              setup_attempt
            fi

            test "$attempt" -eq 2
            test -x "$FLUTTER_CACHE_PATH/flutter/bin/flutter"
            test -d "$PUB_CACHE_PATH"
            test -z "$(find "$PUB_CACHE_PATH" -mindepth 1 -maxdepth 1 -print -quit)"
            """
            env = os.environ.copy()
            env.update(
                {
                    "CI_TOOL_ROOT": str(tool_root),
                    "CI_WORKFLOW_ROOT": str(workflow_root),
                    "FLUTTER_CACHE_PATH": str(flutter_cache),
                    "PUB_CACHE_PATH": str(pub_cache),
                    "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                }
            )
            completed = subprocess.run(
                ["bash", "-c", textwrap.dedent(harness)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                0,
                completed.returncode,
                completed.stdout + completed.stderr,
            )

    def test_retry_preserves_exact_version_and_zero_actions_cache(self) -> None:
        for block in (
            self._job_block("mobile", "apple"),
            self._job_block("apple", "validate"),
        ):
            self.assertEqual(
                2,
                block.count(
                    "flutter-version: ${{ needs.plan.outputs.flutter_version }}"
                ),
            )
            self.assertEqual(2, block.count("cache: false"))
            self.assertEqual(
                2,
                block.count(
                    "cache-path: ${{ format('{0}/flutter-sdk-{1}', env.CI_TOOL_ROOT, needs.plan.outputs.flutter_version) }}"
                ),
            )
            self.assertEqual(
                2,
                block.count(
                    "pub-cache-path: ${{ format('{0}/tmp/flutter-validation/pub-cache', env.CI_WORKFLOW_ROOT) }}"
                ),
            )
            self.assertNotIn("actions/cache@", block)
            self.assertLess(
                block.index("- id: flutter_setup_retry"),
                block.index("phase: verify-toolchain"),
            )

    def test_mobile_interrupted_first_transfer_resets_then_retries_once(self) -> None:
        self._simulate_interrupted_first_transfer(self._job_block("mobile", "apple"))

    def test_apple_interrupted_first_transfer_resets_then_retries_once(self) -> None:
        self._simulate_interrupted_first_transfer(self._job_block("apple", "validate"))


if __name__ == "__main__":
    unittest.main()
