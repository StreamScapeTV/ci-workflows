from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest import mock

import yaml

from ci_workflows.d1_diagnostics import (
    D1DiagnosticError,
    build_request,
    normalize_diagnostics,
    persist_from_environment,
    persist_request,
)

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/persist-ci-diagnostics/action.yml"
CONTRACT = ROOT / "contracts/ci-diagnostics.json"
DOC = ROOT / "docs/workflows/ci-diagnostics.md"
CIW = ROOT / "scripts/ci/ciw.py"

CI_RUN_ID = "11111111-2222-4333-8444-555555555555"
DATABASE_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
NOW = datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, value: object, status: int = 200) -> None:
        self.status = status
        self._raw = json.dumps(value, separators=(",", ":")).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self) -> int:
        return self.status

    def read(self, maximum: int = -1) -> bytes:
        return self._raw if maximum < 0 else self._raw[:maximum]


class CapturingD1Opener:
    def __init__(self, *, readback_overrides: dict[str, object] | None = None) -> None:
        self.request = None
        self.timeout = None
        self.payload = None
        self.readback_overrides = dict(readback_overrides or {})

    def __call__(self, request, timeout: int):
        self.request = request
        self.timeout = timeout
        self.payload = json.loads(request.data.decode("utf-8"))
        batch = self.payload["batch"]
        params = batch[3]["params"]
        row = {
            "diagnostic_key": params[0],
            "ci_run_id": params[1],
            "run_attempt": int(params[2]),
            "github_run_id": int(params[3]),
            "project_key": params[4],
            "repository": params[5],
            "ref": params[6],
            "is_tag": int(params[7]),
            "workflow_key": params[8],
            "profile": params[9],
            "status": params[10],
            "diagnostics_json": params[11],
            "diagnostics_sha256": params[12],
            "diagnostic_count": int(params[13]),
            "expires_at": params[16],
        }
        row.update(self.readback_overrides)
        return Response(
            {
                "success": True,
                "errors": [],
                "messages": [],
                "result": [
                    {"success": True, "results": []},
                    {"success": True, "results": []},
                    {"success": True, "results": []},
                    {"success": True, "results": []},
                    {"success": True, "results": [row]},
                ],
            }
        )


def sample_json() -> str:
    return json.dumps(
        [
            {
                "severity": "warning",
                "code": "swift.warning",
                "stage": "apple-build",
                "message": "Deprecated API used",
            },
            {
                "severity": "error",
                "code": "compile.failed",
                "stage": "apple-test",
                "message": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz token=supersecret",
            },
        ],
        separators=(",", ":"),
    )


def sample_request():
    return build_request(
        ci_run_id=CI_RUN_ID,
        run_attempt="2",
        github_run_id="32840000001",
        project_key="iptv-apple",
        repository="OtherOrg/private-apple",
        ref="develop",
        is_tag="false",
        workflow_key="validation.apple",
        profile="host",
        status="failed",
        diagnostics_json=sample_json(),
    )


class NormalizationTests(unittest.TestCase):
    def test_warning_error_payload_is_canonical_and_secret_redacted(self) -> None:
        rows, canonical, digest = normalize_diagnostics(sample_json())
        self.assertEqual(2, len(rows))
        self.assertEqual("warning", rows[0]["severity"])
        self.assertEqual("error", rows[1]["severity"])
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", canonical)
        self.assertNotIn("supersecret", canonical)
        self.assertIn("<redacted>", canonical)
        self.assertEqual(64, len(digest))
        rows_again, canonical_again, digest_again = normalize_diagnostics(sample_json())
        self.assertEqual(rows, rows_again)
        self.assertEqual(canonical, canonical_again)
        self.assertEqual(digest, digest_again)

    def test_message_is_normalized_to_one_line_and_url_userinfo_is_redacted(self) -> None:
        raw = json.dumps(
            [
                {
                    "severity": "error",
                    "code": "network.failed",
                    "message": "line one\nline two https://user:password@example.invalid/path",
                }
            ]
        )
        rows, _canonical, _digest = normalize_diagnostics(raw)
        message = rows[0]["message"]
        self.assertNotIn("\n", message)
        self.assertIn("line one line two", message)
        self.assertIn("https://<redacted>@example.invalid/path", message)
        self.assertNotIn("password", message)

    def test_info_debug_unknown_fields_and_duplicate_keys_fail_closed(self) -> None:
        for severity in ("info", "debug"):
            with self.subTest(severity=severity), self.assertRaisesRegex(
                D1DiagnosticError, "invalid_diagnostic_severity"
            ):
                normalize_diagnostics(
                    json.dumps(
                        [{"severity": severity, "code": "ci.note", "message": "not copied"}]
                    )
                )
        with self.assertRaisesRegex(D1DiagnosticError, "invalid_diagnostic"):
            normalize_diagnostics(
                json.dumps(
                    [{"severity": "error", "code": "ci.failed", "message": "x", "raw_log": "no"}]
                )
            )
        with self.assertRaisesRegex(D1DiagnosticError, "diagnostic_duplicate_key"):
            normalize_diagnostics(
                '[{"severity":"error","severity":"warning","code":"ci.failed","message":"x"}]'
            )

    def test_record_count_and_message_size_are_bounded(self) -> None:
        row = {"severity": "warning", "code": "ci.warning", "message": "x"}
        with self.assertRaisesRegex(D1DiagnosticError, "too_many_diagnostics"):
            normalize_diagnostics(json.dumps([row] * 65))
        with self.assertRaisesRegex(D1DiagnosticError, "diagnostic_message_too_large"):
            normalize_diagnostics(
                json.dumps(
                    [{"severity": "error", "code": "ci.failed", "message": "x" * 2049}]
                )
            )

    def test_request_identity_uses_ref_and_tag_not_source_sha(self) -> None:
        request = sample_request()
        self.assertEqual("OtherOrg/private-apple", request.repository)
        self.assertEqual("develop", request.ref)
        self.assertFalse(request.is_tag)
        self.assertEqual(f"ci/{CI_RUN_ID}/2", request.diagnostic_key)
        self.assertFalse(hasattr(request, "source_sha"))
        tag = build_request(
            ci_run_id="21111111-2222-4333-8444-555555555555",
            run_attempt=1,
            github_run_id=2,
            project_key="ci-workflows",
            repository="StreamScapeTV/ci-workflows",
            ref="ci-broker-1.0.5",
            is_tag=True,
            workflow_key="broker.release",
            profile="release",
            status="succeeded",
            diagnostics_json="[]",
        )
        self.assertTrue(tag.is_tag)
        self.assertEqual("ci-broker-1.0.5", tag.ref)


class D1TransportTests(unittest.TestCase):
    def test_fixed_parameterized_batch_persists_and_reads_back_exact_identity(self) -> None:
        request = sample_request()
        opener = CapturingD1Opener()
        receipt = persist_request(
            request,
            account_id="0123456789abcdef0123456789abcdef",
            database_id=DATABASE_ID,
            api_token="cloudflare_test_token_1234567890",
            opener=opener,
            now=NOW,
        )
        self.assertEqual("uploaded", receipt.diagnostic_status)
        self.assertEqual(request.diagnostic_key, receipt.diagnostic_key)
        self.assertEqual(request.diagnostics_sha256, receipt.diagnostic_sha256)
        self.assertEqual(2, receipt.diagnostic_count)
        assert opener.request is not None
        self.assertEqual(
            f"https://api.cloudflare.com/client/v4/accounts/0123456789abcdef0123456789abcdef/d1/database/{DATABASE_ID}/query",
            opener.request.full_url,
        )
        self.assertEqual("POST", opener.request.get_method())
        self.assertEqual(
            "Bearer cloudflare_test_token_1234567890",
            opener.request.get_header("Authorization"),
        )
        assert opener.payload is not None
        batch = opener.payload["batch"]
        self.assertEqual(5, len(batch))
        self.assertIn("CREATE TABLE IF NOT EXISTS ci_diagnostics", batch[0]["sql"])
        self.assertIn("CREATE INDEX IF NOT EXISTS", batch[1]["sql"])
        self.assertEqual("DELETE FROM ci_diagnostics WHERE expires_at <= ?", batch[2]["sql"])
        self.assertIn("INSERT INTO ci_diagnostics", batch[3]["sql"])
        self.assertIn("ON CONFLICT(diagnostic_key) DO UPDATE", batch[3]["sql"])
        self.assertIn("WHERE diagnostic_key = ?", batch[4]["sql"])
        self.assertTrue(all(isinstance(value, str) for value in batch[2]["params"]))
        self.assertTrue(all(isinstance(value, str) for value in batch[3]["params"]))
        self.assertTrue(all(isinstance(value, str) for value in batch[4]["params"]))
        sql_text = "\n".join(item["sql"] for item in batch)
        self.assertNotIn("OtherOrg/private-apple", sql_text)
        self.assertNotIn("Deprecated API used", sql_text)
        self.assertNotIn("cloudflare_test_token", json.dumps(opener.payload))

    def test_readback_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(D1DiagnosticError, "d1_readback_mismatch"):
            persist_request(
                sample_request(),
                account_id="0123456789abcdef0123456789abcdef",
                database_id=DATABASE_ID,
                api_token="cloudflare_test_token_1234567890",
                opener=CapturingD1Opener(readback_overrides={"repository": "wrong/repo"}),
                now=NOW,
            )

    def test_http_failure_does_not_surface_cloudflare_body(self) -> None:
        detail = b"private cloudflare failure detail token=do-not-leak"
        error = urllib.error.HTTPError(
            "https://api.cloudflare.com/",
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(detail),
        )
        with self.assertRaises(D1DiagnosticError) as raised:
            persist_request(
                sample_request(),
                account_id="0123456789abcdef0123456789abcdef",
                database_id=DATABASE_ID,
                api_token="cloudflare_test_token_1234567890",
                opener=mock.Mock(side_effect=error),
                now=NOW,
            )
        self.assertEqual("d1_http_403", raised.exception.code)
        self.assertNotIn("private", str(raised.exception))
        self.assertNotIn("do-not-leak", str(raised.exception))

    def test_environment_adapter_outputs_receipt_only(self) -> None:
        opener = CapturingD1Opener()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.touch()
            environment = {
                "INPUT_CI_RUN_ID": CI_RUN_ID,
                "INPUT_PROJECT_KEY": "iptv-apple",
                "INPUT_REPOSITORY": "OtherOrg/private-apple",
                "INPUT_REF": "develop",
                "INPUT_IS_TAG": "false",
                "INPUT_WORKFLOW_KEY": "validation.apple",
                "INPUT_PROFILE": "host",
                "INPUT_STATUS": "failed",
                "INPUT_DIAGNOSTICS_JSON": sample_json(),
                "GITHUB_RUN_ATTEMPT": "2",
                "GITHUB_RUN_ID": "32840000001",
                "CIW_D1_ACCOUNT_ID": "0123456789abcdef0123456789abcdef",
                "CIW_D1_DATABASE_ID": DATABASE_ID,
                "CIW_D1_API_TOKEN": "cloudflare_test_token_1234567890",
                "GITHUB_OUTPUT": str(output),
            }
            receipt = persist_from_environment(environment, opener=opener, now=NOW)
            text = output.read_text()
        self.assertEqual("uploaded", receipt.diagnostic_status)
        self.assertIn(f"diagnostic_key=ci/{CI_RUN_ID}/2", text)
        self.assertIn("diagnostic_status=uploaded", text)
        self.assertIn("diagnostic_sha256=", text)
        self.assertIn("diagnostic_count=2", text)
        self.assertNotIn("Deprecated API used", text)
        self.assertNotIn("cloudflare_test_token", text)
        self.assertNotIn("diagnostics_json", text)


class SourceContractTests(unittest.TestCase):
    def test_action_is_thin_and_has_no_credential_or_sql_inputs(self) -> None:
        action = yaml.safe_load(ACTION.read_text())
        self.assertEqual("composite", action["runs"]["using"])
        self.assertEqual(1, len(action["runs"]["steps"]))
        self.assertEqual(
            {
                "ci_run_id",
                "project_key",
                "repository",
                "ref",
                "is_tag",
                "workflow_key",
                "profile",
                "status",
                "diagnostics_json",
            },
            set(action["inputs"]),
        )
        self.assertEqual(
            {"diagnostic_key", "diagnostic_status", "diagnostic_sha256", "diagnostic_count"},
            set(action["outputs"]),
        )
        text = ACTION.read_text()
        self.assertIn("scripts/ci/ciw.py", text)
        self.assertIn("diagnostics persist", text)
        for forbidden in (
            "api_token:",
            "account_id:",
            "database_id:",
            "sql:",
            "endpoint:",
            "curl ",
            "wrangler",
        ):
            self.assertNotIn(forbidden, text)

    def test_contract_and_docs_keep_logs_out_of_agent_state(self) -> None:
        contract = json.loads(CONTRACT.read_text())
        self.assertEqual("cloudflare-d1", contract["store"])
        self.assertEqual(24, contract["retention_hours"])
        self.assertEqual(["warning", "error"], contract["severities"])
        self.assertTrue(contract["agent_state"]["raw_logs_forbidden"])
        self.assertTrue(contract["agent_state"]["diagnostics_json_forbidden"])
        self.assertEqual(
            "persist-diagnostics-before-terminal-transition",
            contract["agent_state"]["terminal_order"],
        )
        self.assertFalse(contract["credentials"]["caller_selectable"])
        document = DOC.read_text().lower()
        self.assertIn("github actions", document)
        self.assertIn("agent state never stores", document)
        self.assertIn("warning", document)
        self.assertIn("error", document)
        self.assertIn("parameter", document)
        self.assertIn("24 hours", document)

    def test_ciw_gateway_is_the_only_cli_adapter(self) -> None:
        text = CIW.read_text()
        self.assertIn("from ci_workflows.d1_diagnostics import main as d1_diagnostics_main", text)
        self.assertIn('arguments[:1] == ["diagnostics"]', text)
        self.assertFalse((ROOT / "scripts/ci/d1_diagnostics.py").exists())


if __name__ == "__main__":
    unittest.main()
