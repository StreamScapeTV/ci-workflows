from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class GoogleDriveOAuthExchangeTests(unittest.TestCase):
    def run_exchange(self, responses):
        script = yaml.safe_load((ROOT / "actions/google-drive/action.yml").read_text())["runs"]["steps"][0]["run"]
        block = script[script.index('auth_response_file="$(mktemp'):script.index('echo "::add-mask::${access_token}"')]
        cleanup = script[script.index("cleanup() {"):script.index("trap cleanup EXIT")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "attempts").write_text("0")
            (root / "responses.json").write_text(json.dumps(responses))
            harness = '''set -Eeuo pipefail
headers_file="$RUNNER_TEMP/headers"
media_headers_file="$RUNNER_TEMP/media.headers"
media_response_file="$RUNNER_TEMP/media.json"
existing_download_file="$RUNNER_TEMP/existing.bin"
fresh_readback_file="$RUNNER_TEMP/fresh.bin"
temp_archive=""
auth_response_file=""
''' + cleanup + '''trap cleanup EXIT
sleep() { printf '%s\\n' "$1" >> "$RUNNER_TEMP/delays"; }
curl() {
  local output=""
  while test "$#" -gt 0; do
    if test "$1" = --output; then output="$2"; shift; fi
    shift
  done
  python3 - "$RUNNER_TEMP" "$output" <<'PYFAKE'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]); output=Path(sys.argv[2])
assert output.stat().st_mode & 0o077 == 0
count=int((root/'attempts').read_text()); (root/'attempts').write_text(str(count+1))
responses=json.loads((root/'responses.json').read_text())
response=responses[min(count,len(responses)-1)]
output.write_text(response['body'])
print(response['http'],end='')
sys.exit(response['curl'])
PYFAKE
}
''' + block + '''test -n "$access_token"
printf 'exchange_succeeded\\n'
'''
            result = subprocess.run(["bash", "-c", harness], cwd=ROOT, text=True,
                capture_output=True, env={**os.environ, "RUNNER_TEMP": str(root), "BASH_COMPAT": "3.2",
                    "GOOGLE_DRIVE_CLIENT_ID": "synthetic-client", "GOOGLE_DRIVE_CLIENT_SECRET": "private-secret",
                    "GOOGLE_DRIVE_REFRESH_TOKEN": "private-refresh"})
            attempts = int((root / "attempts").read_text())
            delays = (root / "delays").read_text().splitlines() if (root / "delays").exists() else []
            self.assertEqual(list(root.glob("drive-oauth.*")), [], "production EXIT cleanup removes private OAuth responses")
            self.assertNotIn("private-secret", result.stdout + result.stderr)
            self.assertNotIn("private-refresh", result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            return result, attempts, delays

    @staticmethod
    def response(http="200", curl=0, body='{"access_token":"synthetic-token-value-1234567890"}'):
        return {"http": http, "curl": curl, "body": body}

    def test_transient_dns_recovers_after_more_than_three_attempts(self):
        result, attempts, delays = self.run_exchange([self.response("000", 6, "")] * 4 + [self.response()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(attempts, 5)
        self.assertEqual(delays, ["2"] * 4)
        self.assertIn("curl=6 http=000 category=invalid_token_response", result.stderr)

    def test_rate_limit_recovers_on_eleventh_attempt(self):
        result, attempts, delays = self.run_exchange([self.response("429", body='{"error":"server_error"}')] * 10 + [self.response()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(attempts, 11)
        self.assertEqual(len(delays), 10)

    def test_server_failure_exhausts_fixed_retry_budget(self):
        result, attempts, delays = self.run_exchange([self.response("503", body='{"error":"server_error"}')])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(attempts, 11)
        self.assertEqual(len(delays), 10)
        self.assertIn("failed after bounded attempts", result.stderr)

    def test_permanent_authentication_rejections_are_not_retried_or_dumped(self):
        for http, code in [("400", "invalid_grant"), ("401", "invalid_client")]:
            with self.subTest(code=code):
                result, attempts, delays = self.run_exchange([self.response(http, body='{"error":"' + code + '","error_description":"private-refresh private-secret"}')])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(attempts, 1)
                self.assertEqual(delays, [])
                self.assertIn("category=" + code, result.stderr)

    def test_invalid_json_token_and_unknown_provider_error_fail_closed(self):
        for body in ["private-secret", '{"access_token":"short"}', '{"error":"private-refresh"}', '{"access_token":null}']:
            with self.subTest(body=body):
                result, attempts, delays = self.run_exchange([self.response(body=body)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(attempts, 1)
                self.assertEqual(delays, [])
                self.assertIn("category=invalid_token_response", result.stderr)


if __name__ == "__main__":
    unittest.main()
