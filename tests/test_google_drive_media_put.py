from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class GoogleDriveMediaPutTests(unittest.TestCase):
    def test_zero_and_resumed_offsets_execute_under_strict_bash(self) -> None:
        action = yaml.safe_load((ROOT / "actions/google-drive/action.yml").read_text())
        script = action["runs"]["steps"][0]["run"]
        lines = script.splitlines()
        start = next(index for index, line in enumerate(lines) if line.strip() == "run_media_put() {")
        end = next(
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line.strip() == "reconcile_session() {"
        )
        function_text = "\n".join(lines[start:end]).rstrip()

        self.assertNotIn("range_args", function_text)
        self.assertIn('"$@"', function_text)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            payload = temp / "payload.bin"
            payload.write_bytes(b"abcdef")
            for offset, expected_body, expected_range in (
                (0, b"abcdef", None),
                (2, b"cdef", "Content-Range: bytes 2-5/6"),
            ):
                args_file = temp / f"curl-{offset}.args"
                body_file = temp / f"curl-{offset}.body"
                harness = f'''set -Eeuo pipefail
{function_text}
byte_size=6
upload_path={str(payload)!r}
media_headers_file={str(temp / f"media-{offset}.headers")!r}
media_response_file={str(temp / f"media-{offset}.json")!r}
access_token=masked-test-token
DRIVE_MIME_TYPE=application/octet-stream
session_url=https://masked.invalid/upload-session
CURL_ARGS={str(args_file)!r}
CURL_BODY={str(body_file)!r}
curl() {{
  : > "${{CURL_ARGS}}"
  for arg in "$@"; do printf '%s\\n' "$arg" >> "${{CURL_ARGS}}"; done
  cat > "${{CURL_BODY}}"
  printf '200'
}}
run_media_put {offset}
test "${{media_curl_status}}" -eq 0
test "${{media_http_code}}" = 200
'''
                completed = subprocess.run(
                    ["bash", "-c", harness],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(body_file.read_bytes(), expected_body)
                curl_args = args_file.read_text()
                self.assertIn(f"Content-Length: {len(expected_body)}", curl_args)
                if expected_range is None:
                    self.assertNotIn("Content-Range:", curl_args)
                else:
                    self.assertIn(expected_range, curl_args)


if __name__ == "__main__":
    unittest.main()
