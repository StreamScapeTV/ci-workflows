from __future__ import annotations

import io
import json
import unittest
import urllib.error

from ci_workflows.private_source import PrivateSourceError, resolve_private_branch


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class PrivateSourceTests(unittest.TestCase):
    def test_resolves_branch_to_exact_sha_with_fixed_organization(self) -> None:
        expected_sha = "a" * 40
        seen: dict[str, object] = {}

        def opener(request: object, timeout: int) -> _Response:
            seen["url"] = request.full_url  # type: ignore[attr-defined]
            seen["authorization"] = request.get_header("Authorization")  # type: ignore[attr-defined]
            seen["timeout"] = timeout
            return _Response(json.dumps({"sha": expected_sha}).encode("utf-8"))

        actual = resolve_private_branch(
            repository_name="iptv-apple",
            branch="feature/apple-test",
            token="secret-token",
            opener=opener,
        )

        self.assertEqual(actual, expected_sha)
        self.assertEqual(
            seen["url"],
            "https://api.github.com/repos/StreamScapeTV/iptv-apple/commits/feature%2Fapple-test",
        )
        self.assertEqual(seen["authorization"], "Bearer secret-token")
        self.assertEqual(seen["timeout"], 30)

    def test_rejects_repository_outside_fixed_name_shape(self) -> None:
        for repository in ("StreamScapeTV/iptv-apple", "../iptv-apple", " iptv-apple"):
            with self.subTest(repository=repository), self.assertRaises(PrivateSourceError) as raised:
                resolve_private_branch(
                    repository_name=repository,
                    branch="develop",
                    token="secret-token",
                )
            self.assertEqual(raised.exception.code, "invalid_repository_name")

    def test_rejects_unsafe_branch_shapes_before_network_access(self) -> None:
        for branch in ("refs/heads/develop", "../develop", "bad branch", "feature//test", "topic.lock"):
            with self.subTest(branch=branch), self.assertRaises(PrivateSourceError) as raised:
                resolve_private_branch(
                    repository_name="iptv-apple",
                    branch=branch,
                    token="secret-token",
                )
            self.assertEqual(raised.exception.code, "invalid_branch")

    def test_requires_token_before_network_access(self) -> None:
        with self.assertRaises(PrivateSourceError) as raised:
            resolve_private_branch(
                repository_name="iptv-apple",
                branch="develop",
                token="",
            )
        self.assertEqual(raised.exception.code, "github_token_required")

    def test_http_failure_is_sanitized(self) -> None:
        def opener(_request: object, timeout: int) -> _Response:
            self.assertEqual(timeout, 30)
            raise urllib.error.HTTPError(
                "https://example.invalid/private",
                403,
                "forbidden private branch",
                {},
                None,
            )

        with self.assertRaises(PrivateSourceError) as raised:
            resolve_private_branch(
                repository_name="iptv-apple",
                branch="develop",
                token="secret-token",
                opener=opener,
            )
        self.assertEqual(raised.exception.code, "github_api_http_403")
        self.assertNotIn("iptv-apple", str(raised.exception))
        self.assertNotIn("develop", str(raised.exception))

    def test_invalid_response_sha_fails_closed(self) -> None:
        def opener(_request: object, timeout: int) -> _Response:
            self.assertEqual(timeout, 30)
            return _Response(json.dumps({"sha": "main"}).encode("utf-8"))

        with self.assertRaises(PrivateSourceError) as raised:
            resolve_private_branch(
                repository_name="iptv-apple",
                branch="develop",
                token="secret-token",
                opener=opener,
            )
        self.assertEqual(raised.exception.code, "invalid_resolved_sha")


if __name__ == "__main__":
    unittest.main()
