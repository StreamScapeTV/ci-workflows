import datetime as dt
import io
import os
import tempfile
import time
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
            s3_put.object_key("logs", "o/r", "req-1", "000003.log", "build"),
            "logs/r/req-1/build/000003.log",
        )
        self.assertEqual(
            s3_put.object_key("releases", "o/maven-lib", "1.4.2", "publication.zip"),
            "releases/maven-lib/1.4.2/publication.zip",
        )
        self.assertEqual(
            s3_put.object_key("screenshots", "o/app", "phone", "a.png", "ios"),
            "screenshots/app/phone/ios/a.png",
        )

    def test_rejects_traversal_and_bad_kind(self):
        for bad in ("..", "a/b", "bad name", "-x"):
            with self.assertRaises(s3_put.S3Error):
                s3_put.object_key("logs", "o/r", bad, "f.txt")
            with self.assertRaises(s3_put.S3Error):
                s3_put.object_key("logs", "o/r", "1", "f.txt", bad)
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
                    ["put", "--kind", "logs", "--repository", "o/r", "--id", "42", "--stage", "build",
                     "--file-path", str(source), "--file-name", "f.txt"],
                    env,
                )
            self.assertEqual(code, 0)
            text = out.read_text()
            self.assertIn("object_key=logs/r/42/build/f.txt", text)
            self.assertIn("file_sha256=", text)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "t")
            target.write_bytes(b"x")
            link = Path(tmp, "l")
            os.symlink(target, link)
            code = s3_put.main(
                ["put", "--kind", "logs", "--repository", "o/r", "--id", "1",
                 "--file-path", str(link), "--file-name", "f"],
                ENV,
            )
            self.assertEqual(code, 1)


class RecordingClient:
    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type="application/octet-stream", *, immutable=False):
        self.objects[key] = data


class StreamTests(unittest.TestCase):
    def make(self, tmp, secrets=None):
        log = Path(tmp, "stage.log")
        log.write_bytes(b"")
        client = RecordingClient()
        env = {**(secrets or {}), "S3_GITHUB_TRANSPORT_ACCESS_KEY": "AKIA-ACCESS", "S3_GITHUB_TRANSPORT_SECRET_KEY": "s3-secret-value"}
        streamer = s3_put.LogStreamer(client, project="example-repo", request_id="req-1", stage="test", log_path=log, environ=env)
        return log, client, streamer

    def test_chunks_are_numbered_scrubbed_and_merged_final_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, client, streamer = self.make(tmp, {"CI_SECRET_TOKEN": "tok-123456789"})
            with log.open("ab") as handle:
                handle.write(b"first line with tok-123456789 inside\n" + b"x" * 40)
            self.assertTrue(streamer.step())
            with log.open("ab") as handle:
                handle.write(b"\nsecond line s3-secret-value done\n")
            streamer.step()
            streamer.finish()
            keys = sorted(client.objects)
            self.assertTrue(all(k.startswith("logs/example-repo/req-1/test/") for k in keys))
            self.assertEqual(keys[0], "logs/example-repo/req-1/test/000000.log")
            chunks = b"".join(client.objects[k] for k in keys if not k.endswith("final.log"))
            final = client.objects["logs/example-repo/req-1/test/final.log"]
            self.assertEqual(chunks, final)
            for blob in client.objects.values():
                self.assertNotIn(b"tok-123456789", blob)
                self.assertNotIn(b"s3-secret-value", blob)
            self.assertIn(b"[REDACTED]", final)

    def test_secret_split_across_rounds_is_never_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, client, streamer = self.make(tmp, {"CI_SECRET_TOKEN": "tok-123456789"})
            full = b"a" * 50 + b"tok-123456789" + b"b" * 5
            for end in range(len(full) - 17, len(full) + 1, 3):
                log.write_bytes(full[:end])
                streamer.step()
            log.write_bytes(full)
            streamer.finish()
            for blob in client.objects.values():
                self.assertNotIn(b"tok-", blob)
            self.assertEqual(client.objects["logs/example-repo/req-1/test/final.log"], b"a" * 50 + b"[REDACTED]" + b"b" * 5)

    def test_run_stream_stops_and_always_writes_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, client, streamer = self.make(tmp)
            log.write_bytes(b"hello\n")
            stop = s3_put.threading.Event()
            stop.set()
            self.assertEqual(s3_put.run_stream(streamer, stop, interval=0.01), 0)
            self.assertEqual(client.objects["logs/example-repo/req-1/test/final.log"], b"hello\n")

    def test_cli_stream_uploads_chunks_while_running_and_final_on_stop_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp, "stage.log")
            log.write_bytes(b"line one " + b"x" * 100 + b"\n")
            stop_file = Path(tmp, "stage.stop")
            uploads = []

            def opener(request, timeout):
                uploads.append((request.get_method(), request.full_url.split("/github-transport/")[1]))
                return Response()

            def finish_later():
                time.sleep(0.6)
                with log.open("ab") as handle:
                    handle.write(b"line two " + b"y" * 100 + b"\n")
                time.sleep(0.6)
                stop_file.touch()

            thread = s3_put.threading.Thread(target=finish_later)
            thread.start()
            with mock.patch.object(s3_put.urllib.request, "urlopen", opener):
                code = s3_put.main(
                    ["stream", "--repository", "o/example-repo", "--request-id", "req-9", "--stage", "build",
                     "--log-path", str(log), "--stop-file", str(stop_file), "--interval", "0.2"],
                    ENV,
                )
            thread.join()
            self.assertEqual(code, 0)
            keys = [key for method, key in uploads if method == "PUT"]
            self.assertEqual(keys[0], "logs/example-repo/req-9/build/000000.log")
            self.assertEqual(keys[-1], "logs/example-repo/req-9/build/final.log")
            self.assertGreaterEqual(len(keys), 3)

    def test_invalid_stage_is_rejected_before_any_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(s3_put.S3Error):
                s3_put.LogStreamer(RecordingClient(), project="r", request_id="1", stage="../x", log_path=Path(tmp, "l"), environ=ENV)


if __name__ == "__main__":
    unittest.main()
