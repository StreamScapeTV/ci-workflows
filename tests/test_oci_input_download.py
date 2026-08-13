from __future__ import annotations

import contextlib
import hashlib
import io
import os
import socket
import ssl
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import oci_input_download as input_download
from ci_workflows.foundation_types import FoundationError
from ci_workflows.oci_input_download import (
    OciInputDownloadRequest,
    OciInputResponse,
    VerifiedOciInput,
    download_oci_input,
)


class _Transport:
    def __init__(
        self,
        content: bytes,
        *,
        final_url: str,
        redirects: tuple[str, ...] = (),
        status_code: int = 200,
        invoke_redirect_validator: bool = True,
        failure: Exception | None = None,
    ) -> None:
        self.content = content
        self.final_url = final_url
        self.redirects = redirects
        self.status_code = status_code
        self.invoke_redirect_validator = invoke_redirect_validator
        self.failure = failure

    @contextlib.contextmanager
    def __call__(
        self,
        source_url: str,
        *,
        validate_redirect,
        maximum_redirects: int,
    ):
        if self.failure is not None:
            raise self.failure
        if self.invoke_redirect_validator:
            for redirect in self.redirects:
                validate_redirect(redirect)
        yield OciInputResponse(
            stream=io.BytesIO(self.content),
            final_url=self.final_url,
            redirect_urls=self.redirects,
            status_code=self.status_code,
        )


class _FailingStream(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        if self.tell() > 0:
            raise OSError("synthetic stream failure")
        return super().read(2 if size < 0 else min(size, 2))


class _HttpResponse(io.BytesIO):
    def __init__(
        self,
        status: int,
        *,
        location: str | None = None,
        content: bytes = b"",
    ) -> None:
        super().__init__(content)
        self.status = status
        self.location = location

    def getheader(self, name: str) -> str | None:
        if name == "Location":
            return self.location
        return None


class _Connection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class OciInputDownloadTests(unittest.TestCase):
    url = "https://downloads.example.test/assets/fixture.bin"
    host = "downloads.example.test"
    content = b"verified external input\n"

    def request(self, **changes: object) -> OciInputDownloadRequest:
        values: dict[str, object] = {
            "input_id": "fixture-input",
            "source_url": self.url,
            "sha256": hashlib.sha256(self.content).hexdigest(),
            "maximum_bytes": 1024,
            "destination": ".ciw-build-inputs/README.md",
            "allowed_hosts": (self.host,),
            "maximum_redirects": 5,
        }
        values.update(changes)
        return OciInputDownloadRequest(**values)  # type: ignore[arg-type]

    def assert_instruction(self, instruction: str, operation) -> None:
        with self.assertRaises(FoundationError) as caught:
            operation()
        self.assertEqual(caught.exception.instruction, instruction)

    def test_verified_input_is_atomically_installed_read_only_with_redacted_evidence(self) -> None:
        redirect = "https://downloads.example.test/cdn/fixture.bin"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            evidence = download_oci_input(
                self.request(),
                registered_state=state,
                transport=_Transport(
                    self.content,
                    final_url=redirect,
                    redirects=(redirect,),
                ),
            )
            destination = state / ".ciw-build-inputs/README.md"
            self.assertEqual(destination.read_bytes(), self.content)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o444)
            self.assertEqual(
                evidence,
                VerifiedOciInput(
                    input_id="fixture-input",
                    sha256=hashlib.sha256(self.content).hexdigest(),
                    size_bytes=len(self.content),
                    destination=".ciw-build-inputs/README.md",
                ),
            )
            self.assertNotIn(str(state), str(evidence.to_dict()))
            self.assertEqual(
                sorted(
                    path.name for path in (state / ".ciw-build-inputs").iterdir()
                ),
                ["README.md"],
            )

    def test_source_urls_reject_credentials_fragments_ips_private_hosts_and_unapproved_hosts(self) -> None:
        cases = {
            "http://downloads.example.test/a": "oci_input_url_forbidden",
            "https://user@downloads.example.test/a": "oci_input_url_forbidden",
            "https://downloads.example.test/a#part": "oci_input_url_forbidden",
            "https://downloads.example.test:444/a": "oci_input_url_forbidden",
            "https://127.0.0.1/a": "oci_input_host_forbidden",
            "https://[::1]/a": "oci_input_host_forbidden",
            "https://service.internal/a": "oci_input_host_forbidden",
            "https://evil.example.test/a": "oci_input_host_forbidden",
        }
        for url, instruction in cases.items():
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory:
                self.assert_instruction(
                    instruction,
                    lambda: download_oci_input(
                        self.request(source_url=url),
                        registered_state=Path(directory).resolve(),
                        transport=_Transport(self.content, final_url=url),
                    ),
                )
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_each_redirect_and_final_url_is_independently_validated(self) -> None:
        bad = "https://evil.example.test/fixture.bin"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            self.assert_instruction(
                "oci_input_host_forbidden",
                lambda: download_oci_input(
                    self.request(),
                    registered_state=state,
                    transport=_Transport(
                        self.content,
                        final_url=self.url,
                        redirects=(bad,),
                        invoke_redirect_validator=False,
                    ),
                ),
            )
            self.assertEqual(list(state.iterdir()), [])
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            self.assert_instruction(
                "oci_input_host_forbidden",
                lambda: download_oci_input(
                    self.request(),
                    registered_state=state,
                    transport=_Transport(self.content, final_url=bad),
                ),
            )
            self.assertEqual(list(state.iterdir()), [])

    def test_contract_selected_redirect_bound_allows_zero_and_exact_but_rejects_one_more(self) -> None:
        redirect = "https://downloads.example.test/cdn/fixture.bin"
        cases = (
            (0, (), None),
            (1, (redirect,), None),
            (0, (redirect,), "oci_input_redirect_limit"),
            (1, (redirect, self.url), "oci_input_redirect_limit"),
        )
        for maximum, redirects, instruction in cases:
            with self.subTest(maximum=maximum, redirects=len(redirects)):
                with tempfile.TemporaryDirectory() as directory:
                    state = Path(directory).resolve()
                    operation = lambda: download_oci_input(
                        self.request(maximum_redirects=maximum),
                        registered_state=state,
                        transport=_Transport(
                            self.content,
                            final_url=redirects[-1] if redirects else self.url,
                            redirects=redirects,
                            invoke_redirect_validator=False,
                        ),
                    )
                    if instruction is None:
                        operation()
                        self.assertTrue((state / ".ciw-build-inputs/README.md").is_file())
                    else:
                        self.assert_instruction(instruction, operation)
                        self.assertEqual(list(state.iterdir()), [])

    def test_production_transport_enforces_redirect_bound_before_next_connection(self) -> None:
        redirect = "https://downloads.example.test/cdn/fixture.bin"
        first = _HttpResponse(302, location=redirect)
        final = _HttpResponse(200, content=self.content)
        first_connection = _Connection()
        final_connection = _Connection()
        with mock.patch(
            "ci_workflows.oci_input_download._request_once",
            side_effect=((first_connection, first), (final_connection, final)),
        ) as request_once:
            with input_download._open_https_input(
                self.url,
                validate_redirect=lambda url: None,
                maximum_redirects=1,
            ) as response:
                self.assertEqual(response.redirect_urls, (redirect,))
                self.assertEqual(response.stream.read(), self.content)
            self.assertEqual(request_once.call_count, 2)
            self.assertTrue(first_connection.closed)
            self.assertTrue(final_connection.closed)

        blocked_connection = _Connection()

        def open_blocked() -> None:
            with input_download._open_https_input(
                self.url,
                validate_redirect=lambda url: None,
                maximum_redirects=0,
            ):
                self.fail("redirect should have failed before yielding")

        with mock.patch(
            "ci_workflows.oci_input_download._request_once",
            return_value=(blocked_connection, _HttpResponse(302, location=redirect)),
        ) as request_once:
            self.assert_instruction(
                "oci_input_redirect_limit",
                open_blocked,
            )
            self.assertEqual(request_once.call_count, 1)
            self.assertTrue(blocked_connection.closed)

        second_redirect = "https://downloads.example.test/final/fixture.bin"
        first_connection = _Connection()
        second_connection = _Connection()
        responses = (
            (first_connection, _HttpResponse(302, location=redirect)),
            (second_connection, _HttpResponse(307, location=second_redirect)),
        )

        def open_one_too_many() -> None:
            with input_download._open_https_input(
                self.url,
                validate_redirect=lambda url: None,
                maximum_redirects=1,
            ):
                self.fail("second redirect should have failed before yielding")

        with mock.patch(
            "ci_workflows.oci_input_download._request_once",
            side_effect=responses,
        ) as request_once:
            self.assert_instruction("oci_input_redirect_limit", open_one_too_many)
            self.assertEqual(request_once.call_count, 2)
            self.assertTrue(first_connection.closed)
            self.assertTrue(second_connection.closed)

    def test_dns_answers_must_all_be_public(self) -> None:
        public_answers = (
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            ),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0),
            ),
        )
        with mock.patch(
            "ci_workflows.oci_input_download.socket.getaddrinfo",
            return_value=public_answers,
        ):
            self.assertEqual(
                input_download._resolve_public_addresses(self.host),
                ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"),
            )
        for forbidden in (
            "10.0.0.1",
            "127.0.0.1",
            "169.254.1.1",
            "224.0.0.1",
            "0.0.0.0",
            "2001:db8::1",
        ):
            with self.subTest(forbidden=forbidden), mock.patch(
                "ci_workflows.oci_input_download.socket.getaddrinfo",
                return_value=(
                    public_answers[0],
                    (
                        socket.AF_INET6 if ":" in forbidden else socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (forbidden, 443),
                    ),
                ),
            ):
                self.assert_instruction(
                    "oci_input_address_forbidden",
                    lambda: input_download._resolve_public_addresses(self.host),
                )

    def test_tls_peer_must_match_pinned_public_address_and_original_hostname(self) -> None:
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

        raw_socket = Socket("93.184.216.34")
        tls_socket = Socket("1.1.1.1")
        context = Context(tls_socket)
        connection = input_download._PinnedHTTPSConnection(
            self.host,
            "93.184.216.34",
            context=context,  # type: ignore[arg-type]
        )
        with mock.patch(
            "ci_workflows.oci_input_download.socket.create_connection",
            return_value=raw_socket,
        ):
            self.assert_instruction("oci_input_peer_mismatch", connection.connect)
        self.assertEqual(context.server_hostname, self.host)
        self.assertTrue(tls_socket.closed)

    def test_input_identifier_exactly_matches_lock_contract(self) -> None:
        accepted = ("a", "a" + "1" * 63, "input-1")
        rejected = ("A", "a_b", "1input", "a" + "1" * 64, "")
        for input_id in accepted:
            with self.subTest(accepted=input_id), tempfile.TemporaryDirectory() as directory:
                download_oci_input(
                    self.request(input_id=input_id),
                    registered_state=Path(directory).resolve(),
                    transport=_Transport(self.content, final_url=self.url),
                )
        for input_id in rejected:
            with self.subTest(rejected=input_id), tempfile.TemporaryDirectory() as directory:
                self.assert_instruction(
                    "oci_input_request_invalid",
                    lambda: download_oci_input(
                        self.request(input_id=input_id),
                        registered_state=Path(directory).resolve(),
                        transport=_Transport(self.content, final_url=self.url),
                    ),
                )
    def test_digest_oversize_status_and_transport_failures_leave_no_partial_state(self) -> None:
        cases = (
            (
                "oci_input_digest_mismatch",
                self.request(sha256="0" * 64),
                _Transport(self.content, final_url=self.url),
            ),
            (
                "oci_input_too_large",
                self.request(maximum_bytes=len(self.content) - 1),
                _Transport(self.content, final_url=self.url),
            ),
            (
                "oci_input_download_failed",
                self.request(),
                _Transport(self.content, final_url=self.url, status_code=404),
            ),
            (
                "oci_input_download_failed",
                self.request(),
                _Transport(self.content, final_url=self.url, failure=OSError("failed")),
            ),
        )
        for instruction, request, transport in cases:
            with self.subTest(instruction=instruction), tempfile.TemporaryDirectory() as directory:
                state = Path(directory).resolve()
                self.assert_instruction(
                    instruction,
                    lambda: download_oci_input(
                        request,
                        registered_state=state,
                        transport=transport,
                    ),
                )
                self.assertEqual(list(state.iterdir()), [])

    def test_stream_failure_is_wrapped_and_cleaned(self) -> None:
        @contextlib.contextmanager
        def failing_transport(
            source_url: str,
            *,
            validate_redirect,
            maximum_redirects: int,
        ):
            yield OciInputResponse(
                stream=_FailingStream(self.content),
                final_url=source_url,
                redirect_urls=(),
                status_code=200,
            )

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            self.assert_instruction(
                "oci_input_download_failed",
                lambda: download_oci_input(
                    self.request(),
                    registered_state=state,
                    transport=failing_transport,
                ),
            )
            self.assertEqual(list(state.iterdir()), [])

    def test_preexisting_destination_symlink_or_partial_is_never_replaced(self) -> None:
        for kind in ("file", "symlink", "partial"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                state = Path(directory).resolve()
                inputs = state / ".ciw-build-inputs"
                inputs.mkdir()
                outside = state / "outside"
                outside.write_bytes(b"outside")
                if kind == "file":
                    occupied = inputs / "README.md"
                    occupied.write_bytes(b"existing")
                    instruction = "oci_input_destination_occupied"
                elif kind == "symlink":
                    occupied = inputs / "README.md"
                    occupied.symlink_to(outside)
                    instruction = "oci_input_destination_occupied"
                else:
                    occupied = inputs / ".README.md.fixture-input.partial"
                    occupied.write_bytes(b"partial")
                    instruction = "oci_input_partial_state"
                self.assert_instruction(
                    instruction,
                    lambda: download_oci_input(
                        self.request(),
                        registered_state=state,
                        transport=_Transport(self.content, final_url=self.url),
                    ),
                )
                if kind == "symlink":
                    self.assertTrue(occupied.is_symlink())
                else:
                    self.assertTrue(occupied.exists())
                self.assertEqual(outside.read_bytes(), b"outside")

    def test_destination_and_registered_state_are_strictly_bounded(self) -> None:
        for destination in (
            "../escape",
            "/absolute",
            "inputs/README.md",
            ".hidden/README.md",
            ".ciw-build-inputs",
            ".ciw-build-inputs/.hidden",
            ".ciw-build-inputs/nested/.ciw-build-inputs/file",
            ".ciw-build-inputs/space name",
        ):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as directory:
                self.assert_instruction(
                    "oci_input_destination_invalid",
                    lambda: download_oci_input(
                        self.request(destination=destination),
                        registered_state=Path(directory).resolve(),
                        transport=_Transport(self.content, final_url=self.url),
                    ),
                )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            self.assert_instruction(
                "oci_input_state_invalid",
                lambda: download_oci_input(
                    self.request(),
                    registered_state=linked,
                    transport=_Transport(self.content, final_url=self.url),
                ),
            )

    def test_atomic_finalize_failure_removes_partial_and_created_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            with mock.patch(
                "ci_workflows.oci_input_download.os.link",
                side_effect=OSError("synthetic finalize failure"),
            ):
                self.assert_instruction(
                    "oci_input_finalize_failed",
                    lambda: download_oci_input(
                        self.request(),
                        registered_state=state,
                        transport=_Transport(self.content, final_url=self.url),
                    ),
                )
            self.assertEqual(list(state.iterdir()), [])

    def test_partial_file_uses_exclusive_no_follow_creation(self) -> None:
        observed_flags: list[int] = []
        real_open = os.open

        def recording_open(path, flags, mode=0o777, *, dir_fd=None):
            if isinstance(path, str) and path.endswith(".partial"):
                observed_flags.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "ci_workflows.oci_input_download.os.open",
            side_effect=recording_open,
        ):
            download_oci_input(
                self.request(),
                registered_state=Path(directory).resolve(),
                transport=_Transport(self.content, final_url=self.url),
            )
        self.assertEqual(len(observed_flags), 1)
        self.assertTrue(observed_flags[0] & os.O_EXCL)
        self.assertTrue(observed_flags[0] & os.O_NOFOLLOW)

    def test_cancellation_still_removes_owned_partial_state(self) -> None:
        @contextlib.contextmanager
        def cancelled_transport(
            source_url: str,
            *,
            validate_redirect,
            maximum_redirects: int,
        ):
            raise KeyboardInterrupt
            yield  # pragma: no cover

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            with self.assertRaises(KeyboardInterrupt):
                download_oci_input(
                    self.request(),
                    registered_state=state,
                    transport=cancelled_transport,
                )
            self.assertEqual(list(state.iterdir()), [])

    def test_base_exception_after_partial_create_closes_raw_descriptor(self) -> None:
        partial_descriptors: list[int] = []
        real_open = os.open
        request = self.request()

        def recording_open(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if isinstance(path, str) and path.endswith(".partial"):
                partial_descriptors.append(descriptor)
            return descriptor

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "ci_workflows.oci_input_download.os.open",
            side_effect=recording_open,
        ), mock.patch(
            "ci_workflows.oci_input_download.hashlib.sha256",
            side_effect=KeyboardInterrupt,
        ):
            state = Path(directory).resolve()
            with self.assertRaises(KeyboardInterrupt):
                download_oci_input(
                    request,
                    registered_state=state,
                    transport=_Transport(self.content, final_url=self.url),
                )
            self.assertEqual(list(state.iterdir()), [])
        self.assertEqual(len(partial_descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(partial_descriptors[0])

    def test_base_exception_after_directory_open_closes_raw_descriptor(self) -> None:
        descriptors: list[int] = []
        real_open = os.open

        def recording_open(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            descriptors.append(descriptor)
            return descriptor

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "ci_workflows.oci_input_download.os.open",
            side_effect=recording_open,
        ), mock.patch(
            "ci_workflows.oci_input_download.os.fstat",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                download_oci_input(
                    self.request(),
                    registered_state=Path(directory).resolve(),
                    transport=_Transport(self.content, final_url=self.url),
                )
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])


if __name__ == "__main__":
    unittest.main()
