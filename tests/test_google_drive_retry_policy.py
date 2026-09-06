from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class GoogleDriveRetryPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        action = yaml.safe_load((ROOT / "actions/google-drive/action.yml").read_text())
        self.script = action["runs"]["steps"][0]["run"]
        lines = self.script.splitlines()
        start = next(
            index
            for index, line in enumerate(lines)
            if line.strip() == "max_media_upload_attempts=5"
        )
        end = next(
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line.strip().startswith('test -n "${file_id}"')
        )
        self.retry_loop = "\n".join(lines[start:end])

    def test_retry_policy_is_fixed_bounded_and_single_destination(self) -> None:
        self.assertIn("max_media_upload_attempts=5", self.script)
        self.assertIn("media_retry_delays=(3 5 8 13)", self.script)
        self.assertIn("408|429|5??", self.script)
        self.assertIn("retryable_media_failure", self.script)
        self.assertIn("reconcile_session", self.retry_loop)
        self.assertIn('drive_backoff_sleep "${retry_delay}"', self.retry_loop)
        self.assertIn("failed after bounded recovery attempts", self.retry_loop)
        self.assertIn("Google Drive resumable media upload failed with HTTP", self.retry_loop)
        self.assertNotIn("--retry-all-errors", self.script)
        self.assertNotIn("cloudflarestorage.com", self.script)
        self.assertNotIn("R2_", self.script)

    def _run_loop(self, run_media_put_body: str) -> tuple[subprocess.CompletedProcess[str], list[str], list[str], int]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            attempts = temp / "attempts"
            delays = temp / "delays"
            reconciles = temp / "reconciles"
            attempts.write_text("0", encoding="utf-8")
            delays.write_text("", encoding="utf-8")
            reconciles.write_text("", encoding="utf-8")
            harness = f'''set -Eeuo pipefail
ATTEMPTS={str(attempts)!r}
DELAYS={str(delays)!r}
RECONCILES={str(reconciles)!r}
media_response_file=/dev/null
run_media_put() {{
  count="$(cat "${{ATTEMPTS}}")"
  count=$((count + 1))
  printf '%s' "${{count}}" > "${{ATTEMPTS}}"
  {run_media_put_body}
}}
retryable_media_failure() {{
  if test "$1" -ne 0; then
    return 0
  fi
  case "$2" in
    408|429|5??) return 0 ;;
    *) return 1 ;;
  esac
}}
reconcile_session() {{ printf 'reconcile\n' >> "${{RECONCILES}}"; }}
response_file_id() {{ printf 'uploaded-id\n'; }}
drive_backoff_sleep() {{ printf '%s\n' "$1" >> "${{DELAYS}}"; }}
{self.retry_loop}
printf 'file_id=%s\n' "${{file_id}}"
'''
            completed = subprocess.run(
                ["bash", "-c", harness],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            delay_values = [line for line in delays.read_text(encoding="utf-8").splitlines() if line]
            reconcile_values = [line for line in reconciles.read_text(encoding="utf-8").splitlines() if line]
            attempt_count = int(attempts.read_text(encoding="utf-8"))
            return completed, delay_values, reconcile_values, attempt_count

    def test_transport_failure_uses_four_backoffs_then_fifth_attempt_succeeds(self) -> None:
        completed, delays, reconciles, attempts = self._run_loop(
            '''if test "${count}" -lt 5; then
    media_curl_status=92
    media_http_code=000
  else
    media_curl_status=0
    media_http_code=200
  fi'''
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("file_id=uploaded-id", completed.stdout)
        self.assertEqual(attempts, 5)
        self.assertEqual(delays, ["3", "5", "8", "13"])
        self.assertEqual(len(reconciles), 4)

    def test_transient_http_failure_reconciles_and_retries(self) -> None:
        completed, delays, reconciles, attempts = self._run_loop(
            '''if test "${count}" -eq 1; then
    media_curl_status=0
    media_http_code=503
  else
    media_curl_status=0
    media_http_code=200
  fi'''
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(attempts, 2)
        self.assertEqual(delays, ["3"])
        self.assertEqual(reconciles, ["reconcile"])

    def test_permanent_http_failure_fails_immediately_without_backoff(self) -> None:
        completed, delays, reconciles, attempts = self._run_loop(
            "media_curl_status=0; media_http_code=403"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("failed with HTTP 403", completed.stderr)
        self.assertEqual(attempts, 1)
        self.assertEqual(delays, [])
        self.assertEqual(reconciles, [])


if __name__ == "__main__":
    unittest.main()
