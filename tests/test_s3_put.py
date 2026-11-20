import datetime as dt
import io
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ci"))
import s3_put  # noqa: E402

ENV = {
    "S3_GITHUB_TRANSPORT_ACCESS_KEY": "AKIAIOSFODNN7EXAMPLE",
    "S3_GITHUB_TRANSPORT_SECRET_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}
NOW = dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc)


class Response:
    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeOpener:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "x", {}, io.BytesIO())


class SigV4Test(unittest.TestCase):
    def test_aws_documented_get_object_vector(self):
        headers = s3_put.sign_headers(
            method="GET",
            url="https://examplebucket.s3.amazonaws.com/test.txt",
            payload_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            access_key=ENV["S3_GITHUB_TRANSPORT_ACCESS_KEY"],
            secret_key=ENV["S3_GITHUB_TRANSPORT_SECRET_KEY"],
            region="us-east-1",
            now=NOW,
            extra_headers={"Range": "bytes=0-9"},
        )
        self.assertTrue(
            headers["authorization"].endswith(
                "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
            )
        )
        self.assertNotIn("host", headers)


class KeyLayoutTest(unittest.TestCase):
    def test_layouts(self):
        self.assertEqual(
            s3_put.object_key("logs", "StreamScapeTV/example-repo", "0123-uuid", "1-1.txt"),
            "logs/example-repo/0123-uuid/1-1.txt",
        )
        self.assertEqual(
            s3_put.object_key("releases", "o/maven-lib", "1.4.2", "publication.zip"),
            "releases/maven-lib/1.4.2/publication.zip",
        )
        self.assertEqual(
            s3_put.object_key("screenshots", "o/app", "ios/phone", "a.png"),
            "screenshots/app/ios/phone/a.png",
        )

    def test_rejects_traversal_and_bad_kind(self):
        for scope in ("..", "a/../b", "bad name", "-x"):
            with self.assertRaises(s3_put.S3Error):
                s3_put.object_key("logs", "o/r", scope, "f.txt")
        with self.assertRaises(s3_put.S3Error):
            s3_put.object_key("other", "o/r", "1", "f.txt")
        with self.assertRaises(s3_put.S3Error):
            s3_put.object_key("logs", "o/r", "1", "x" * 600)


class ClientTest(unittest.TestCase):
    def client(self, opener, env=ENV):
        return s3_put.Client(env, opener=opener, sleep=lambda _s: None, clock=lambda: NOW)

    def test_put_signs_and_never_sends_secret(self):
        opener = FakeOpener(Response())
        self.client(opener).put("logs/r/1/f.txt", b"hello", "text/plain")
        request = opener.requests[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.full_url, "https://s3.nas.faruqi.dev/github-transport/logs/r/1/f.txt")
        flat = repr(request.headers)
        self.assertIn("AWS4-HMAC-SHA256", flat)
        self.assertNotIn(ENV["S3_GITHUB_TRANSPORT_SECRET_KEY"], flat)
        self.assertEqual(request.data, b"hello")

    def test_retries_transient_then_fails_permanent(self):
        opener = FakeOpener(http_error(503), Response())
        self.client(opener).put("logs/r/1/f.txt", b"x")
        self.assertEqual(len(opener.requests), 2)
        with self.assertRaises(s3_put.S3Error) as ctx:
            self.client(FakeOpener(http_error(403))).put("logs/r/1/f.txt", b"x")
        self.assertEqual(ctx.exception.category, "s3_http_403")

    def test_immutable_existing_identical_is_noop_and_different_fails(self):
        import hashlib

        etag = hashlib.md5(b"same").hexdigest()
        opener = FakeOpener(Response(200, {"ETag": f'"{etag}"'}))
        self.client(opener).put("releases/r/1/f", b"same", immutable=True)
        self.assertEqual([r.method for r in opener.requests], ["HEAD"])
        with self.assertRaises(s3_put.S3Error) as ctx:
            self.client(FakeOpener(Response(200, {"ETag": '"deadbeef"'}))).put(
                "releases/r/1/f", b"same", immutable=True
            )
        self.assertEqual(ctx.exception.category, "immutable_object_differs")
        opener = FakeOpener(http_error(404), Response())
        self.client(opener).put("releases/r/1/f", b"same", immutable=True)
        self.assertEqual([r.method for r in opener.requests], ["HEAD", "PUT"])

    def test_missing_credentials_and_http_endpoint(self):
        with self.assertRaises(s3_put.S3Error):
            s3_put.Client({})
        with self.assertRaises(s3_put.S3Error):
            s3_put.Client({**ENV, "S3_ENDPOINT": "http://s3.example"})

    def test_reachable_single_attempt(self):
        self.assertTrue(self.client(FakeOpener(http_error(403))).reachable())
        self.assertFalse(self.client(FakeOpener(OSError("down"))).reachable())


class CliTest(unittest.TestCase):
    def test_put_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "f.txt")
            source.write_bytes(b"data")
            out = Path(tmp, "out")
            env = {**ENV, "GITHUB_OUTPUT": str(out)}
            with mock.patch.object(s3_put.urllib.request, "urlopen", FakeOpener(Response())):
                code = s3_put.main(
                    ["put", "--kind", "logs", "--repository", "o/r", "--scope", "42",
                     "--file-path", str(source), "--file-name", "f.txt"],
                    env,
                )
            self.assertEqual(code, 0)
            text = out.read_text()
            self.assertIn("object_key=logs/r/42/f.txt", text)
            self.assertIn("file_sha256=", text)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "t")
            target.write_bytes(b"x")
            link = Path(tmp, "l")
            os.symlink(target, link)
            code = s3_put.main(
                ["put", "--kind", "logs", "--repository", "o/r", "--scope", "1",
                 "--file-path", str(link), "--file-name", "f"],
                ENV,
            )
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
