from __future__ import annotations

import base64
import gzip
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/ci/protected_deployed_conformance.py"


def _gzip_b64(value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def _project_state(repository: str = "ExampleOrg/service-backend") -> dict:
    return {
        "repository_ci": {
            "schemaVersion": 1,
            "repository": repository,
            "capabilities": ["private_network", "protected_deployed_conformance"],
        },
        "protected_deployed_conformance": {
            "schemaVersion": 2,
            "repository": repository,
            "operation": "full",
            "entrypoint": {
                "kind": "python",
                "path": "scripts/protected_probe.py",
            },
            "environmentProjection": {
                "httpsTarget": "APP_API_BASE",
                "wssTarget": "APP_WS_URL",
                "httpScenarioPath": "APP_HTTP_SCENARIO",
                "websocketScenarioPath": "APP_WS_SCENARIO",
                "setupCredential": "APP_ADMIN_TOKEN",
                "releaseVersion": "APP_RELEASE_VERSION",
                "runId": "APP_PROTECTED_RUN_ID",
                "liveFlag": "APP_LIVE_E2E",
            },
            "identity": {
                "releaseVersion": "3.2.3",
            },
            "reset": {
                "request": {
                    "method": "PATCH",
                    "pathTemplate": "/admin/users/{subject}/subscription",
                    "subject": {
                        "scenario": "websocket",
                        "jsonPointer": "/user_id",
                    },
                    "authorization": "setupCredential",
                    "body": {"status": "inactive"},
                    "expectedStatus": 200,
                },
                "verify": {
                    "path": "/api/v1/subscription/entitlements",
                    "authorization": {
                        "scenario": "websocket",
                        "jsonPointer": "/devices/observer/access_token",
                    },
                    "expectedStatus": 200,
                    "jsonPointer": "/is_entitled",
                    "equals": False,
                },
            },
        },
    }


class _ResetHandler(BaseHTTPRequestHandler):
    state = {"active": True, "patches": 0, "reads": 0}

    def log_message(self, *_args):
        return

    def do_PATCH(self):
        if self.path != "/admin/users/synthetic-user/subscription":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Bearer setup-credential-1234567890":
            self.send_response(401)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if body != {"status": "inactive"}:
            self.send_response(400)
            self.end_headers()
            return
        type(self).state["active"] = False
        type(self).state["patches"] += 1
        payload = b'{"message":"reset"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path != "/api/v1/subscription/entitlements":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Bearer observer-access-token-123456":
            self.send_response(401)
            self.end_headers()
            return
        type(self).state["reads"] += 1
        payload = json.dumps({"is_entitled": type(self).state["active"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ProtectedDeployedConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        _ResetHandler.state = {"active": True, "patches": 0, "reads": 0}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ResetHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _materialize(self, root: Path, *, state: dict | None = None) -> tuple[subprocess.CompletedProcess[str], Path]:
        state_path = root / "state.json"
        state_path.write_text(json.dumps(state or _project_state()), encoding="utf-8")
        output_root = root / "protected"
        http_scenario = {
            "schema_version": 1,
            "namespace": "conformance-http-fixture",
            "setup": [],
            "cases": [{"name": "ordinary-result", "outcome": "passed"}],
            "disabled_cases": [],
            "cleanup": [],
        }
        ws_scenario = {
            "schema_version": 1,
            "namespace": "conformance-ws-fixture",
            "user_id": "synthetic-user",
            "devices": {
                "observer": {
                    "device_id": "observer-device",
                    "access_token": "observer-access-token-123456",
                }
            },
            "cases": [],
            "cleanup": [],
            "cleanup_snapshot": {"device": "observer"},
        }
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "materialize",
                "--project-state",
                str(state_path),
                "--repository",
                "ExampleOrg/service-backend",
                "--operation",
                "full",
                "--run-id",
                "11111111-1111-4111-8111-111111111111",
                "--output-root",
                str(output_root),
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "CI_PROTECTED_DEPLOYED_HTTPS_TARGET": f"http://127.0.0.1:{self.port}",
                "CI_PROTECTED_DEPLOYED_WSS_TARGET": f"ws://127.0.0.1:{self.port}/api/v1/realtime",
                "CI_PROTECTED_DEPLOYED_HTTP_SCENARIO_GZIP_BASE64": _gzip_b64(http_scenario),
                "CI_PROTECTED_DEPLOYED_WEBSOCKET_SCENARIO_GZIP_BASE64": _gzip_b64(ws_scenario),
                "CI_PROTECTED_DEPLOYED_SETUP_CREDENTIAL": "setup-credential-1234567890",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        return result, output_root

    def _source(self, root: Path, *, fail: bool = False) -> Path:
        source = root / "source"
        script = source / "scripts" / "protected_probe.py"
        script.parent.mkdir(parents=True)
        body = f'''from __future__ import annotations\nimport json\nimport os\nfrom pathlib import Path\nimport stat\nassert os.environ["APP_LIVE_E2E"] == "1"\nassert os.environ["APP_RELEASE_VERSION"] == "3.2.3"\nfor name in ("APP_HTTP_SCENARIO", "APP_WS_SCENARIO"):\n    path = Path(os.environ[name])\n    assert path.is_file() and not path.is_symlink()\n    assert stat.S_IMODE(path.stat().st_mode) == 0o600\nassert os.environ["APP_ADMIN_TOKEN"] == "setup-credential-1234567890"\nif {fail!r}:\n    raise SystemExit(7)\nprint(json.dumps({{"schema_version": 1, "outcome": "passed", "release": os.environ["APP_RELEASE_VERSION"], "run_id": os.environ["APP_PROTECTED_RUN_ID"]}}, sort_keys=True, separators=(",", ":")))\n'''
        script.write_text(body, encoding="utf-8")
        version = subprocess.run(
            [sys.executable, "-c", "import platform; print(platform.python_version())"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        (source / ".python-version").write_text(version + "\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        subprocess.run(["git", "add", "scripts/protected_probe.py", ".python-version"], cwd=source, check=True)
        return source

    def test_materialize_creates_only_private_bounded_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result, output_root = self._materialize(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            self.assertEqual(response["root"], str(output_root))
            for name in (
                "http-scenario.json",
                "websocket-scenario.json",
                "child-environment.json",
                "metadata.json",
            ):
                path = output_root / name
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            env_document = json.loads((output_root / "child-environment.json").read_text())
            self.assertEqual(env_document["APP_API_BASE"], f"http://127.0.0.1:{self.port}")
            self.assertEqual(env_document["APP_ADMIN_TOKEN"], "setup-credential-1234567890")
            self.assertEqual(env_document["APP_RELEASE_VERSION"], "3.2.3")
            self.assertNotIn("APP_RELEASE_SOURCE_SHA", env_document)
            self.assertNotIn("APP_RELEASE_IMAGE_DIGEST", env_document)
            self.assertNotIn("APP_RELEASE_CHART_DIGEST", env_document)
            self.assertNotIn("APP_DEPLOYED_IMAGE_DIGEST", env_document)
            self.assertNotIn("APP_DEPLOYED_CHART_DIGEST", env_document)
            self.assertNotIn("APP_ENVIRONMENT_SHA256", env_document)
            metadata = json.loads((output_root / "metadata.json").read_text())
            rendered = json.dumps(metadata)
            self.assertNotIn(f"127.0.0.1:{self.port}", rendered)
            self.assertNotIn("setup-credential-1234567890", rendered)
            self.assertEqual(metadata["capability"], "protected_deployed_conformance")

    def test_materialize_rejects_unsafe_environment_projection_and_missing_material(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = _project_state()
            state["protected_deployed_conformance"]["environmentProjection"]["httpsTarget"] = "CI_SECRET_ESCAPE"
            result, _ = self._materialize(root, state=state)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("overlaps Central-owned state", result.stderr)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_path = root / "state.json"
            state_path.write_text(json.dumps(_project_state()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "materialize",
                    "--project-state",
                    str(state_path),
                    "--repository",
                    "ExampleOrg/service-backend",
                    "--operation",
                    "full",
                    "--run-id",
                    "11111111-1111-4111-8111-111111111111",
                    "--output-root",
                    str(root / "protected"),
                ],
                cwd=ROOT,
                env={**os.environ},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("material is absent", result.stderr)

    def test_materialize_rejects_superseded_digest_identity_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = _project_state()
            state["protected_deployed_conformance"]["schemaVersion"] = 1
            state["protected_deployed_conformance"]["environmentProjection"]["releaseImageDigest"] = "APP_RELEASE_IMAGE_DIGEST"
            state["protected_deployed_conformance"]["identity"]["releaseImageDigest"] = "sha256:" + "1" * 64
            result, output_root = self._materialize(root, state=state)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("descriptor version is invalid", result.stderr)
            self.assertFalse(output_root.exists())

    def test_run_executes_tracked_certifier_writes_bounded_evidence_and_resets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            materialized, config_root = self._materialize(root)
            self.assertEqual(materialized.returncode, 0, materialized.stderr)
            source = self._source(root)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            result = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "run",
                    "--source-root",
                    str(source),
                    "--config-root",
                    str(config_root),
                    "--repository",
                    "ExampleOrg/service-backend",
                    "--operation",
                    "full",
                    "--run-id",
                    "11111111-1111-4111-8111-111111111111",
                    "--log-dir",
                    str(log_dir),
                    "--artifact-dir",
                    str(artifact_dir),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(config_root.exists())
            self.assertEqual(_ResetHandler.state, {"active": False, "patches": 1, "reads": 1})
            evidence = json.loads((artifact_dir / "protected-deployed-conformance.json").read_text())
            self.assertEqual(evidence["outcome"], "passed")
            self.assertEqual(evidence["release"], "3.2.3")
            reset = json.loads((artifact_dir / "protected-deployed-reset.json").read_text())
            self.assertEqual(reset["outcome"], "passed")
            log_text = (log_dir / "protected-deployed-conformance.txt").read_text()
            self.assertNotIn("setup-credential", log_text)
            self.assertNotIn(f"127.0.0.1:{self.port}", log_text)

    def test_run_still_requires_reset_when_certifier_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            materialized, config_root = self._materialize(root)
            self.assertEqual(materialized.returncode, 0, materialized.stderr)
            source = self._source(root, fail=True)
            result = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "run",
                    "--source-root",
                    str(source),
                    "--config-root",
                    str(config_root),
                    "--repository",
                    "ExampleOrg/service-backend",
                    "--operation",
                    "full",
                    "--run-id",
                    "11111111-1111-4111-8111-111111111111",
                    "--log-dir",
                    str(root / "logs"),
                    "--artifact-dir",
                    str(root / "artifacts"),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("entrypoint failed with exit 7", result.stderr)
            self.assertEqual(_ResetHandler.state, {"active": False, "patches": 1, "reads": 1})
            self.assertFalse(config_root.exists())
            self.assertTrue((root / "artifacts" / "protected-deployed-reset.json").is_file())


if __name__ == "__main__":
    unittest.main()
