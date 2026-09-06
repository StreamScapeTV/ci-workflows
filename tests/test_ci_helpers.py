from pathlib import Path
import http.server
import json
import os
import subprocess
import tempfile
import threading
import urllib.parse
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]


class CiHelperTests(unittest.TestCase):
    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "android", "python", "node", "flutter", "maven", "container_service", "public_native_image_chart", "oci_reproducibility", "branch_delete", "central_dispatch", "self_check", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "google_drive", "private_git"})
        self.assertEqual(set(inventory["scripts"]), {"oci_reproducibility"})
        self.assertEqual(set(inventory["services"]), {"runner_images"})
        self.assertFalse((ROOT / "ci-broker").exists())
        self.assertFalse((ROOT / "PYTHON_INVENTORY.yml").exists())
        self.assertFalse((ROOT / "contracts").exists())

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 13)
        self.assertNotIn("broker.yml", names)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        for name in ("apple.yml", "android.yml", "python.yml", "node.yml", "flutter.yml", "maven.yml", "container-service.yml", "branch-delete.yml"):
            self.assertIn(name, names)

    def test_only_three_custom_actions_exist(self) -> None:
        actions = {p.name for p in (ROOT / "actions").iterdir() if p.is_dir()}
        self.assertEqual(actions, {"agent-state", "google-drive", "private-git"})

    def test_agent_state_action_has_claim_start_observe_finish_lifecycle(self) -> None:
        path = ROOT / "actions/agent-state/action.yml"
        action = yaml.safe_load(path.read_text())
        text = path.read_text()
        self.assertIn("claim_ci_run", text)
        self.assertIn("transition_ci_run", text)
        self.assertIn("external_repository:$repository", text)
        self.assertIn("external_run_url:$run_url", text)
        self.assertIn("https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}", text)
        self.assertIn("observed_source_sha", action["inputs"])
        self.assertIn("observe-source", action["inputs"]["phase"]["description"])
        self.assertIn("cancel-if-active", action["inputs"]["phase"]["description"])
        self.assertIn('[[ "${OBSERVED_SOURCE_SHA}" =~ ^[0-9A-Fa-f]{40}$ ]] || exit 2', text)
        self.assertIn("p_patch:{observed_source_sha:$sha}", text)
        self.assertIn('p_patch:{status:"cancelled"}', text)
        self.assertIn("already_terminal", text)
        self.assertIn("succeeded|failed|cancelled|timed_out) exit 0", text)
        self.assertIn("Agent State cancellation settlement failed", text)
        self.assertNotIn("diagnostic_", text)

    def test_google_drive_action_is_parent_scoped_resumable_and_in_place(self) -> None:
        text = (ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn("https://oauth2.googleapis.com/token", text)
        self.assertIn("GOOGLE_DRIVE_ROOT_FOLDER_ID", text)
        self.assertIn("repository_folder_id:", text)
        self.assertIn("DRIVE_REPOSITORY_FOLDER_ID", text)
        self.assertIn("configured repository folder is not directly below the configured root", text)
        self.assertIn("in parents", text)
        self.assertIn("uploadType=resumable", text)
        self.assertIn("--request PATCH", text)
        self.assertIn("--request POST", text)
        self.assertNotIn("cloudflarestorage.com", text)
        self.assertNotIn("R2_", text)

    def test_google_drive_resumable_upload_retries_only_bounded_transient_failures(self) -> None:
        text = (ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn("max_media_upload_attempts=4", text)
        self.assertIn("408|429|5??", text)
        self.assertIn("retryable_media_failure", text)
        self.assertIn("failed after bounded recovery attempts", text)
        self.assertIn('sleep "${media_attempt}"', text)

    def test_google_drive_resumable_upload_fails_permanent_http_errors(self) -> None:
        text = (ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn("Google Drive resumable media upload failed with HTTP ${media_http_code}", text)
        self.assertIn("Google Drive resumable upload status query failed with HTTP ${session_http_code}", text)
        self.assertNotIn("--retry-all-errors", text)

    def test_google_drive_resumable_upload_reconciles_ambiguous_completion(self) -> None:
        text = (ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn('echo "::add-mask::${session_url}"', text)
        self.assertIn('Content-Range: bytes */${byte_size}', text)
        self.assertIn("reconcile_session", text)
        self.assertIn('if test "$1" -ne 0; then', text)
        self.assertIn("308)", text)
        self.assertIn("received_offset", text)
        self.assertIn("Range header", text)
        self.assertIn('Content-Range: bytes ${offset}-$((byte_size - 1))/${byte_size}', text)

    def test_private_git_action_is_one_fixed_tailscale_boundary(self) -> None:
        text = (ROOT / "actions/private-git/action.yml").read_text()
        self.assertIn("tailscale/github-action@v4", text)
        self.assertIn("tags: tag:github-ci", text)
        self.assertIn('("git.faruqi.dev", 443)', text)
        self.assertIn("TS_OAUTH_CLIENT_ID", text)
        self.assertIn("TS_OAUTH_SECRET", text)
        self.assertNotIn("inputs:", text)
        android = (ROOT / ".github/workflows/android.yml").read_text()
        self.assertIn("actions/private-git@", android)
        self.assertNotIn("tailscale/github-action@v4", android)

    def test_agent_state_workflows_use_private_drive_logs_without_public_command_tee(self) -> None:
        plain_text_logs = {"android", "python", "node", "flutter"}
        for name in ("apple", "android", "python", "node", "flutter"):
            text = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
            self.assertIn("GOOGLE_DRIVE_CI_LOGS_FOLDER_ID", text)
            self.assertIn("actions/google-drive@", text)
            if name in plain_text_logs:
                self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}.txt", text)
                self.assertIn("mime_type: text/plain", text)
                self.assertNotIn("gzip: 'true'", text)
            elif name == "apple":
                self.assertIn(
                    "${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.lane }}.txt",
                    text,
                )
                self.assertIn("mime_type: text/plain", text)
                self.assertNotIn("gzip: 'true'", text)
            else:
                self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}.log.gz", text)
                self.assertIn("mime_type: application/gzip", text)
                self.assertIn("gzip: 'true'", text)
            self.assertIn("inspect the private Google Drive CI log", text)
            self.assertNotIn("R2_", text)
            self.assertNotIn("r2-upload", text)
            self.assertNotIn("tee -a", text)

    def test_python_records_source_sha_and_uploads_readable_log_before_finish(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/python.yml").read_text())
        self.assertEqual(
            set(workflow["on"]["workflow_call"]["inputs"]),
            {"repository", "ref", "test_profile", "ci_run_id", "upload_private_log"},
        )
        steps = workflow["jobs"]["ci"]["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        identity = by_name["Resolve observed source SHA"]
        observe = by_name["Record observed source SHA"]
        commands = by_name["Run fixed Python profile"]
        scrub = by_name["Scrub configured CI secrets from private log"]
        drive = by_name["Upload CI log to Google Drive"]
        finish = by_name["Finish Agent State run"]

        self.assertEqual(identity["if"], "${{ inputs.ci_run_id != '' }}")
        self.assertIn('source_sha="$(git rev-parse HEAD)"', identity["run"])
        self.assertNotIn("github.sha", identity["run"])
        self.assertEqual(observe["if"], "${{ inputs.ci_run_id != '' }}")
        self.assertEqual(observe["uses"], "StreamScapeTV/ci-workflows/actions/agent-state@main")
        self.assertEqual(observe["with"]["phase"], "observe-source")
        self.assertEqual(observe["with"]["observed_source_sha"], "${{ steps.source_identity.outputs.source_sha }}")
        for profile in ("compile)", "unit)", "release-gates)"):
            self.assertIn(profile, commands["run"])
        self.assertEqual(drive["with"]["file_name"], "${{ github.run_id }}-${{ github.run_attempt }}.txt")
        self.assertEqual(drive["with"]["mime_type"], "text/plain")
        self.assertNotIn("gzip", drive["with"])
        self.assertIn("steps.drive.outcome == 'success'", finish["with"]["status"])
        self.assertLess(names.index("Check out source"), names.index("Resolve observed source SHA"))
        self.assertLess(names.index("Resolve observed source SHA"), names.index("Record observed source SHA"))
        self.assertLess(names.index("Record observed source SHA"), names.index("Run fixed Python profile"))
        self.assertLess(names.index("Run fixed Python profile"), names.index("Scrub configured CI secrets from private log"))
        self.assertLess(names.index("Scrub configured CI secrets from private log"), names.index("Upload CI log to Google Drive"))
        self.assertLess(names.index("Upload CI log to Google Drive"), names.index("Finish Agent State run"))
        self.assertEqual(scrub["if"], "${{ always() }}")
        self.assertEqual(finish["if"], "${{ always() && inputs.ci_run_id != '' }}")

    def test_central_dispatch_passes_the_human_ref_directly(self) -> None:
        text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertNotIn("refs/tags/{0}", text)
        self.assertGreaterEqual(text.count("ref: ${{ needs.request.outputs.ref }}"), 6)

    def test_central_dispatch_preserves_newest_run_wins_with_snapshot_isolation(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        jobs = workflow["jobs"]
        execution_jobs = (
            "apple",
            "apple_release",
            "android",
            "python",
            "node",
            "flutter",
            "maven",
            "container_service",
            "public_native_image_chart",
            "oci_reproducibility",
        )
        self.assertNotIn("concurrency", workflow)
        self.assertNotIn("concurrency", jobs["request"])
        for name in execution_jobs:
            self.assertEqual(jobs[name]["concurrency"]["group"], "central-ci-${{ needs.request.outputs.workflow_key }}-${{ inputs.active_key }}")
            self.assertTrue(jobs[name]["concurrency"]["cancel-in-progress"])

        branch_delete = jobs["branch_delete"]["concurrency"]
        self.assertEqual(branch_delete["group"], "central-ci-maintenance-${{ inputs.active_key }}")
        self.assertFalse(branch_delete["cancel-in-progress"])

        snapshot = jobs["source_snapshot"]["concurrency"]
        self.assertEqual(snapshot["group"], "central-ci-snapshot-${{ inputs.active_key }}")
        self.assertTrue(snapshot["cancel-in-progress"])
        self.assertNotEqual(snapshot["group"], jobs["apple"]["concurrency"]["group"])

        settlement = jobs["settle_cancelled"]
        self.assertNotIn("concurrency", settlement)
        self.assertEqual(set(settlement["needs"]), {"request", *execution_jobs, "branch_delete", "source_snapshot"})
        self.assertIn("always()", settlement["if"])
        self.assertIn("needs.request.result != 'success'", settlement["if"])
        for name in (*execution_jobs, "branch_delete", "source_snapshot"):
            self.assertIn(f"needs.{name}.result == 'cancelled'", settlement["if"])
        self.assertEqual(
            settlement["steps"][-1]["with"]["phase"],
            "cancel-if-active",
        )

    def test_central_dispatch_routes_only_bounded_oci_reproducibility_inputs(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        job = workflow["jobs"]["oci_reproducibility"]
        self.assertEqual(job["if"], "${{ needs.request.outputs.workflow_key == 'oci.reproducibility' }}")
        self.assertEqual(job["uses"], "./.github/workflows/oci-reproducibility.yml")
        self.assertEqual(
            set(job["with"]),
            {"repository", "ref", "dockerfile_path", "build_context", "ci_run_id"},
        )
        self.assertEqual(job["with"]["repository"], "${{ needs.request.outputs.repository }}")
        self.assertEqual(job["with"]["ref"], "${{ needs.request.outputs.ref }}")
        self.assertEqual(
            job["with"]["dockerfile_path"],
            "${{ fromJSON(needs.request.outputs.inputs_json).dockerfile_path }}",
        )
        self.assertEqual(
            job["with"]["build_context"],
            "${{ fromJSON(needs.request.outputs.inputs_json).build_context }}",
        )
        self.assertEqual(job["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        self.assertTrue(job["secrets"] == "inherit")

    def test_branch_delete_capability_is_bounded_and_fail_closed(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/branch-delete.yml").read_text())
        dispatch = yaml.safe_load((ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        call = workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "branch", "expected_head", "ci_run_id", "deletion_source", "deleted_ref_type"},
        )
        self.assertIn("delete", workflow["on"])
        self.assertNotIn("workflow_dispatch", workflow["on"])
        self.assertFalse(call["inputs"]["expected_head"]["required"])
        self.assertFalse(call["inputs"]["ci_run_id"]["required"])
        self.assertEqual(call["inputs"]["deletion_source"]["default"], "agent-state")
        self.assertEqual(call["inputs"]["deleted_ref_type"]["default"], "")
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        self.assertIn("branch-retirement-", workflow["concurrency"]["group"])
        self.assertIn("github.event.ref", workflow["concurrency"]["group"])
        self.assertIn("inputs.branch", workflow["concurrency"]["group"])

        delete_job = workflow["jobs"]["delete"]
        steps = delete_job["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        start = by_name["Mark Agent State run as running"]
        validate = by_name["Validate bounded branch deletion inputs"]
        token = by_name["Create exact target repository token"]
        delete = by_name["Delete exact eligible branch"]
        finish = workflow["jobs"]["finish"]
        cleanup = workflow["jobs"]["snapshot_cleanup"]

        self.assertEqual(start["with"]["phase"], "start")
        self.assertIn("github.event_name != 'delete'", start["if"])
        self.assertIn("inputs.deletion_source != 'github-delete-event'", start["if"])
        self.assertEqual(token["uses"], "actions/create-github-app-token@v2")
        self.assertEqual(token["with"]["owner"], "StreamScapeTV")
        self.assertEqual(token["with"]["repositories"], "${{ steps.request.outputs.repository_name }}")
        self.assertIn("github-delete-event", token["with"]["permission-contents"])
        self.assertIn("read", token["with"]["permission-contents"])
        self.assertIn("write", token["with"]["permission-contents"])
        self.assertIn('git check-ref-format --branch "${TARGET_BRANCH}"', validate["run"])
        self.assertIn('branch in {"main", "develop"}', validate["run"])
        self.assertIn('deleted_ref_type != "branch"', validate["run"])
        self.assertIn("does not accept expected_head", validate["run"])
        self.assertIn("does not use Agent State ci_run_id", validate["run"])
        self.assertIn("repository live default branch", delete["run"])
        self.assertIn("delete-event cleanup refuses to delete Drive state while the branch still exists", delete["run"])
        self.assertIn('branch_value.get("protected") is not False', delete["run"])
        self.assertIn('rule.get("type") == "deletion"', delete["run"])
        self.assertIn('for page in range(1, 11)', delete["run"])
        self.assertIn('?per_page=100&page={page}', delete["run"])
        self.assertIn('branch_was_present=false', delete["run"])
        self.assertIn('branch_was_present=true', delete["run"])
        self.assertEqual(cleanup["uses"], "./.github/workflows/source-snapshot-delete.yml")
        self.assertEqual(cleanup["with"]["repository"], "${{ needs.delete.outputs.repository }}")
        self.assertEqual(cleanup["with"]["ref"], "${{ needs.delete.outputs.branch }}")
        self.assertEqual(cleanup["with"]["expected_source_sha"], "")
        self.assertIn("github.event_name != 'delete'", finish["if"])
        self.assertIn("inputs.deletion_source != 'github-delete-event'", finish["if"])
        self.assertIn("needs.snapshot_cleanup.result == 'success'", finish["steps"][0]["with"]["status"])

        request_steps = dispatch["jobs"]["request"]["steps"]
        request_by_name = {step.get("name"): step for step in request_steps if step.get("name")}
        validator = request_by_name["Validate branch deletion request"]
        self.assertEqual(validator["if"], "${{ steps.claim.outputs.workflow_key == 'maintenance.branch-delete' }}")
        self.assertIn('test "${TEST_PROFILE}" = delete', validator["run"])
        self.assertIn('test "${REQUEST_IS_TAG}" = false', validator["run"])
        self.assertIn('set(inputs) != {"expected_head"}', validator["run"])

        job = dispatch["jobs"]["branch_delete"]
        self.assertEqual(
            job["if"],
            "${{ needs.request.outputs.workflow_key == 'maintenance.branch-delete' && needs.request.outputs.test_profile == 'delete' }}",
        )
        self.assertEqual(job["uses"], "./.github/workflows/branch-delete.yml")
        self.assertEqual(set(job["with"]), {"repository", "branch", "expected_head", "ci_run_id"})
        self.assertEqual(job["with"]["repository"], "${{ needs.request.outputs.repository }}")
        self.assertEqual(job["with"]["branch"], "${{ needs.request.outputs.ref }}")
        self.assertEqual(job["with"]["expected_head"], "${{ fromJSON(needs.request.outputs.inputs_json).expected_head }}")
        self.assertTrue(job["secrets"] == "inherit")

        expected_head = "a" * 40
        ci_run_id = "11111111-1111-4111-8111-111111111111"

        def embedded_python(script: str) -> str:
            marker = "python3 - <<'PY'\n"
            start = script.index(marker) + len(marker)
            end = script.index("\nPY", start)
            return script[start:end] + "\n"

        validate_python = embedded_python(validate["run"])
        delete_python = embedded_python(delete["run"])

        def run_embedded(source: str, env_updates: dict[str, str]) -> subprocess.CompletedProcess[str]:
            try:
                with patch.dict(os.environ, env_updates, clear=False):
                    exec(compile(source, "<branch-delete-workflow>", "exec"), {"__name__": "__main__"})
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                return subprocess.CompletedProcess(["embedded-python"], code, "", str(exc))
            except Exception as exc:  # pragma: no cover - surfaced as focused-test failure
                return subprocess.CompletedProcess(["embedded-python"], 1, "", repr(exc))
            return subprocess.CompletedProcess(["embedded-python"], 0, "", "")

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "github-output"

            def validate_case(
                *,
                branch: str = "feature/cleanup",
                deletion_source: str = "agent-state",
                deleted_ref_type: str = "",
                expected: str = expected_head,
                run_id: str = ci_run_id,
            ) -> subprocess.CompletedProcess[str]:
                output.write_text("", encoding="utf-8")
                completed = run_embedded(
                    validate_python,
                    {
                        "TARGET_REPOSITORY": "StreamScapeTV/example",
                        "TARGET_BRANCH": branch,
                        "EXPECTED_HEAD": expected,
                        "CI_RUN_ID": run_id,
                        "DELETION_SOURCE": deletion_source,
                        "DELETED_REF_TYPE": deleted_ref_type,
                        "GITHUB_OUTPUT": str(output),
                    },
                )
                if completed.returncode == 0:
                    ref_check = subprocess.run(
                        ["git", "check-ref-format", "--branch", branch],
                        cwd=ROOT,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=10,
                    )
                    if ref_check.returncode != 0:
                        return subprocess.CompletedProcess(
                            completed.args,
                            ref_check.returncode,
                            completed.stdout,
                            "branch deletion branch name is not a valid Git branch",
                        )
                return completed

            self.assertEqual(validate_case().returncode, 0)
            self.assertNotEqual(validate_case(branch="main").returncode, 0)
            self.assertNotEqual(validate_case(branch="develop").returncode, 0)
            self.assertNotEqual(validate_case(branch="refs/heads/feature/cleanup").returncode, 0)
            self.assertEqual(
                validate_case(
                    deletion_source="github-delete-event",
                    deleted_ref_type="branch",
                    expected="",
                    run_id="",
                ).returncode,
                0,
            )
            self.assertNotEqual(
                validate_case(
                    deletion_source="github-delete-event",
                    deleted_ref_type="tag",
                    expected="",
                    run_id="",
                ).returncode,
                0,
            )
            self.assertNotEqual(
                validate_case(
                    deletion_source="github-delete-event",
                    deleted_ref_type="branch",
                    expected=expected_head,
                    run_id="",
                ).returncode,
                0,
            )

        def run_delete_case(
            *, deletion_source: str = "agent-state", **scenario: object
        ) -> tuple[subprocess.CompletedProcess[str], list[tuple[str, str]]]:
            records: list[tuple[str, str]] = []
            branch_calls = 0
            repository_calls = 0

            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    return

                def _send_json(self, status: int, value: object) -> None:
                    body = json.dumps(value).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self) -> None:
                    nonlocal branch_calls, repository_calls
                    parsed = urllib.parse.urlsplit(self.path)
                    path = urllib.parse.unquote(parsed.path)
                    query = urllib.parse.parse_qs(parsed.query)
                    records.append(("GET", path))
                    if path == "/repos/StreamScapeTV/example":
                        repository_calls += 1
                        default_branch = str(scenario.get("default_branch", "trunk"))
                        if repository_calls > 1 and scenario.get("second_default_branch"):
                            default_branch = str(scenario["second_default_branch"])
                        self._send_json(
                            200,
                            {
                                "full_name": "StreamScapeTV/example",
                                "default_branch": default_branch,
                                "private": False,
                            },
                        )
                        return
                    if path == "/repos/StreamScapeTV/example/branches/feature/cleanup":
                        branch_calls += 1
                        missing = bool(scenario.get("missing", False))
                        if branch_calls > 1 and scenario.get("second_missing"):
                            missing = True
                        if missing:
                            self._send_json(404, {"message": "Not Found"})
                            return
                        head = str(scenario.get("head", expected_head))
                        if branch_calls > 1 and scenario.get("second_head"):
                            head = str(scenario["second_head"])
                        self._send_json(
                            200,
                            {
                                "name": "feature/cleanup",
                                "protected": bool(scenario.get("protected", False)),
                                "commit": {"sha": head},
                            },
                        )
                        return
                    if path == "/repos/StreamScapeTV/example/rules/branches/feature/cleanup":
                        page = int(query.get("page", ["1"])[0])
                        if scenario.get("deletion_rule_page_two"):
                            self._send_json(200, [{"type": "required_linear_history"}] * 100 if page == 1 else [{"type": "deletion"}])
                            return
                        rules = [{"type": "deletion"}] if scenario.get("deletion_rule") else []
                        self._send_json(200, rules)
                        return
                    self._send_json(404, {"message": "Not Found"})

                def do_DELETE(self) -> None:
                    path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
                    records.append(("DELETE", path))
                    if path == "/repos/StreamScapeTV/example/git/refs/heads/feature/cleanup":
                        self.send_response(204)
                        self.end_headers()
                        return
                    self._send_json(404, {"message": "Not Found"})

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                script = delete_python.replace(
                    'api_root = "https://api.github.com"',
                    f'api_root = "http://127.0.0.1:{server.server_port}"',
                )
                completed = run_embedded(
                    script,
                    {
                        "TARGET_REPOSITORY": "StreamScapeTV/example",
                        "TARGET_BRANCH": "feature/cleanup",
                        "EXPECTED_HEAD": expected_head if deletion_source == "agent-state" else "",
                        "DELETION_SOURCE": deletion_source,
                        "TARGET_TOKEN": "masked-test-token",
                        "GITHUB_OUTPUT": os.devnull,
                    },
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            return completed, records

        success, records = run_delete_case()
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(
            [record for record in records if record[0] == "DELETE"],
            [("DELETE", "/repos/StreamScapeTV/example/git/refs/heads/feature/cleanup")],
        )

        already_absent, absent_records = run_delete_case(missing=True)
        self.assertEqual(already_absent.returncode, 0, already_absent.stderr)
        self.assertFalse(any(method == "DELETE" for method, _ in absent_records))

        event_success, event_records = run_delete_case(
            deletion_source="github-delete-event", missing=True
        )
        self.assertEqual(event_success.returncode, 0, event_success.stderr)
        self.assertFalse(any(method == "DELETE" for method, _ in event_records))

        event_refused, event_refused_records = run_delete_case(
            deletion_source="github-delete-event", missing=False
        )
        self.assertNotEqual(event_refused.returncode, 0)
        self.assertFalse(any(method == "DELETE" for method, _ in event_refused_records))

        for scenario in (
            {"default_branch": "feature/cleanup"},
            {"second_default_branch": "feature/cleanup"},
            {"head": "b" * 40},
            {"second_head": "b" * 40},
            {"protected": True},
            {"deletion_rule": True},
            {"deletion_rule_page_two": True},
        ):
            refused, refused_records = run_delete_case(**scenario)
            self.assertNotEqual(refused.returncode, 0, scenario)
            self.assertFalse(any(method == "DELETE" for method, _ in refused_records), scenario)

    def test_fixed_profiles_replace_arbitrary_command_transport(self) -> None:
        forbidden = ("prepare_command", "build_command", "test_command", "release_command", "bash -lc")
        for name in ("apple", "android", "python", "node", "flutter"):
            text = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
            for value in forbidden:
                self.assertNotIn(value, text)
            self.assertIn("Scrub configured CI secrets from private log", text)
            self.assertIn("steps.scrub.outcome == 'success'", text)
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        for value in forbidden[:-1]:
            self.assertNotIn(value, dispatch)

    def test_android_dependency_cache_uses_github_default_branch_writer_policy(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text())
        steps = workflow["jobs"]["ci"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        scope = by_name["Resolve IPTV Android default-branch cache scope"]
        restore = by_name["Restore IPTV Android default-branch dependency cache"]
        save = by_name["Save IPTV Android default-branch dependency cache"]
        expected_paths = "~/.gradle/wrapper\n~/.gradle/caches/modules-2\n"

        script = scope["run"]
        self.assertIn("StreamScapeTV/iptv-android", script)
        self.assertIn("https://api.github.com/repos/${SOURCE_REPOSITORY}", script)
        self.assertIn('get("default_branch", "")', script)
        self.assertIn('test "${source_ref}" = "${default_branch}"', script)
        self.assertIn('test "${checkout_branch}" = "${default_branch}"', script)
        self.assertNotIn('test "${source_ref}" = develop', script)
        self.assertIn('source_ref="${source_ref#refs/heads/}"', script)
        self.assertIn("inputs.repository || github.repository", scope["env"]["SOURCE_REPOSITORY"])
        self.assertIn("inputs.ref", scope["env"]["REQUESTED_REF"])
        self.assertEqual(restore["if"], "${{ steps.android_default_cache_scope.outputs.enabled == 'true' }}")
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(save["uses"], "actions/cache/save@v4")
        self.assertEqual(restore["with"]["path"], expected_paths)
        self.assertEqual(save["with"]["path"], expected_paths)
        self.assertIn("iptv-android-default-gradle-deps-v2-", restore["with"]["key"])
        self.assertNotIn("inputs.ref", restore["with"]["key"])
        self.assertNotIn("github.ref", restore["with"]["key"])
        self.assertEqual(save["with"]["key"], "${{ steps.gradle_dependency_cache.outputs.cache-primary-key }}")
        self.assertIn("steps.commands.outcome == 'success'", save["if"])
        finish = by_name["Finish Agent State run"]
        self.assertIn("steps.gradle_dependency_cache_save.outcome == 'success'", finish["with"]["status"])
        self.assertIn("steps.gradle_dependency_cache_save.outcome == 'skipped'", finish["with"]["status"])

    def test_android_owner_profiles_and_gitops_retirement_are_explicit(self) -> None:
        android = (ROOT / ".github/workflows/android.yml").read_text()
        for profile in ("smoke)", "compile)", "unit)", "targeted-unit)", "targeted-tests)", "lint)", "assemble)", "full)", "release)"):
            self.assertIn(profile, android)
        self.assertIn("CIW_MAVEN_PACKAGE_READ_TOKEN", android)
        self.assertIn("compileDebugKotlin testDebugUnitTest lintDebug assembleDebug", android)
        self.assertFalse((ROOT / ".github/workflows/gitops.yml").exists())
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertNotIn("workflow_key == 'validation.gitops'", dispatch)

    def test_product_release_uses_central_private_registry(self) -> None:
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        release = (ROOT / ".github/workflows/public-native-image-chart.yml").read_text()
        self.assertIn("workflow_key == 'release.public-native-image-chart'", dispatch)
        self.assertIn('uses: ./.github/workflows/public-native-image-chart.yml', dispatch)
        self.assertIn('repository: ${{ needs.request.outputs.repository }}', dispatch)
        self.assertIn('ref: ${{ needs.request.outputs.ref }}', dispatch)
        self.assertIn('repository: ${{ inputs.repository }}', release)
        self.assertIn('ref: ${{ inputs.ref }}', release)
        self.assertIn('test "${{ inputs.publish_latest_image }}" = false', release)
        self.assertIn('REGISTRY: git.faruqi.dev', release)
        self.assertIn('FORGEJO_REGISTRY_USERNAME', release)
        self.assertIn('FORGEJO_REGISTRY_TOKEN', release)
        self.assertIn('actions/private-git@main', release)
        self.assertIn('Authenticated private registry read-back', release)
        self.assertNotIn('ghcr.io', release)
        self.assertIn('phase: start', release)
        self.assertIn('phase: finish', release)
        self.assertIn('actions/google-drive@main', release)
        self.assertNotIn('kubectl', release)
        self.assertNotIn('helm upgrade', release)

    def test_source_snapshot_reuses_drive_helper_and_updates_manifest_in_place(self) -> None:
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("workflow_key == 'source.snapshot'", dispatch)
        self.assertIn("git -C source archive --format=zip", dispatch)
        self.assertIn("GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID", dispatch)
        self.assertIn("Resolve durable Drive repository folder", dispatch)
        self.assertIn("Google Drive repository folder ID", dispatch)
        self.assertEqual(dispatch.count("repository_folder_id: ${{ steps.drive_repository.outputs.folder_id }}"), 3)
        self.assertIn("Google Drive repository folder ID: `1--JcV6RK8jdIIP3ONWw420QDVpNTQ7L8`", agents)
        self.assertIn("file_name: source.zip", dispatch)
        self.assertEqual(dispatch.count("file_name: manifest.json"), 2)
        for key in (
            '"repository_name"',
            '"archive_format": "zip"',
            '"archive_format_version": 1',
            '"resolved_source_sha"',
            '"tree_sha"',
            '"source_zip_sha256"',
            '"source_zip_size_bytes"',
            '"manifest_file_id"',
            '"source_zip_file_id"',
            '"folder_id"',
        ):
            self.assertIn(key, dispatch)
        self.assertIn('test "${CREATED_ID}" = "${UPDATED_ID}"', dispatch)
        self.assertIn("Clean snapshot workspace", dispatch)

        workflow = yaml.safe_load(dispatch)
        steps = workflow["jobs"]["source_snapshot"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        names = [step.get("name") for step in steps]
        identity = by_name["Resolve observed source SHA"]
        record = by_name["Record observed source SHA"]
        snapshot = by_name["Create exact tracked-source snapshot"]
        finish = by_name["Finish Agent State run"]
        self.assertIn('source_sha="$(git -C source rev-parse HEAD)"', identity["run"])
        self.assertEqual(record["with"]["phase"], "observe-source")
        self.assertEqual(record["with"]["observed_source_sha"], "${{ steps.source_identity.outputs.source_sha }}")
        self.assertNotIn("github.sha", record["with"]["observed_source_sha"])
        self.assertEqual(snapshot["env"]["OBSERVED_SOURCE_SHA"], "${{ steps.source_identity.outputs.source_sha }}")
        self.assertIn('source_sha="${OBSERVED_SOURCE_SHA}"', snapshot["run"])
        self.assertLess(names.index("Check out requested source"), names.index("Resolve observed source SHA"))
        self.assertLess(names.index("Resolve observed source SHA"), names.index("Record observed source SHA"))
        self.assertLess(names.index("Record observed source SHA"), names.index("Create exact tracked-source snapshot"))
        self.assertEqual(finish["if"], "${{ always() }}")




if __name__ == "__main__":
    unittest.main()
