from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from ci_workflows.github_app_token import (
    GitHubAppRepositoryTokenClient,
    GitHubAppTokenError,
    issue_repository_token,
)

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/github-app-repository-token/action.yml"
CIW = ROOT / "scripts/ci/ciw.py"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nsynthetic-test-key-material\n-----END PRIVATE KEY-----"
SYNTHETIC_TOKEN = "ghs_" + "a" * 40
SYNTHETIC_STATELESS_TOKEN = (
    "ghs_12345_" + "a" * 180 + "." + "b" * 180 + "." + "c" * 180
)


class Response:
    def __init__(self, value: object, status: int) -> None:
        self.status = status
        self._raw = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._raw if amount < 0 else self._raw[:amount]

    def getcode(self) -> int:
        return self.status


class RecordingOpener:
    def __init__(self, token: str = SYNTHETIC_TOKEN) -> None:
        self.requests: list[dict[str, object]] = []
        self.token = token

    def __call__(self, request: object, timeout: int = 0) -> Response:
        assert hasattr(request, "full_url")
        assert hasattr(request, "get_method")
        raw = getattr(request, "data", None)
        self.requests.append(
            {
                "url": str(request.full_url),  # type: ignore[attr-defined]
                "method": request.get_method(),  # type: ignore[attr-defined]
                "headers": dict(request.header_items()),  # type: ignore[attr-defined]
                "body": None if raw is None else json.loads(bytes(raw).decode("utf-8")),
                "timeout": timeout,
            }
        )
        if str(request.full_url).endswith("/installation"):  # type: ignore[attr-defined]
            return Response({"id": 71}, 200)
        return Response({"token": self.token}, 201)


class GitHubAppRepositoryTokenClientTests(unittest.TestCase):
    def test_exact_external_repository_gets_single_repo_contents_read_token(self) -> None:
        opener = RecordingOpener()
        signed: dict[str, object] = {}

        def signer(payload: bytes, private_key: str) -> bytes:
            signed["payload"] = payload
            signed["private_key"] = private_key
            return b"synthetic-signature"

        client = GitHubAppRepositoryTokenClient(
            "12345",
            PRIVATE_KEY,
            opener=opener,
            signer=signer,
        )

        result = client.repository_contents_read_token("OtherOrg/private-app")

        self.assertEqual(result, SYNTHETIC_TOKEN)
        self.assertEqual(len(opener.requests), 2)
        installation, issued = opener.requests
        self.assertEqual(
            installation["url"],
            "https://api.github.com/repos/OtherOrg/private-app/installation",
        )
        self.assertEqual(installation["method"], "GET")
        self.assertEqual(
            issued["url"],
            "https://api.github.com/app/installations/71/access_tokens",
        )
        self.assertEqual(issued["method"], "POST")
        self.assertEqual(
            issued["body"],
            {
                "repositories": ["private-app"],
                "permissions": {"contents": "read"},
            },
        )
        self.assertEqual(installation["timeout"], 30)
        self.assertEqual(issued["timeout"], 30)
        self.assertEqual(signed["private_key"], PRIVATE_KEY)
        self.assertNotIn(PRIVATE_KEY, json.dumps(opener.requests))
        for request in opener.requests:
            headers = {
                str(key).lower(): str(value)
                for key, value in request["headers"].items()  # type: ignore[union-attr]
            }
            self.assertTrue(headers["authorization"].startswith("Bearer "))
            self.assertNotIn(PRIVATE_KEY, headers["authorization"])

    def test_stateless_installation_token_is_accepted(self) -> None:
        opener = RecordingOpener(SYNTHETIC_STATELESS_TOKEN)
        client = GitHubAppRepositoryTokenClient(
            1,
            PRIVATE_KEY,
            opener=opener,
            signer=lambda _payload, _key: b"signature",
        )

        result = client.repository_contents_read_token("OtherOrg/private-app")

        self.assertEqual(result, SYNTHETIC_STATELESS_TOKEN)
        self.assertGreater(len(result), 512)
        self.assertEqual(result.count("."), 2)

    def test_repository_and_returned_token_are_fail_closed(self) -> None:
        client = GitHubAppRepositoryTokenClient(
            1,
            PRIVATE_KEY,
            opener=RecordingOpener(),
            signer=lambda _payload, _key: b"signature",
        )
        with self.assertRaisesRegex(GitHubAppTokenError, "invalid_repository"):
            client.repository_contents_read_token("private-app")

        class InvalidTokenOpener(RecordingOpener):
            def __call__(self, request: object, timeout: int = 0) -> Response:
                if str(request.full_url).endswith("/installation"):  # type: ignore[attr-defined]
                    return Response({"id": 71}, 200)
                return Response({"token": "bad token"}, 201)

        invalid = GitHubAppRepositoryTokenClient(
            1,
            PRIVATE_KEY,
            opener=InvalidTokenOpener(),
            signer=lambda _payload, _key: b"signature",
        )
        with self.assertRaisesRegex(GitHubAppTokenError, "github_app_token_invalid"):
            invalid.repository_contents_read_token("OtherOrg/private-app")


class RepositoryTokenAdapterTests(unittest.TestCase):
    def test_environment_adapter_masks_before_exporting_token(self) -> None:
        captured: dict[str, object] = {}

        class Client:
            def __init__(self, app_id: object, private_key: object) -> None:
                captured["app_id"] = app_id
                captured["private_key"] = private_key

            def repository_contents_read_token(self, repository: object) -> str:
                captured["repository"] = repository
                return SYNTHETIC_TOKEN

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "CIW_GITHUB_APP_ID": "91",
                "CIW_GITHUB_APP_PRIVATE_KEY": PRIVATE_KEY,
                "INPUT_REPOSITORY": "OtherOrg/private-app",
                "GITHUB_OUTPUT": str(output),
            }
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = issue_repository_token(
                    environment,
                    client_factory=Client,  # type: ignore[arg-type]
                )

            self.assertEqual(result, SYNTHETIC_TOKEN)
            self.assertEqual(captured["app_id"], "91")
            self.assertEqual(captured["private_key"], PRIVATE_KEY)
            self.assertEqual(captured["repository"], "OtherOrg/private-app")
            self.assertEqual(stream.getvalue(), f"::add-mask::{SYNTHETIC_TOKEN}\n")
            self.assertEqual(output.read_text(encoding="utf-8"), f"token={SYNTHETIC_TOKEN}\n")

    def test_composite_action_has_no_secret_name_or_permission_surface(self) -> None:
        document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
        self.assertEqual(set(document["inputs"]), {"repository"})
        self.assertEqual(document["runs"]["using"], "composite")
        step = document["runs"]["steps"][0]
        self.assertEqual(
            set(step["env"]),
            {"PYTHONDONTWRITEBYTECODE", "INPUT_REPOSITORY"},
        )
        text = ACTION.read_text(encoding="utf-8")
        self.assertIn("scripts/ci/ciw.py", text)
        self.assertIn("github-app repository-token", text)
        self.assertNotIn("secrets.", text)
        for forbidden in ("app_id:", "private_key:", "permission:", "permissions:"):
            self.assertNotIn(forbidden, text)

    def test_ciw_gateway_exposes_only_the_named_github_app_adapter(self) -> None:
        text = CIW.read_text(encoding="utf-8")
        self.assertIn('arguments[:1] == ["github-app"]', text)
        self.assertIn("github_app_token_main(arguments[1:])", text)


if __name__ == "__main__":
    unittest.main()
