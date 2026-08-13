from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import oci_registry_download as registry
from ci_workflows.oci_base_inspection import inspect_oci_base_layout
from ci_workflows.oci_registry_download import (
    OciRegistryAcquisitionError,
    OciRegistryAcquisitionRequest,
    OciRegistryHttpRequest,
    OciRegistryHttpResponse,
    acquire_oci_base,
)


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class FakeTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests: list[OciRegistryHttpRequest] = []

    @contextlib.contextmanager
    def __call__(self, request: OciRegistryHttpRequest):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected request: {request.url}")
        row = self.responses.pop(0)
        expected_url = row.get("url")
        if expected_url is not None:
            if request.url != expected_url:
                raise AssertionError(f"expected {expected_url}, got {request.url}")
        yield OciRegistryHttpResponse(
            stream=io.BytesIO(row.get("body", b"")),  # type: ignore[arg-type]
            final_url=row.get("final_url", request.url),  # type: ignore[arg-type]
            redirect_urls=row.get("redirect_urls", ()),  # type: ignore[arg-type]
            status_code=row.get("status", 200),  # type: ignore[arg-type]
            headers=row.get("headers", ()),  # type: ignore[arg-type]
        )


class RegistryFixture:
    reference_host = "docker.io"
    api_host = "registry-1.docker.io"
    token_host = "auth.docker.io"
    blob_host = "production.cloudfront.docker.com"
    repository = "library/busybox"
    platform = "linux/amd64"

    def __init__(self) -> None:
        self.layer = b"synthetic-layer"
        self.config = _json(
            {
                "architecture": "amd64",
                "config": {},
                "os": "linux",
                "rootfs": {"type": "layers", "diff_ids": [_digest(b"unpacked")]},
            }
        )
        self.manifest = _json(
            {
                "schemaVersion": 2,
                "mediaType": registry._MANIFEST_MEDIA_TYPE,
                "config": {
                    "mediaType": registry._CONFIG_MEDIA_TYPE,
                    "digest": _digest(self.config),
                    "size": len(self.config),
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                        "digest": _digest(self.layer),
                        "size": len(self.layer),
                    }
                ],
            }
        )
        self.child_descriptor = {
            "mediaType": registry._MANIFEST_MEDIA_TYPE,
            "digest": _digest(self.manifest),
            "size": len(self.manifest),
            "platform": {"os": "linux", "architecture": "amd64"},
        }
        self.root = _json(
            {
                "schemaVersion": 2,
                "mediaType": registry._INDEX_MEDIA_TYPE,
                "manifests": [self.child_descriptor],
            }
        )
        self.reference = f"{self.reference_host}/{self.repository}@{_digest(self.root)}"

    def request(self, **changes: object) -> OciRegistryAcquisitionRequest:
        values: dict[str, object] = {
            "reference": self.reference,
            "platform_manifest_digests": ((self.platform, _digest(self.manifest)),),
            "registry_api_host": self.api_host,
            "allowed_reference_hosts": (self.reference_host,),
            "allowed_registry_api_hosts": (self.api_host,),
            "allowed_token_hosts": (self.token_host,),
            "allowed_blob_hosts": (self.blob_host,),
            "maximum_redirects": 5,
        }
        values.update(changes)
        return OciRegistryAcquisitionRequest(**values)  # type: ignore[arg-type]

    def challenge(self, **changes: str) -> str:
        values = {
            "realm": f"https://{self.token_host}/token",
            "service": "registry.docker.io",
            "scope": f"repository:{self.repository}:pull",
        }
        values.update(changes)
        return "Bearer " + ",".join(f'{key}="{value}"' for key, value in values.items())

    def responses(self) -> list[dict[str, object]]:
        root_url = f"https://{self.api_host}/v2/{self.repository}/manifests/{_digest(self.root)}"
        child_url = f"https://{self.api_host}/v2/{self.repository}/manifests/{_digest(self.manifest)}"
        token_url = (
            f"https://{self.token_host}/token?service=registry.docker.io"
            f"&scope=repository%3A{self.repository.replace('/', '%2F')}%3Apull"
        )
        return [
            {
                "url": root_url,
                "status": 401,
                "headers": (("WWW-Authenticate", self.challenge()),),
            },
            {
                "url": token_url,
                "body": _json(
                    {
                        "token": "a" * 32,
                        "access_token": "a" * 32,
                        "expires_in": 300,
                    }
                ),
                "headers": (("Content-Type", "application/json"),),
            },
            {
                "url": root_url,
                "body": self.root,
                "headers": (
                    ("Content-Type", registry._INDEX_MEDIA_TYPE),
                    ("Docker-Content-Digest", _digest(self.root)),
                ),
            },
            {
                "url": child_url,
                "body": self.manifest,
                "headers": (
                    ("Content-Type", registry._MANIFEST_MEDIA_TYPE),
                    ("Docker-Content-Digest", _digest(self.manifest)),
                ),
            },
            self.blob_response(_digest(self.config), self.config),
            self.blob_response(_digest(self.layer), self.layer),
        ]

    def blob_response(self, digest: str, body: bytes) -> dict[str, object]:
        initial = f"https://{self.api_host}/v2/{self.repository}/blobs/{digest}"
        final = f"https://{self.blob_host}/registry-v2/docker/registry/v2/blobs/{digest}/data?sig=x"
        return {
            "url": initial,
            "body": body,
            "headers": (("Content-Length", str(len(body))),),
            "redirect_urls": (final,),
            "final_url": final,
        }


class OciRegistryAcquisitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RegistryFixture()
        self.temporary = tempfile.TemporaryDirectory()
        self.parent = Path(self.temporary.name).resolve() / "private"
        self.parent.mkdir(mode=0o700)
        self.state = self.parent / "acquisition"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_code(self, code: str, function) -> None:
        with self.assertRaises(OciRegistryAcquisitionError) as failure:
            function()
        self.assertEqual(code, failure.exception.code)

    def acquire(self, responses: list[dict[str, object]] | None = None):
        transport = FakeTransport(responses or self.fixture.responses())
        result = acquire_oci_base(
            self.fixture.request(), registered_state=self.state, transport=transport
        )
        return result, transport

    def test_anonymous_bearer_acquisition_creates_inspectable_exact_layouts(self) -> None:
        result, transport = self.acquire()
        self.assertEqual(_digest(self.fixture.root), result.root_digest)
        self.assertEqual(registry._INDEX_MEDIA_TYPE, result.root_media_type)
        self.assertEqual({self.fixture.platform}, set(result.child_layouts))
        evidence = inspect_oci_base_layout(
            result.root_layout,
            self.fixture.reference,
            (self.fixture.platform,),
            result.child_layouts,
        )
        self.assertEqual(_digest(self.fixture.root), evidence.root_digest)
        self.assertEqual(_digest(self.fixture.manifest), evidence.platforms[0].manifest_digest)
        self.assertEqual(_digest(self.fixture.config), evidence.platforms[0].config_digest)
        self.assertFalse(any(self.fixture.token_host in str(path) for path in self.state.rglob("*")))
        self.assertTrue(
            all(b"a" * 32 not in path.read_bytes() for path in self.state.rglob("*") if path.is_file())
        )
        self.assertTrue(
            all(path.stat().st_mode & 0o777 == 0o444 for path in self.state.rglob("*") if path.is_file())
        )
        token_request = transport.requests[1]
        self.assertNotIn("Authorization", dict(token_request.headers))
        for request in transport.requests[2:]:
            self.assertEqual("Bearer " + "a" * 32, dict(request.headers).get("Authorization"))

    def test_failure_removes_every_created_layout_and_partial(self) -> None:
        responses = self.fixture.responses()
        responses[-1]["body"] = b"tampered-layer"
        self.assert_code(
            "oci_registry_size_mismatch",
            lambda: acquire_oci_base(
                self.fixture.request(),
                registered_state=self.state,
                transport=FakeTransport(responses),
            ),
        )
        self.assertFalse(self.state.exists())
        self.assertEqual([], list(self.parent.rglob("*.partial")))

    def test_cancellation_closes_raw_descriptor_and_cleans_partial(self) -> None:
        destination = self.parent / "value"
        captured: list[int] = []
        real_open = os.open

        def capture_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            captured.append(descriptor)
            return descriptor

        with (
            mock.patch.object(registry.os, "open", side_effect=capture_open),
            mock.patch.object(registry.os, "write", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            registry._atomic_bytes(destination, b"value")
        self.assertEqual(1, len(captured))
        with self.assertRaises(OSError):
            os.fstat(captured[0])
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_name(".value.partial").exists())

    def test_transport_cancellation_propagates_after_complete_state_cleanup(self) -> None:
        class CancelTransport:
            @contextlib.contextmanager
            def __call__(self, _request: OciRegistryHttpRequest):
                raise KeyboardInterrupt
                yield  # pragma: no cover

        with self.assertRaises(KeyboardInterrupt):
            acquire_oci_base(
                self.fixture.request(),
                registered_state=self.state,
                transport=CancelTransport(),
            )
        self.assertFalse(self.state.exists())

    def test_digest_mismatch_fails_and_cleans_state(self) -> None:
        responses = self.fixture.responses()
        responses[2]["body"] = self.fixture.root + b" "
        self.assert_code(
            "oci_registry_digest_mismatch",
            lambda: acquire_oci_base(
                self.fixture.request(), registered_state=self.state, transport=FakeTransport(responses)
            ),
        )
        self.assertFalse(self.state.exists())

    def test_unapproved_blob_redirect_is_rejected(self) -> None:
        responses = self.fixture.responses()
        responses[-2]["redirect_urls"] = ("https://evil.example/blob",)
        responses[-2]["final_url"] = "https://evil.example/blob"
        self.assert_code(
            "oci_registry_host_forbidden",
            lambda: acquire_oci_base(
                self.fixture.request(), registered_state=self.state, transport=FakeTransport(responses)
            ),
        )
        self.assertFalse(self.state.exists())

    def test_redirect_limit_is_rechecked_across_injected_transport(self) -> None:
        responses = self.fixture.responses()
        redirect = f"https://{self.fixture.blob_host}/blob"
        responses[-2]["redirect_urls"] = (redirect, redirect)
        responses[-2]["final_url"] = redirect
        self.assert_code(
            "oci_registry_redirect_limit",
            lambda: acquire_oci_base(
                self.fixture.request(maximum_redirects=1),
                registered_state=self.state,
                transport=FakeTransport(responses),
            ),
        )

    def test_challenge_scope_is_exact(self) -> None:
        responses = self.fixture.responses()
        responses[0]["headers"] = (
            ("WWW-Authenticate", self.fixture.challenge(scope="repository:other/repo:pull")),
        )
        self.assert_code(
            "oci_registry_auth_invalid",
            lambda: acquire_oci_base(
                self.fixture.request(), registered_state=self.state, transport=FakeTransport(responses)
            ),
        )

    def test_token_response_rejects_refresh_or_unequal_tokens(self) -> None:
        for payload in (
            {"token": "a" * 32, "refresh_token": "b" * 32},
            {"token": "a" * 32, "access_token": "b" * 32},
        ):
            with self.subTest(payload=payload):
                responses = self.fixture.responses()
                responses[1]["body"] = _json(payload)
                self.assert_code(
                    "oci_registry_auth_invalid",
                    lambda responses=responses: acquire_oci_base(
                        self.fixture.request(),
                        registered_state=self.state,
                        transport=FakeTransport(responses),
                    ),
                )
                self.assertFalse(self.state.exists())

    def test_token_response_requires_json_media_type(self) -> None:
        responses = self.fixture.responses()
        responses[1]["headers"] = (("Content-Type", "text/plain"),)
        self.assert_code(
            "oci_registry_auth_invalid",
            lambda: acquire_oci_base(
                self.fixture.request(), registered_state=self.state, transport=FakeTransport(responses)
            ),
        )

    def test_index_must_bind_requested_platform_digest(self) -> None:
        request = self.fixture.request(
            platform_manifest_digests=((self.fixture.platform, "sha256:" + "0" * 64),)
        )
        self.assert_code(
            "oci_registry_lock_mismatch",
            lambda: acquire_oci_base(
                request, registered_state=self.state, transport=FakeTransport(self.fixture.responses())
            ),
        )

    def test_blob_size_is_bounded_before_download(self) -> None:
        original = registry._MAXIMUM_CONFIG_BYTES
        with mock.patch.object(registry, "_MAXIMUM_CONFIG_BYTES", len(self.fixture.config) - 1):
            self.assert_code(
                "oci_registry_blob_too_large",
                lambda: acquire_oci_base(
                    self.fixture.request(),
                    registered_state=self.state,
                    transport=FakeTransport(self.fixture.responses()),
                ),
            )
        self.assertGreater(original, len(self.fixture.config))

    def test_total_size_is_bounded_before_next_blob_request(self) -> None:
        responses = self.fixture.responses()
        # Root, child manifest, and config requests may complete. The declared
        # layer size crosses the patched total budget, so no layer request is
        # issued and every registered byte is removed.
        transport = FakeTransport(responses)
        budget = len(self.fixture.root) + len(self.fixture.manifest) + len(self.fixture.config)
        with mock.patch.object(registry, "_MAXIMUM_TOTAL_BYTES", budget):
            self.assert_code(
                "oci_registry_total_too_large",
                lambda: acquire_oci_base(
                    self.fixture.request(),
                    registered_state=self.state,
                    transport=transport,
                ),
            )
        self.assertEqual(5, len(transport.requests))
        self.assertIn("/blobs/" + _digest(self.fixture.config), transport.requests[-1].url)
        self.assertFalse(self.state.exists())

    def test_later_platform_manifest_is_bounded_before_child_layout_write(self) -> None:
        parsed = registry._parse_request(self.fixture.request())
        document = registry._FetchedDocument(
            self.fixture.manifest,
            registry._MANIFEST_MEDIA_TYPE,
            _digest(self.fixture.manifest),
        )
        child = self.parent / "children" / "linux-arm64-v8"
        total = [registry._MAXIMUM_TOTAL_BYTES - len(self.fixture.manifest) + 1]
        transport = FakeTransport([])
        self.assert_code(
            "oci_registry_total_too_large",
            lambda: registry._materialize_manifest_layout(
                child,
                self.fixture.child_descriptor,
                document,
                parsed=parsed,
                transport=transport,
                token="a" * 32,
                total_bytes=total,
            ),
        )
        self.assertEqual([], transport.requests)
        self.assertFalse(child.exists())
        self.assertEqual(
            registry._MAXIMUM_TOTAL_BYTES - len(self.fixture.manifest) + 1,
            total[0],
        )

    def test_layer_count_is_bounded_before_any_blob_request(self) -> None:
        too_many = registry._MAXIMUM_LAYERS + 1
        manifest = _json(
            {
                "schemaVersion": 2,
                "mediaType": registry._MANIFEST_MEDIA_TYPE,
                "config": {
                    "mediaType": registry._CONFIG_MEDIA_TYPE,
                    "digest": _digest(self.fixture.config),
                    "size": len(self.fixture.config),
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                        "digest": _digest(self.fixture.layer),
                        "size": len(self.fixture.layer),
                    }
                    for _ in range(too_many)
                ],
            }
        )
        child = dict(self.fixture.child_descriptor)
        child["digest"] = _digest(manifest)
        child["size"] = len(manifest)
        root = _json(
            {
                "schemaVersion": 2,
                "mediaType": registry._INDEX_MEDIA_TYPE,
                "manifests": [child],
            }
        )
        self.fixture.root = root
        self.fixture.manifest = manifest
        self.fixture.child_descriptor = child
        self.fixture.reference = f"docker.io/{self.fixture.repository}@{_digest(root)}"
        transport = FakeTransport(self.fixture.responses()[:4])
        self.assert_code(
            "oci_registry_manifest_invalid",
            lambda: acquire_oci_base(
                self.fixture.request(),
                registered_state=self.state,
                transport=transport,
            ),
        )
        self.assertEqual(4, len(transport.requests))
        self.assertTrue(all("/blobs/" not in request.url for request in transport.requests))
        self.assertFalse(self.state.exists())

    def test_existing_or_symlink_state_is_never_reused(self) -> None:
        self.state.mkdir()
        self.assert_code(
            "oci_registry_state_invalid",
            lambda: acquire_oci_base(
                self.fixture.request(),
                registered_state=self.state,
                transport=FakeTransport(self.fixture.responses()),
            ),
        )
        self.state.rmdir()
        outside = self.parent / "outside"
        outside.mkdir()
        self.state.symlink_to(outside, target_is_directory=True)
        self.assert_code(
            "oci_registry_state_invalid",
            lambda: acquire_oci_base(
                self.fixture.request(),
                registered_state=self.state,
                transport=FakeTransport(self.fixture.responses()),
            ),
        )

    def test_reference_and_all_host_roles_are_closed(self) -> None:
        cases = (
            {"reference": "https://docker.io/library/busybox@sha256:" + "0" * 64},
            {"allowed_reference_hosts": ("other.example",)},
            {"registry_api_host": "127.0.0.1"},
            {"allowed_token_hosts": ("auth.docker.io", "auth.docker.io")},
            {"maximum_redirects": 6},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assert_code(
                    "oci_registry_reference_invalid"
                    if "reference" in changes
                    else (
                        "oci_registry_host_forbidden"
                        if "allowed_reference_hosts" in changes or "registry_api_host" in changes
                        else "oci_registry_request_invalid"
                    ),
                    lambda changes=changes: acquire_oci_base(
                        self.fixture.request(**changes),
                        registered_state=self.state,
                        transport=FakeTransport(self.fixture.responses()),
                    ),
                )

    def test_dns_rejects_entire_answer_set_if_one_address_is_private(self) -> None:
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ]
        with mock.patch.object(socket, "getaddrinfo", return_value=answers):
            self.assert_code(
                "oci_registry_address_forbidden",
                lambda: registry._resolve_public_addresses(self.fixture.api_host),
            )

    def test_pinned_connection_rejects_changed_peer_and_closes_sockets(self) -> None:
        class Socket:
            def __init__(self, peer: str) -> None:
                self.peer = peer
                self.closed = False

            def getpeername(self):
                return (self.peer, 443)

            def close(self) -> None:
                self.closed = True

        class Context:
            def __init__(self, tls_socket: Socket) -> None:
                self.tls_socket = tls_socket
                self.server_hostname: str | None = None

            def wrap_socket(self, raw_socket, *, server_hostname: str):
                self.server_hostname = server_hostname
                return self.tls_socket

        raw = Socket("93.184.216.34")
        tls = Socket("1.1.1.1")
        context = Context(tls)
        connection = registry._PinnedHTTPSConnection(
            self.fixture.api_host, "93.184.216.34", context=context  # type: ignore[arg-type]
        )
        with mock.patch.object(socket, "create_connection", return_value=raw):
            self.assert_code("oci_registry_peer_mismatch", connection.connect)
        self.assertEqual(self.fixture.api_host, context.server_hostname)
        self.assertTrue(tls.closed)

    def test_default_transport_strips_bearer_before_redirect(self) -> None:
        class Response:
            def __init__(self, status: int, location: str | None = None) -> None:
                self.status = status
                self.location = location
                self.closed = False

            def getheader(self, name: str):
                return self.location if name == "Location" else None

            def getheaders(self):
                return []

            def close(self) -> None:
                self.closed = True

            def read(self, _size: int = -1) -> bytes:
                return b""

        class Connection:
            def close(self) -> None:
                pass

        redirect = f"https://{self.fixture.blob_host}/blob"
        calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        responses = [Response(307, redirect), Response(200)]

        def request_once(url: str, headers: tuple[tuple[str, str], ...]):
            calls.append((url, headers))
            return Connection(), responses.pop(0)

        request = OciRegistryHttpRequest(
            url=f"https://{self.fixture.api_host}/v2/blob",
            headers=(("Authorization", "Bearer secret-value-123456"),),
            initial_hosts=(self.fixture.api_host,),
            redirect_hosts=(self.fixture.blob_host,),
            maximum_redirects=1,
        )
        with mock.patch.object(registry, "_request_once", side_effect=request_once):
            with registry._open_https(request) as response:
                self.assertEqual(200, response.status_code)
        self.assertIn("Authorization", dict(calls[0][1]))
        self.assertNotIn("Authorization", dict(calls[1][1]))

    def test_all_stable_module_codes_are_registered_in_product_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/ci_workflows/oci_registry_download.py").read_text(encoding="utf-8")
        codes = set(re.findall(r'"(oci_registry_[a-z_]+)"', source))
        contract = json.loads((root / "contracts/oci-products.json").read_text(encoding="utf-8"))
        self.assertTrue(codes)
        self.assertEqual(set(), codes - set(contract["failure_codes"]))


@unittest.skipUnless(
    os.environ.get("CIW_LIVE_OCI_REGISTRY_TEST") == "1",
    "set CIW_LIVE_OCI_REGISTRY_TEST=1 for the exact anonymous network proof",
)
class LiveOciRegistryAcquisitionTests(unittest.TestCase):
    def test_exact_busybox_amd64_acquisition(self) -> None:
        root_digest = "sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
        child_digest = "sha256:b7f3d86d6e84fc17718c48bcde1450807faa2d56704205c697b4bd5df7b9e29f"
        request = OciRegistryAcquisitionRequest(
            reference=f"docker.io/library/busybox@{root_digest}",
            platform_manifest_digests=(("linux/amd64", child_digest),),
            registry_api_host="registry-1.docker.io",
            allowed_reference_hosts=("docker.io",),
            allowed_registry_api_hosts=("registry-1.docker.io",),
            allowed_token_hosts=("auth.docker.io",),
            allowed_blob_hosts=("production.cloudfront.docker.com",),
            maximum_redirects=5,
        )
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve() / "private"
            parent.mkdir(mode=0o700)
            acquired = acquire_oci_base(request, registered_state=parent / "busybox")
            evidence = inspect_oci_base_layout(
                acquired.root_layout,
                request.reference,
                ("linux/amd64",),
                acquired.child_layouts,
            )
            self.assertEqual(root_digest, evidence.root_digest)
            self.assertEqual(child_digest, evidence.platforms[0].manifest_digest)


if __name__ == "__main__":
    unittest.main()
