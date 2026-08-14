"""Fail-closed acquisition of contract-owned OCI build inputs."""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import os
import re
import socket
import ssl
import stat
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, ContextManager, Protocol

from .foundation_types import (
    FoundationError,
    bounded_int,
    require,
    safe_name,
    safe_relative_path,
    sha256_hex,
)

_CHUNK_BYTES = 1024 * 1024
_MAXIMUM_INPUT_BYTES = 1024 * 1024 * 1024
_MAXIMUM_REDIRECTS = 5
_MAXIMUM_ALLOWED_HOSTS = 8
_RESERVED_INPUT_DIRECTORY = ".ciw-build-inputs"
_INPUT_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PRIVATE_HOST_SUFFIXES = (".home", ".internal", ".lan", ".local", ".localhost")
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


@dataclass(frozen=True)
class OciInputDownloadRequest:
    """One immutable external input selected by the central product contract."""

    input_id: str
    source_url: str
    sha256: str
    maximum_bytes: int
    destination: str
    allowed_hosts: tuple[str, ...]
    maximum_redirects: int


@dataclass(frozen=True)
class VerifiedOciInput:
    """Redacted input evidence; destination is always state-relative."""

    input_id: str
    sha256: str
    size_bytes: int
    destination: str

    def to_dict(self) -> dict[str, object]:
        return {
            "input_id": self.input_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "destination": self.destination,
        }


@dataclass(frozen=True)
class OciInputResponse:
    """A transport response whose complete redirect path remains inspectable."""

    stream: BinaryIO
    final_url: str
    redirect_urls: tuple[str, ...]
    status_code: int


class OciInputTransport(Protocol):
    """Narrow transport seam used only to make acquisition tests hermetic."""

    def __call__(
        self,
        source_url: str,
        *,
        validate_redirect: Callable[[str], None],
        maximum_redirects: int,
    ) -> ContextManager[OciInputResponse]: ...


def _normalized_host(host: str) -> str:
    require(isinstance(host, str) and host == host.strip(), "oci_input_host_forbidden")
    candidate = host.lower()
    require(
        candidate == host
        and 1 < len(candidate) <= 253
        and not candidate.endswith(".")
        and "." in candidate,
        "oci_input_host_forbidden",
    )
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise FoundationError("oci_input_host_forbidden")
    require(
        candidate != "localhost"
        and not candidate.endswith(_PRIVATE_HOST_SUFFIXES)
        and all(_HOST_LABEL.fullmatch(label) is not None for label in candidate.split(".")),
        "oci_input_host_forbidden",
    )
    return candidate


def _allowed_hosts(values: tuple[str, ...]) -> frozenset[str]:
    require(
        isinstance(values, tuple) and 0 < len(values) <= _MAXIMUM_ALLOWED_HOSTS,
        "oci_input_request_invalid",
    )
    hosts = tuple(_normalized_host(value) for value in values)
    require(len(set(hosts)) == len(hosts), "oci_input_request_invalid")
    return frozenset(hosts)


def _validate_url(url: str, allowed_hosts: frozenset[str]) -> None:
    require(
        isinstance(url, str)
        and url == url.strip()
        and url.isascii()
        and "#" not in url
        and all(ord(character) >= 0x20 for character in url),
        "oci_input_url_forbidden",
    )
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise FoundationError("oci_input_url_forbidden") from error
    require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and isinstance(hostname, str)
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and bool(parsed.path),
        "oci_input_url_forbidden",
    )
    require(_normalized_host(hostname) in allowed_hosts, "oci_input_host_forbidden")


def _public_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise FoundationError("oci_input_address_forbidden") from error
    require(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified,
        "oci_input_address_forbidden",
    )
    return address.compressed


def _resolve_public_addresses(hostname: str) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(
            hostname,
            443,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as error:
        raise FoundationError("oci_input_dns_failed") from error
    require(bool(answers), "oci_input_dns_failed")
    addresses: list[str] = []
    for family, socket_type, protocol, _canonical_name, socket_address in answers:
        require(
            family in {socket.AF_INET, socket.AF_INET6}
            and socket_type == socket.SOCK_STREAM
            and protocol == socket.IPPROTO_TCP
            and isinstance(socket_address, tuple)
            and bool(socket_address),
            "oci_input_address_forbidden",
        )
        address = _public_address(str(socket_address[0]))
        if address not in addresses:
            addresses.append(address)
    require(bool(addresses), "oci_input_dns_failed")
    return tuple(addresses)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS with DNS performed once and the resulting peer pinned."""

    def __init__(
        self,
        hostname: str,
        pinned_address: str,
        *,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(hostname, port=443, timeout=60, context=context)
        self._pinned_address = _public_address(pinned_address)

    def connect(self) -> None:
        raw_socket: socket.socket | None = None
        tls_socket: socket.socket | None = None
        try:
            raw_socket = socket.create_connection(
                (self._pinned_address, 443),
                self.timeout,
                self.source_address,
            )
            tls_socket = self._context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
            raw_socket = None
            peer = tls_socket.getpeername()
            require(
                isinstance(peer, tuple)
                and bool(peer)
                and _public_address(str(peer[0])) == self._pinned_address,
                "oci_input_peer_mismatch",
            )
            self.sock = tls_socket
            tls_socket = None
        except BaseException:
            if tls_socket is not None:
                tls_socket.close()
            if raw_socket is not None:
                raw_socket.close()
            raise


def _request_once(url: str) -> tuple[_PinnedHTTPSConnection, http.client.HTTPResponse]:
    parsed = urllib.parse.urlsplit(url)
    require(isinstance(parsed.hostname, str), "oci_input_url_forbidden")
    addresses = _resolve_public_addresses(parsed.hostname)
    connection = _PinnedHTTPSConnection(
        parsed.hostname,
        addresses[0],
        context=ssl.create_default_context(),
    )
    path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/octet-stream",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Host": parsed.hostname,
                "User-Agent": "StreamScapeTV-ci-workflows-oci-input/1.0",
            },
        )
        return connection, connection.getresponse()
    except BaseException:
        connection.close()
        raise


@contextlib.contextmanager
def _open_https_input(
    source_url: str,
    *,
    validate_redirect: Callable[[str], None],
    maximum_redirects: int,
):
    redirects: list[str] = []
    current_url = source_url
    while True:
        connection: _PinnedHTTPSConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            connection, response = _request_once(current_url)
            if response.status not in {301, 302, 303, 307, 308}:
                yield OciInputResponse(
                    stream=response,
                    final_url=current_url,
                    redirect_urls=tuple(redirects),
                    status_code=response.status,
                )
                return
            require(
                len(redirects) < maximum_redirects,
                "oci_input_redirect_limit",
            )
            location = response.getheader("Location")
            require(
                isinstance(location, str) and bool(location),
                "oci_input_redirect_invalid",
            )
            redirect_url = urllib.parse.urljoin(current_url, location)
            validate_redirect(redirect_url)
            redirects.append(redirect_url)
            current_url = redirect_url
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()


def _validated_destination(value: str) -> tuple[str, tuple[str, ...]]:
    destination = safe_relative_path(value, "oci_input_destination_invalid")
    parts = PurePosixPath(destination).parts
    require(
        2 <= len(parts) <= 8
        and len(destination) <= 512
        and parts[0] == _RESERVED_INPUT_DIRECTORY
        and _RESERVED_INPUT_DIRECTORY not in parts[1:]
        and all(
            safe_name(part, "oci_input_destination_invalid") == part
            for part in parts[1:]
        ),
        "oci_input_destination_invalid",
    )
    return destination, parts


def _open_directory(name: str | Path, *, dir_fd: int | None = None) -> int:
    require(getattr(os, "O_NOFOLLOW", 0) != 0, "oci_input_state_invalid")
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=dir_fd)
        info = os.fstat(descriptor)
        require(
            stat.S_ISDIR(info.st_mode) and info.st_mode & 0o022 == 0,
            "oci_input_state_invalid",
        )
        return descriptor
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise FoundationError("oci_input_state_invalid") from error
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _open_destination_parent(
    root_descriptor: int,
    parent_parts: tuple[str, ...],
) -> tuple[int, tuple[tuple[str, ...], ...]]:
    current = os.dup(root_descriptor)
    created: list[tuple[str, ...]] = []
    prefix: list[str] = []
    try:
        for part in parent_parts:
            prefix.append(part)
            try:
                child = _open_directory(part, dir_fd=current)
            except FoundationError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                except FileExistsError:
                    child = _open_directory(part, dir_fd=current)
                except OSError as error:
                    raise FoundationError("oci_input_destination_invalid") from error
                else:
                    created.append(tuple(prefix))
                    child = _open_directory(part, dir_fd=current)
            os.close(current)
            current = child
        return current, tuple(created)
    except BaseException:
        os.close(current)
        _remove_created_directories(root_descriptor, tuple(created))
        raise


def _open_existing_parent(root_descriptor: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root_descriptor)
    try:
        for part in parts:
            child = _open_directory(part, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _entry_exists(parent_descriptor: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise FoundationError("oci_input_destination_invalid") from error
    return True


def _remove_entry(parent_descriptor: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return
    except OSError as error:
        raise FoundationError("oci_input_cleanup_failed") from error


def _remove_created_directories(
    root_descriptor: int,
    created: tuple[tuple[str, ...], ...],
) -> None:
    for parts in reversed(created):
        parent = _open_existing_parent(root_descriptor, parts[:-1])
        try:
            os.rmdir(parts[-1], dir_fd=parent)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise FoundationError("oci_input_cleanup_failed") from error
        finally:
            os.close(parent)


def _cleanup_failed_download(
    root_descriptor: int,
    parent_descriptor: int,
    *,
    temporary_name: str,
    destination_name: str,
    temporary_created: bool,
    final_created: bool,
    created_directories: tuple[tuple[str, ...], ...],
) -> None:
    if temporary_created:
        _remove_entry(parent_descriptor, temporary_name)
    if final_created:
        _remove_entry(parent_descriptor, destination_name)
    _remove_created_directories(root_descriptor, created_directories)


def _validated_request(
    request: OciInputDownloadRequest,
) -> tuple[str, str, int, str, tuple[str, ...], frozenset[str], int]:
    require(isinstance(request, OciInputDownloadRequest), "oci_input_request_invalid")
    require(
        isinstance(request.input_id, str)
        and _INPUT_ID.fullmatch(request.input_id) is not None,
        "oci_input_request_invalid",
    )
    input_id = request.input_id
    expected_sha256 = sha256_hex(request.sha256, "oci_input_request_invalid")
    maximum_bytes = bounded_int(
        request.maximum_bytes,
        minimum=1,
        maximum=_MAXIMUM_INPUT_BYTES,
        instruction="oci_input_request_invalid",
    )
    destination, destination_parts = _validated_destination(request.destination)
    hosts = _allowed_hosts(request.allowed_hosts)
    maximum_redirects = bounded_int(
        request.maximum_redirects,
        minimum=0,
        maximum=_MAXIMUM_REDIRECTS,
        instruction="oci_input_request_invalid",
    )
    _validate_url(request.source_url, hosts)
    return (
        input_id,
        expected_sha256,
        maximum_bytes,
        destination,
        destination_parts,
        hosts,
        maximum_redirects,
    )


def download_oci_input(
    request: OciInputDownloadRequest,
    *,
    registered_state: Path,
    transport: OciInputTransport | None = None,
) -> VerifiedOciInput:
    """Verify and atomically materialize one exact external build input."""

    (
        input_id,
        expected_sha256,
        maximum_bytes,
        destination,
        destination_parts,
        hosts,
        maximum_redirects,
    ) = _validated_request(request)
    require(
        isinstance(registered_state, Path) and registered_state.is_absolute(),
        "oci_input_state_invalid",
    )
    try:
        require(
            registered_state.resolve(strict=True) == registered_state,
            "oci_input_state_invalid",
        )
    except OSError as error:
        raise FoundationError("oci_input_state_invalid") from error

    root_descriptor = _open_directory(registered_state)
    parent_descriptor: int | None = None
    created_directories: tuple[tuple[str, ...], ...] = ()
    destination_name = destination_parts[-1]
    temporary_name = f".{destination_name}.{input_id}.partial"
    temporary_descriptor: int | None = None
    temporary_created = False
    final_created = False
    try:
        parent_descriptor, created_directories = _open_destination_parent(
            root_descriptor,
            destination_parts[:-1],
        )
        require(
            not _entry_exists(parent_descriptor, destination_name),
            "oci_input_destination_occupied",
        )
        require(
            not _entry_exists(parent_descriptor, temporary_name),
            "oci_input_partial_state",
        )
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            temporary_descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
            temporary_created = True
        except OSError as error:
            raise FoundationError("oci_input_partial_state") from error

        digest = hashlib.sha256()
        size_bytes = 0
        opener = transport or _open_https_input
        try:
            with os.fdopen(temporary_descriptor, "wb") as output:
                temporary_descriptor = None
                with opener(
                    request.source_url,
                    validate_redirect=lambda value: _validate_url(value, hosts),
                    maximum_redirects=maximum_redirects,
                ) as response:
                    require(
                        isinstance(response, OciInputResponse)
                        and isinstance(response.redirect_urls, tuple)
                        and isinstance(response.status_code, int)
                        and 200 <= response.status_code < 300,
                        "oci_input_download_failed",
                    )
                    require(
                        len(response.redirect_urls) <= maximum_redirects,
                        "oci_input_redirect_limit",
                    )
                    for redirect_url in response.redirect_urls:
                        _validate_url(redirect_url, hosts)
                    _validate_url(response.final_url, hosts)
                    while True:
                        chunk = response.stream.read(_CHUNK_BYTES)
                        if not chunk:
                            break
                        require(
                            isinstance(chunk, (bytes, bytearray)),
                            "oci_input_download_failed",
                        )
                        size_bytes += len(chunk)
                        require(size_bytes <= maximum_bytes, "oci_input_too_large")
                        digest.update(chunk)
                        output.write(chunk)
                require(
                    digest.hexdigest() == expected_sha256,
                    "oci_input_digest_mismatch",
                )
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), 0o444)
        except FoundationError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            raise FoundationError("oci_input_download_failed") from error
        except Exception as error:
            raise FoundationError("oci_input_download_failed") from error

        try:
            os.link(
                temporary_name,
                destination_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            final_created = True
            final_info = os.stat(
                destination_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            require(
                stat.S_ISREG(final_info.st_mode)
                and final_info.st_mode & 0o777 == 0o444,
                "oci_input_finalize_failed",
            )
            _remove_entry(parent_descriptor, temporary_name)
            os.fsync(parent_descriptor)
        except FoundationError:
            raise
        except OSError as error:
            raise FoundationError("oci_input_finalize_failed") from error

        return VerifiedOciInput(
            input_id=input_id,
            sha256=expected_sha256,
            size_bytes=size_bytes,
            destination=destination,
        )
    except BaseException:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
            temporary_descriptor = None
        if parent_descriptor is not None:
            _cleanup_failed_download(
                root_descriptor,
                parent_descriptor,
                temporary_name=temporary_name,
                destination_name=destination_name,
                temporary_created=temporary_created,
                final_created=final_created,
                created_directories=created_directories,
            )
        raise
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        os.close(root_descriptor)
