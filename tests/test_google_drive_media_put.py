from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.parse

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _function(script: str, name: str, next_name: str) -> str:
    lines = script.splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == f"{name}() {{")
    end = next(
        index
        for index, line in enumerate(lines[start + 1 :], start + 1)
        if line.strip() == f"{next_name}() {{"
    )
    return "\n".join(lines[start:end]).rstrip()


class GoogleDriveMediaPutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = yaml.safe_load((ROOT / "actions/google-drive/action.yml").read_text())
        self.script = self.action["runs"]["steps"][0]["run"]

    def test_folder_query_escapes_quotes_and_backslashes(self) -> None:
        function_text = _function(self.script, "drive_list_url", "unique_id")
        parent = "parent'with\\slashes"
        name = "ref'with\\slashes"
        completed = subprocess.run(
            ["bash", "-c", f'''set -Eeuo pipefail
{function_text}
drive_list_url {parent!r} {name!r} folder
'''],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(completed.stdout.strip()).query)

        def literal(value: str) -> str:
            return value.replace("\\", "\\\\").replace("'", "\\'")

        self.assertEqual(
            query["q"],
            [
                f"'{literal(parent)}' in parents and "
                f"name = '{literal(name)}' and "
                "trashed = false and "
                "mimeType = 'application/vnd.google-apps.folder'"
            ],
        )
        self.assertEqual(query["fields"], ["nextPageToken,files(id,name,mimeType,createdTime)"])

    def test_existing_exact_folder_is_reused_without_create(self) -> None:
        function_text = _function(self.script, "drive_list_url", "verify_repository_folder")
        response = '{"files":[{"id":"existing-id","createdTime":"2026-08-31T00:00:00Z"}]}'
        harness = f'''set -Eeuo pipefail
{function_text}
access_token=masked-token
curl() {{
  method=GET
  url=""
  while test "$#" -gt 0; do
    case "$1" in
      --request) method="$2"; shift 2 ;;
      http*) url="$1"; shift ;;
      *) shift ;;
    esac
  done
  test "${{method}}" = GET || {{ echo "unexpected ${{method}}" >&2; return 97; }}
  test -n "${{url}}"
  printf '%s' {response!r}
}}
result="$(ensure_folder parent-id exact-name)"
test "${{result}}" = existing-id
printf '%s' "${{result}}"
'''
        completed = subprocess.run(
            ["bash", "-c", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "existing-id")

    def test_existing_folder_lookup_paginates_before_canonical_selection(self) -> None:
        function_text = _function(self.script, "drive_list_url", "verify_repository_folder")
        harness = f'''set -Eeuo pipefail
{function_text}
access_token=masked-token
curl() {{
  method=GET
  url=""
  while test "$#" -gt 0; do
    case "$1" in
      --request) method="$2"; shift 2 ;;
      http*) url="$1"; shift ;;
      *) shift ;;
    esac
  done
  test "${{method}}" = GET || {{ echo "unexpected ${{method}}" >&2; return 97; }}
  case "${{url}}" in
    *pageToken=page-2*)
      printf '%s' '{{"files":[{{"id":"canonical-id","createdTime":"2026-08-31T00:00:01Z"}}]}}'
      ;;
    *)
      printf '%s' '{{"nextPageToken":"page-2","files":[{{"id":"later-id","createdTime":"2026-08-31T00:00:02Z"}}]}}'
      ;;
  esac
}}
result="$(ensure_folder parent-id exact-name)"
test "${{result}}" = canonical-id
printf '%s' "${{result}}"
'''
        completed = subprocess.run(
            ["bash", "-c", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "canonical-id")
        self.assertIn("preserving every non-canonical folder", completed.stderr)

    def test_concurrent_folder_creators_converge_and_empty_loser_is_trashed(self) -> None:
        function_text = _function(self.script, "drive_list_url", "verify_repository_folder")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            counter = temp / "list-count"
            request_log = temp / "requests.log"
            counter.write_text("0")
            harness = f'''set -Eeuo pipefail
{function_text}
access_token=masked-token
LIST_COUNT={str(counter)!r}
REQUEST_LOG={str(request_log)!r}
drive_backoff_sleep() {{ :; }}
emit() {{
  if test -n "$1"; then
    printf '%s' "$2" > "$1"
  else
    printf '%s' "$2"
  fi
}}
curl() {{
  method=GET
  output=""
  url=""
  while test "$#" -gt 0; do
    case "$1" in
      --request) method="$2"; shift 2 ;;
      --output) output="$2"; shift 2 ;;
      http*) url="$1"; shift ;;
      *) shift ;;
    esac
  done
  printf '%s %s\n' "${{method}}" "${{url}}" >> "${{REQUEST_LOG}}"
  if test "${{method}}" = POST; then
    emit "${{output}}" '{{"id":"own-id","createdTime":"2026-08-31T00:00:02Z"}}'
    return 0
  fi
  if test "${{method}}" = PATCH; then
    emit "${{output}}" '{{"id":"own-id","trashed":true}}'
    return 0
  fi
  case "${{url}}" in
    *pageSize=1)
      emit "${{output}}" '{{"files":[]}}'
      return 0
      ;;
  esac
  count="$(cat "${{LIST_COUNT}}")"
  count=$((count + 1))
  printf '%s' "${{count}}" > "${{LIST_COUNT}}"
  case "${{count}}" in
    1) body='{{"files":[]}}' ;;
    2) body='{{"files":[{{"id":"own-id","createdTime":"2026-08-31T00:00:02Z"}}]}}' ;;
    3) body='{{"files":[{{"id":"own-id","createdTime":"2026-08-31T00:00:02Z"}},{{"id":"canonical-id","createdTime":"2026-08-31T00:00:01Z"}}]}}' ;;
    *) body='{{"files":[{{"id":"own-id","createdTime":"2026-08-31T00:00:02Z"}}]}}' ;;
  esac
  emit "${{output}}" "${{body}}"
}}
result="$(ensure_folder parent-id exact-name)"
test "${{result}}" = canonical-id
printf '%s' "${{result}}"
'''
            completed = subprocess.run(
                ["bash", "-c", harness],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "canonical-id")
            requests = request_log.read_text()
            self.assertIn("POST https://www.googleapis.com/drive/v3/files?fields=id,createdTime", requests)
            self.assertIn(
                "PATCH https://www.googleapis.com/drive/v3/files/own-id?fields=id,trashed",
                requests,
            )
            self.assertNotIn("PATCH https://www.googleapis.com/drive/v3/files/canonical-id", requests)
            self.assertEqual(
                sum(line.endswith("pageSize=1") for line in requests.splitlines()),
                2,
            )

    def test_preexisting_duplicate_folders_are_preserved_with_bounded_warning(self) -> None:
        function_text = _function(self.script, "drive_list_url", "verify_repository_folder")
        response = (
            '{"files":['
            '{"id":"later-nonempty","createdTime":"2026-08-31T00:00:01Z"},'
            '{"id":"canonical-id","createdTime":"2026-08-31T00:00:01Z"}'
            ']}'
        )
        harness = f'''set -Eeuo pipefail
{function_text}
access_token=masked-token
curl() {{
  method=GET
  while test "$#" -gt 0; do
    case "$1" in
      --request) method="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  test "${{method}}" = GET || {{ echo "unexpected mutation" >&2; return 96; }}
  printf '%s' {response!r}
}}
result="$(ensure_folder parent-id exact-name)"
test "${{result}}" = canonical-id
printf '%s' "${{result}}"
'''
        completed = subprocess.run(
            ["bash", "-c", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "canonical-id")
        self.assertIn("preserving every non-canonical folder", completed.stderr)

    def test_zero_and_resumed_offsets_execute_under_strict_bash(self) -> None:
        function_text = _function(self.script, "run_media_put", "reconcile_session")

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
  for arg in "$@"; do printf '%s\n' "$arg" >> "${{CURL_ARGS}}"; done
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

    def test_immutable_existing_file_is_idempotent_only_for_identical_bytes(self) -> None:
        lines = self.script.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == "verify_immutable_existing() {")
        end = next(
            i
            for i, line in enumerate(lines[start + 1 :], start + 1)
            if line.strip() == 'if test -n "${DRIVE_REPOSITORY_FOLDER_ID}"; then'
        )
        function_text = "\n".join(lines[start:end]).rstrip()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            local = temp / "local.bin"
            remote = temp / "remote.bin"
            local.write_bytes(b"same-release-bytes")
            for remote_bytes, expected_rc in ((b"same-release-bytes", 0), (b"different-release-bytes", 1)):
                remote.write_bytes(remote_bytes)
                harness = f'''set -Eeuo pipefail
{function_text}
upload_path={str(local)!r}
existing_download_file={str(temp / "download.bin")!r}
existing_id=masked-existing-id
access_token=masked-token
REMOTE_SOURCE={str(remote)!r}
curl() {{
  output=""
  while test "$#" -gt 0; do
    if test "$1" = --output; then output="$2"; shift 2; continue; fi
    shift
  done
  test -n "${{output}}"
  cp "${{REMOTE_SOURCE}}" "${{output}}"
}}
verify_immutable_existing
'''
                completed = subprocess.run(
                    ["bash", "-c", harness],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, expected_rc, completed.stderr)
                if expected_rc:
                    self.assertIn(
                        "immutable Google Drive file already exists with different bytes",
                        completed.stderr,
                    )

    def test_optional_subdirectory_and_immutable_mode_are_bounded(self) -> None:
        inputs = self.action["inputs"]
        self.assertEqual(inputs["subdirectory"]["default"], "")
        self.assertEqual(inputs["immutable"]["default"], "false")
        self.assertIn("subdirectory must be one bounded child folder", self.script)
        self.assertIn('target_folder_id="$(ensure_folder "${ref_folder_id}" "${DRIVE_SUBDIRECTORY}")"', self.script)
        self.assertIn('repository_folder_id="${DRIVE_REPOSITORY_FOLDER_ID}"', self.script)
        self.assertIn('repository_folder_id="$(ensure_folder "${GOOGLE_DRIVE_ROOT_FOLDER_ID}" "${repository_name}")"', self.script)
        self.assertIn('test "${DRIVE_IMMUTABLE}" = "true"', self.script)
        self.assertIn("immutable uploads require caller-supplied deterministic bytes", self.script)
        self.assertIn('"$(drive_list_url "${target_folder_id}" "${DRIVE_FILE_NAME}" file)"', self.script)
        self.assertIn('printf \'folder_id=%s\\n\' "${target_folder_id}"', self.script)


if __name__ == "__main__":
    unittest.main()
