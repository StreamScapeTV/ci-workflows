"""Anonymous, exact-digest OCI Distribution acquisition.

The central product contract supplies every network hostname.  Producer source
supplies only an immutable image reference and the already-reviewed platform
manifest identities.  The resulting layouts are suitable for offline
inspection and import; no credential or source URL is retained in them.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import stat
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, ContextManager, Mapping, Protocol, Sequence

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY_SEGMENT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TOKEN_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{16,8192}$")
_PLATFORMS = frozenset({"linux/amd64", "linux/arm64/v8"})
_PRIVATE_HOST_SUFFIXES = (".home", ".internal", ".lan", ".local", ".localhost")
_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
_LAYER_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.nondistributable.v1.tar",
        "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
    }
)
_MANIFEST_ACCEPT = f"{_INDEX_MEDIA_TYPE}, {_MANIFEST_MEDIA_TYPE}"
_MAXIMUM_HOSTS = 8
_MAXIMUM_REDIRECTS = 5
_MAXIMUM_MANIFEST_BYTES = 32 * 1024 * 1024
_MAXIMUM_CONFIG_BYTES = 32 * 1024 * 1024
_MAXIMUM_LAYER_BYTES = 1024 * 1024 * 1024
_MAXIMUM_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
_MAXIMUM_TOKEN_BYTES = 64 * 1024
_MAXIMUM_LAYERS = 256
_CHUNK_BYTES = 1024 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class OciRegistryAcquisitionError(RuntimeError):
    """Fail-closed acquisition error carrying only a stable non-secret code."""

    def __init__(self, code: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", code) is None:
            raise ValueError("OCI registry acquisition error code must be safe")
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise OciRegistryAcquisitionError(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        _fail(code)


@dataclass(frozen=True)
class OciRegistryAcquisitionRequest:
    """One immutable anonymous base acquisition selected by central policy."""

    reference: str
    platform_manifest_digests: tuple[tuple[str, str], ...]
    registry_api_host: str
    allowed_reference_hosts: tuple[str, ...]
    allowed_registry_api_hosts: tuple[str, ...]
    allowed_token_hosts: tuple[str, ...]
    allowed_blob_hosts: tuple[str, ...]
    maximum_redirects: int


@dataclass(frozen=True)
class OciRegistryHttpRequest:
    """A fully constrained request consumed by the hermetic transport seam."""

    url: str
    headers: tuple[tuple[str, str], ...]
    initial_hosts: tuple[str, ...]
    redirect_hosts: tuple[str, ...]
    maximum_redirects: int


@dataclass(frozen=True)
class OciRegistryHttpResponse:
    """A response whose complete redirect path remains inspectable."""

    stream: BinaryIO
    final_url: str
    redirect_urls: tuple[str, ...]
    status_code: int
    headers: tuple[tuple[str, str], ...]


class OciRegistryTransport(Protocol):
    def __call__(
        self, request: OciRegistryHttpRequest
    ) -> ContextManager[OciRegistryHttpResponse]: ...


@dataclass(frozen=True)
class AcquiredOciBase:
    """Registered exact layouts, with child paths keyed by platform."""

    root_layout: Path
    child_layouts: Mapping[str, Path]
    root_digest: str
    root_media_type: str


@dataclass(frozen=True)
class _ParsedRequest:
    reference_host: str
    repository: str
    reference_digest: str
    platform_digests: Mapping[str, str]
    registry_api_host: str
    reference_hosts: frozenset[str]
    api_hosts: frozenset[str]
    token_hosts: frozenset[str]
    blob_hosts: frozenset[str]
    maximum_redirects: int


@dataclass(frozen=True)
class _FetchedDocument:
    content: bytes
    media_type: str
    digest: str


def _normalized_host(value: object) -> str:
    _require(isinstance(value, str) and value == value.strip(), "oci_registry_host_forbidden")
    host = value.lower()
    _require(
        host == value
        and 1 < len(host) <= 253
        and not host.endswith(".")
        and "." in host,
        "oci_registry_host_forbidden",
    )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _fail("oci_registry_host_forbidden")
    _require(
        host != "localhost"
        and not host.endswith(_PRIVATE_HOST_SUFFIXES)
        and all(_HOST_LABEL.fullmatch(label) is not None for label in host.split(".")),
        "oci_registry_host_forbidden",
    )
    return host


def _host_set(values: object, *, allow_empty: bool = False) -> frozenset[str]:
    _require(isinstance(values, tuple), "oci_registry_request_invalid")
    _require(
        (allow_empty or bool(values)) and len(values) <= _MAXIMUM_HOSTS,
        "oci_registry_request_invalid",
    )
    hosts = tuple(_normalized_host(value) for value in values)
    _require(len(hosts) == len(set(hosts)), "oci_registry_request_invalid")
    return frozenset(hosts)


def _parse_reference(reference: object) -> tuple[str, str, str]:
    _require(
        isinstance(reference, str)
        and reference == reference.strip()
        and reference.isascii()
        and "://" not in reference
        and reference.count("@") == 1,
        "oci_registry_reference_invalid",
    )
    name, digest = reference.rsplit("@", 1)
    _require(_DIGEST.fullmatch(digest) is not None, "oci_registry_reference_invalid")
    host, separator, repository = name.partition("/")
    _require(bool(separator), "oci_registry_reference_invalid")
    normalized_host = _normalized_host(host)
    segments = repository.split("/")
    _require(
        len(repository) <= 255
        and 1 <= len(segments) <= 16
        and all(_REPOSITORY_SEGMENT.fullmatch(segment) is not None for segment in segments),
        "oci_registry_reference_invalid",
    )
    return normalized_host, repository, digest


def _parse_request(request: OciRegistryAcquisitionRequest) -> _ParsedRequest:
    _require(
        isinstance(request, OciRegistryAcquisitionRequest),
        "oci_registry_request_invalid",
    )
    reference_host, repository, digest = _parse_reference(request.reference)
    reference_hosts = _host_set(request.allowed_reference_hosts)
    api_hosts = _host_set(request.allowed_registry_api_hosts)
    token_hosts = _host_set(request.allowed_token_hosts)
    blob_hosts = _host_set(request.allowed_blob_hosts, allow_empty=True)
    api_host = _normalized_host(request.registry_api_host)
    _require(reference_host in reference_hosts, "oci_registry_host_forbidden")
    _require(api_host in api_hosts, "oci_registry_host_forbidden")
    pairs = request.platform_manifest_digests
    _require(
        isinstance(pairs, tuple) and 1 <= len(pairs) <= len(_PLATFORMS),
        "oci_registry_request_invalid",
    )
    platform_digests: dict[str, str] = {}
    for pair in pairs:
        _require(
            isinstance(pair, tuple)
            and len(pair) == 2
            and isinstance(pair[0], str)
            and pair[0] in _PLATFORMS
            and isinstance(pair[1], str)
            and _DIGEST.fullmatch(pair[1]) is not None
            and pair[0] not in platform_digests,
            "oci_registry_request_invalid",
        )
        platform_digests[pair[0]] = pair[1]
    _require(
        type(request.maximum_redirects) is int
        and 0 <= request.maximum_redirects <= _MAXIMUM_REDIRECTS,
        "oci_registry_request_invalid",
    )
    return _ParsedRequest(
        reference_host=reference_host,
        repository=repository,
        reference_digest=digest,
        platform_digests=MappingProxyType(dict(sorted(platform_digests.items()))),
        registry_api_host=api_host,
        reference_hosts=reference_hosts,
        api_hosts=api_hosts,
        token_hosts=token_hosts,
        blob_hosts=blob_hosts,
        maximum_redirects=request.maximum_redirects,
    )


def _validate_url(url: object, hosts: frozenset[str]) -> None:
    _require(
        isinstance(url, str)
        and url == url.strip()
        and url.isascii()
        and "#" not in url
        and all(ord(character) >= 0x20 for character in url),
        "oci_registry_url_forbidden",
    )
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise OciRegistryAcquisitionError("oci_registry_url_forbidden") from error
    _require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and isinstance(hostname, str)
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and parsed.path.startswith("/"),
        "oci_registry_url_forbidden",
    )
    _require(_normalized_host(hostname) in hosts, "oci_registry_host_forbidden")


def _public_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise OciRegistryAcquisitionError("oci_registry_address_forbidden") from error
    _require(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified,
        "oci_registry_address_forbidden",
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
        raise OciRegistryAcquisitionError("oci_registry_dns_failed") from error
    _require(bool(answers), "oci_registry_dns_failed")
    addresses: list[str] = []
    for family, socket_type, protocol, _canonical_name, socket_address in answers:
        _require(
            family in {socket.AF_INET, socket.AF_INET6}
            and socket_type == socket.SOCK_STREAM
            and protocol == socket.IPPROTO_TCP
            and isinstance(socket_address, tuple)
            and bool(socket_address),
            "oci_registry_address_forbidden",
        )
        address = _public_address(str(socket_address[0]))
        if address not in addresses:
            addresses.append(address)
    _require(bool(addresses), "oci_registry_dns_failed")
    return tuple(addresses)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS with one public DNS answer pinned and the original TLS name kept."""

    def __init__(self, hostname: str, address: str, *, context: ssl.SSLContext) -> None:
        super().__init__(hostname, port=443, timeout=60, context=context)
        self._pinned_address = _public_address(address)

    def connect(self) -> None:
        raw_socket: socket.socket | None = None
        tls_socket: socket.socket | None = None
        try:
            raw_socket = socket.create_connection(
                (self._pinned_address, 443), self.timeout, self.source_address
            )
            tls_socket = self._context.wrap_socket(raw_socket, server_hostname=self.host)
            raw_socket = None
            peer = tls_socket.getpeername()
            _require(
                isinstance(peer, tuple)
                and bool(peer)
                and _public_address(str(peer[0])) == self._pinned_address,
                "oci_registry_peer_mismatch",
            )
            self.sock = tls_socket
            tls_socket = None
        except BaseException:
            if tls_socket is not None:
                tls_socket.close()
            if raw_socket is not None:
                raw_socket.close()
            raise


def _request_once(
    url: str, headers: tuple[tuple[str, str], ...]
) -> tuple[_PinnedHTTPSConnection, http.client.HTTPResponse]:
    parsed = urllib.parse.urlsplit(url)
    _require(isinstance(parsed.hostname, str), "oci_registry_url_forbidden")
    addresses = _resolve_public_addresses(parsed.hostname)
    connection = _PinnedHTTPSConnection(
        parsed.hostname, addresses[0], context=ssl.create_default_context()
    )
    path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
    request_headers = {
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Host": parsed.hostname,
        "User-Agent": "StreamScapeTV-ci-workflows-oci-registry/1.0",
    }
    for name, value in headers:
        _require(
            isinstance(name, str)
            and isinstance(value, str)
            and name.lower() not in {"host", "connection", "accept-encoding"}
            and "\r" not in name + value
            and "\n" not in name + value,
            "oci_registry_request_invalid",
        )
        request_headers[name] = value
    try:
        connection.request("GET", path, headers=request_headers)
        return connection, connection.getresponse()
    except BaseException:
        connection.close()
        raise


@contextlib.contextmanager
def _open_https(request: OciRegistryHttpRequest):
    initial_hosts = frozenset(request.initial_hosts)
    redirect_hosts = frozenset(request.redirect_hosts)
    _validate_url(request.url, initial_hosts)
    current_url = request.url
    redirects: list[str] = []
    headers = request.headers
    while True:
        connection: _PinnedHTTPSConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            connection, response = _request_once(current_url, headers)
            if response.status not in _REDIRECT_STATUSES:
                yield OciRegistryHttpResponse(
                    stream=response,
                    final_url=current_url,
                    redirect_urls=tuple(redirects),
                    status_code=response.status,
                    headers=tuple(response.getheaders()),
                )
                return
            _require(
                len(redirects) < request.maximum_redirects,
                "oci_registry_redirect_limit",
            )
            location = response.getheader("Location")
            _require(
                isinstance(location, str) and bool(location),
                "oci_registry_redirect_invalid",
            )
            redirect_url = urllib.parse.urljoin(current_url, location)
            _validate_url(redirect_url, redirect_hosts)
            redirects.append(redirect_url)
            current_url = redirect_url
            # Bearer authority is constrained to the registry API origin.
            headers = tuple(
                (name, value)
                for name, value in headers
                if name.casefold() != "authorization"
            )
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()


def _headers(values: Sequence[tuple[str, str]]) -> Mapping[str, str]:
    result: dict[str, str] = {}
    for name, value in values:
        _require(
            isinstance(name, str) and isinstance(value, str),
            "oci_registry_response_invalid",
        )
        normalized = name.casefold()
        _require(normalized not in result, "oci_registry_response_invalid")
        result[normalized] = value.strip()
    return MappingProxyType(result)


def _read_bounded(stream: BinaryIO, maximum: int, too_large_code: str) -> bytes:
    content = bytearray()
    while True:
        try:
            block = stream.read(min(_CHUNK_BYTES, maximum + 1 - len(content)))
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            raise OciRegistryAcquisitionError("oci_registry_download_failed") from error
        except Exception as error:
            raise OciRegistryAcquisitionError("oci_registry_download_failed") from error
        _require(isinstance(block, (bytes, bytearray)), "oci_registry_download_failed")
        if not block:
            return bytes(content)
        content.extend(block)
        _require(len(content) <= maximum, too_large_code)


def _validate_response_path(
    response: OciRegistryHttpResponse,
    *,
    initial_hosts: frozenset[str],
    redirect_hosts: frozenset[str],
    maximum_redirects: int,
) -> None:
    _require(
        isinstance(response, OciRegistryHttpResponse)
        and isinstance(response.redirect_urls, tuple)
        and type(response.status_code) is int,
        "oci_registry_response_invalid",
    )
    _require(
        len(response.redirect_urls) <= maximum_redirects,
        "oci_registry_redirect_limit",
    )
    if response.redirect_urls:
        for url in response.redirect_urls:
            _validate_url(url, redirect_hosts)
        _validate_url(response.final_url, redirect_hosts)
    else:
        _validate_url(response.final_url, initial_hosts)


def _open_response(
    transport: OciRegistryTransport,
    *,
    url: str,
    headers: tuple[tuple[str, str], ...],
    initial_hosts: frozenset[str],
    redirect_hosts: frozenset[str],
    maximum_redirects: int,
) -> ContextManager[OciRegistryHttpResponse]:
    request = OciRegistryHttpRequest(
        url=url,
        headers=headers,
        initial_hosts=tuple(sorted(initial_hosts)),
        redirect_hosts=tuple(sorted(redirect_hosts)),
        maximum_redirects=maximum_redirects,
    )
    @contextlib.contextmanager
    def open_checked():
        try:
            with transport(request) as response:
                yield response
        except OciRegistryAcquisitionError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            raise OciRegistryAcquisitionError("oci_registry_download_failed") from error
        except Exception as error:
            raise OciRegistryAcquisitionError("oci_registry_download_failed") from error

    return open_checked()


def _parse_json(content: bytes, code: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        _fail(code)

    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except OciRegistryAcquisitionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciRegistryAcquisitionError(code) from error


def _parse_bearer_challenge(
    value: object, *, repository: str, token_hosts: frozenset[str]
) -> tuple[str, str, str]:
    _require(isinstance(value, str) and value.startswith("Bearer "), "oci_registry_auth_invalid")
    fields: dict[str, str] = {}
    for item in value.removeprefix("Bearer ").split(","):
        name, separator, quoted = item.strip().partition("=")
        _require(
            bool(separator)
            and name in {"realm", "service", "scope"}
            and name not in fields
            and len(quoted) >= 2
            and quoted.startswith('"')
            and quoted.endswith('"')
            and '"' not in quoted[1:-1]
            and "\\" not in quoted,
            "oci_registry_auth_invalid",
        )
        fields[name] = quoted[1:-1]
    _require(set(fields) == {"realm", "service", "scope"}, "oci_registry_auth_invalid")
    realm = fields["realm"]
    _validate_url(realm, token_hosts)
    parsed_realm = urllib.parse.urlsplit(realm)
    _require(not parsed_realm.query, "oci_registry_auth_invalid")
    service = fields["service"]
    scope = fields["scope"]
    _require(_TOKEN_SERVICE.fullmatch(service) is not None, "oci_registry_auth_invalid")
    _require(scope == f"repository:{repository}:pull", "oci_registry_auth_invalid")
    return realm, service, scope


def _anonymous_token(
    challenge: str,
    *,
    parsed: _ParsedRequest,
    transport: OciRegistryTransport,
) -> str:
    realm, service, scope = _parse_bearer_challenge(
        challenge, repository=parsed.repository, token_hosts=parsed.token_hosts
    )
    token_url = realm + "?" + urllib.parse.urlencode(
        {"service": service, "scope": scope}, quote_via=urllib.parse.quote
    )
    with _open_response(
        transport,
        url=token_url,
        headers=(("Accept", "application/json"),),
        initial_hosts=parsed.token_hosts,
        redirect_hosts=parsed.token_hosts,
        maximum_redirects=parsed.maximum_redirects,
    ) as response:
        _validate_response_path(
            response,
            initial_hosts=parsed.token_hosts,
            redirect_hosts=parsed.token_hosts,
            maximum_redirects=parsed.maximum_redirects,
        )
        _require(response.status_code == 200, "oci_registry_auth_failed")
        response_headers = _headers(response.headers)
        token_media_type = response_headers.get("content-type", "").split(";", 1)[0].strip()
        _require(token_media_type == "application/json", "oci_registry_auth_invalid")
        content = _read_bounded(response.stream, _MAXIMUM_TOKEN_BYTES, "oci_registry_auth_invalid")
    value = _parse_json(content, "oci_registry_auth_invalid")
    _require(isinstance(value, Mapping), "oci_registry_auth_invalid")
    _require(
        set(value).issubset({"token", "access_token", "expires_in", "issued_at"}),
        "oci_registry_auth_invalid",
    )
    token = value.get("token")
    access_token = value.get("access_token")
    _require(
        isinstance(token, str) or isinstance(access_token, str),
        "oci_registry_auth_invalid",
    )
    if token is None:
        token = access_token
    if access_token is not None:
        _require(token == access_token, "oci_registry_auth_invalid")
    _require(isinstance(token, str) and _TOKEN.fullmatch(token) is not None, "oci_registry_auth_invalid")
    expires = value.get("expires_in")
    _require(
        expires is None or type(expires) is int and 1 <= expires <= 86400,
        "oci_registry_auth_invalid",
    )
    issued = value.get("issued_at")
    _require(issued is None or isinstance(issued, str) and len(issued) <= 64, "oci_registry_auth_invalid")
    return token


def _manifest_url(parsed: _ParsedRequest, digest: str) -> str:
    return f"https://{parsed.registry_api_host}/v2/{parsed.repository}/manifests/{digest}"


def _blob_url(parsed: _ParsedRequest, digest: str) -> str:
    return f"https://{parsed.registry_api_host}/v2/{parsed.repository}/blobs/{digest}"


def _fetch_manifest(
    digest: str,
    *,
    parsed: _ParsedRequest,
    transport: OciRegistryTransport,
    token: str | None,
) -> tuple[_FetchedDocument | None, str | None]:
    headers: tuple[tuple[str, str], ...] = (("Accept", _MANIFEST_ACCEPT),)
    if token is not None:
        headers += (("Authorization", f"Bearer {token}"),)
    with _open_response(
        transport,
        url=_manifest_url(parsed, digest),
        headers=headers,
        initial_hosts=parsed.api_hosts,
        redirect_hosts=parsed.api_hosts,
        maximum_redirects=parsed.maximum_redirects,
    ) as response:
        _validate_response_path(
            response,
            initial_hosts=parsed.api_hosts,
            redirect_hosts=parsed.api_hosts,
            maximum_redirects=parsed.maximum_redirects,
        )
        response_headers = _headers(response.headers)
        if response.status_code == 401 and token is None:
            challenge = response_headers.get("www-authenticate")
            _require(isinstance(challenge, str), "oci_registry_auth_invalid")
            _read_bounded(response.stream, _MAXIMUM_TOKEN_BYTES, "oci_registry_response_invalid")
            return None, challenge
        _require(response.status_code == 200, "oci_registry_manifest_failed")
        media_type = response_headers.get("content-type", "").split(";", 1)[0].strip()
        _require(media_type in {_INDEX_MEDIA_TYPE, _MANIFEST_MEDIA_TYPE}, "oci_registry_media_type_invalid")
        declared_digest = response_headers.get("docker-content-digest")
        _require(declared_digest == digest, "oci_registry_digest_mismatch")
        content = _read_bounded(
            response.stream, _MAXIMUM_MANIFEST_BYTES, "oci_registry_manifest_too_large"
        )
    actual_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    _require(actual_digest == digest, "oci_registry_digest_mismatch")
    return _FetchedDocument(content, media_type, actual_digest), None


def _authenticated_manifest(
    digest: str,
    *,
    parsed: _ParsedRequest,
    transport: OciRegistryTransport,
    token: str | None,
) -> tuple[_FetchedDocument, str | None]:
    document, challenge = _fetch_manifest(
        digest, parsed=parsed, transport=transport, token=token
    )
    if document is not None:
        return document, token
    _require(token is None and challenge is not None, "oci_registry_auth_failed")
    acquired_token = _anonymous_token(challenge, parsed=parsed, transport=transport)
    document, repeated_challenge = _fetch_manifest(
        digest, parsed=parsed, transport=transport, token=acquired_token
    )
    _require(document is not None and repeated_challenge is None, "oci_registry_auth_failed")
    return document, acquired_token


def _descriptor(value: object, media_types: frozenset[str]) -> tuple[str, str, int]:
    _require(isinstance(value, Mapping), "oci_registry_descriptor_invalid")
    media_type = value.get("mediaType")
    digest = value.get("digest")
    size = value.get("size")
    _require(
        isinstance(media_type, str)
        and media_type in media_types
        and isinstance(digest, str)
        and _DIGEST.fullmatch(digest) is not None
        and type(size) is int
        and size >= 0,
        "oci_registry_descriptor_invalid",
    )
    return media_type, digest, size


def _platform(value: object) -> str:
    _require(isinstance(value, Mapping), "oci_registry_platform_invalid")
    os_name = value.get("os")
    architecture = value.get("architecture")
    variant = value.get("variant")
    _require(
        isinstance(os_name, str)
        and isinstance(architecture, str)
        and (variant is None or isinstance(variant, str)),
        "oci_registry_platform_invalid",
    )
    if os_name == "linux" and architecture == "arm64" and variant is None:
        return "linux/arm64/v8"
    return f"{os_name}/{architecture}" + (f"/{variant}" if variant else "")


def _index_descriptors(
    document: _FetchedDocument, expected: Mapping[str, str]
) -> Mapping[str, Mapping[str, object]]:
    value = _parse_json(document.content, "oci_registry_manifest_invalid")
    _require(
        isinstance(value, Mapping)
        and value.get("schemaVersion") == 2
        and value.get("mediaType") == _INDEX_MEDIA_TYPE
        and isinstance(value.get("manifests"), list),
        "oci_registry_manifest_invalid",
    )
    selected: dict[str, Mapping[str, object]] = {}
    for descriptor in value["manifests"]:
        if not isinstance(descriptor, Mapping):
            _fail("oci_registry_descriptor_invalid")
        media_type, digest, _size = _descriptor(
            descriptor, frozenset({_MANIFEST_MEDIA_TYPE})
        )
        del media_type
        declared = _platform(descriptor.get("platform"))
        if declared in expected:
            _require(declared not in selected, "oci_registry_platform_ambiguous")
            _require(digest == expected[declared], "oci_registry_lock_mismatch")
            selected[declared] = descriptor
    _require(set(selected) == set(expected), "oci_registry_platform_missing")
    return MappingProxyType(dict(sorted(selected.items())))


def _manifest_blob_descriptors(
    document: _FetchedDocument,
) -> tuple[Mapping[str, object], tuple[Mapping[str, object], ...]]:
    _require(document.media_type == _MANIFEST_MEDIA_TYPE, "oci_registry_media_type_invalid")
    value = _parse_json(document.content, "oci_registry_manifest_invalid")
    _require(
        isinstance(value, Mapping)
        and value.get("schemaVersion") == 2
        and value.get("mediaType") in {None, _MANIFEST_MEDIA_TYPE}
        and isinstance(value.get("config"), Mapping)
        and isinstance(value.get("layers"), list),
        "oci_registry_manifest_invalid",
    )
    config = value["config"]
    _descriptor(config, frozenset({_CONFIG_MEDIA_TYPE}))
    _require(len(value["layers"]) <= _MAXIMUM_LAYERS, "oci_registry_manifest_invalid")
    layers: list[Mapping[str, object]] = []
    for layer in value["layers"]:
        _descriptor(layer, _LAYER_MEDIA_TYPES)
        layers.append(layer)
    return config, tuple(layers)


def _create_layout(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
        (path / "blobs").mkdir(mode=0o700)
        (path / "blobs" / "sha256").mkdir(mode=0o700)
    except OSError as error:
        raise OciRegistryAcquisitionError("oci_registry_state_invalid") from error


def _atomic_bytes(path: Path, content: bytes, mode: int = 0o444) -> None:
    temporary = path.with_name(f".{path.name}.partial")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            _require(written > 0, "oci_registry_write_failed")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except OciRegistryAcquisitionError:
        raise
    except OSError as error:
        raise OciRegistryAcquisitionError("oci_registry_write_failed") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _blob_path(layout: Path, digest: str) -> Path:
    return layout / "blobs" / "sha256" / digest.removeprefix("sha256:")


def _write_blob(layout: Path, document: _FetchedDocument) -> None:
    _atomic_bytes(_blob_path(layout, document.digest), document.content)


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def _initialize_layout(layout: Path, descriptor: Mapping[str, object]) -> None:
    _atomic_bytes(layout / "oci-layout", b'{"imageLayoutVersion":"1.0.0"}\n')
    _atomic_bytes(
        layout / "index.json",
        _canonical_bytes(
            {
                "schemaVersion": 2,
                "mediaType": _INDEX_MEDIA_TYPE,
                "manifests": [dict(descriptor)],
            }
        ),
    )


def _stream_blob(
    layout: Path,
    descriptor: Mapping[str, object],
    *,
    media_types: frozenset[str],
    maximum_bytes: int,
    parsed: _ParsedRequest,
    transport: OciRegistryTransport,
    token: str | None,
) -> int:
    _media_type, digest, expected_size = _descriptor(descriptor, media_types)
    _require(expected_size <= maximum_bytes, "oci_registry_blob_too_large")
    destination = _blob_path(layout, digest)
    if destination.exists():
        try:
            info = destination.stat()
        except OSError as error:
            raise OciRegistryAcquisitionError("oci_registry_state_invalid") from error
        _require(
            not destination.is_symlink()
            and stat.S_ISREG(info.st_mode)
            and info.st_size == expected_size,
            "oci_registry_state_invalid",
        )
        return 0
    temporary = destination.with_name(f".{destination.name}.partial")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    output: int | None = None
    try:
        output = os.open(temporary, flags, 0o600)
        headers: tuple[tuple[str, str], ...] = (("Accept", "application/octet-stream"),)
        if token is not None:
            headers += (("Authorization", f"Bearer {token}"),)
        with _open_response(
            transport,
            url=_blob_url(parsed, digest),
            headers=headers,
            initial_hosts=parsed.api_hosts,
            redirect_hosts=parsed.blob_hosts,
            maximum_redirects=parsed.maximum_redirects,
        ) as response:
            _validate_response_path(
                response,
                initial_hosts=parsed.api_hosts,
                redirect_hosts=parsed.blob_hosts,
                maximum_redirects=parsed.maximum_redirects,
            )
            _require(response.status_code == 200, "oci_registry_blob_failed")
            response_headers = _headers(response.headers)
            length = response_headers.get("content-length")
            if length is not None:
                _require(length.isdecimal() and int(length) == expected_size, "oci_registry_size_mismatch")
            hasher = hashlib.sha256()
            actual_size = 0
            while True:
                try:
                    block = response.stream.read(_CHUNK_BYTES)
                except (OSError, http.client.HTTPException, ssl.SSLError) as error:
                    raise OciRegistryAcquisitionError("oci_registry_download_failed") from error
                except Exception as error:
                    raise OciRegistryAcquisitionError("oci_registry_download_failed") from error
                _require(isinstance(block, (bytes, bytearray)), "oci_registry_download_failed")
                if not block:
                    break
                actual_size += len(block)
                _require(actual_size <= expected_size, "oci_registry_size_mismatch")
                hasher.update(block)
                view = memoryview(block)
                while view:
                    written = os.write(output, view)
                    _require(written > 0, "oci_registry_write_failed")
                    view = view[written:]
        _require(actual_size == expected_size, "oci_registry_size_mismatch")
        _require(f"sha256:{hasher.hexdigest()}" == digest, "oci_registry_digest_mismatch")
        os.fsync(output)
        os.fchmod(output, 0o444)
        os.close(output)
        output = None
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
        return actual_size
    except OciRegistryAcquisitionError:
        raise
    except OSError as error:
        raise OciRegistryAcquisitionError("oci_registry_write_failed") from error
    finally:
        if output is not None:
            os.close(output)
        temporary.unlink(missing_ok=True)


def _materialize_manifest_layout(
    layout: Path,
    root_descriptor: Mapping[str, object],
    manifest: _FetchedDocument,
    *,
    parsed: _ParsedRequest,
    transport: OciRegistryTransport,
    token: str | None,
    total_bytes: list[int],
) -> None:
    _require(
        total_bytes[0] + len(manifest.content) <= _MAXIMUM_TOTAL_BYTES,
        "oci_registry_total_too_large",
    )
    _create_layout(layout)
    _initialize_layout(layout, root_descriptor)
    _write_blob(layout, manifest)
    total_bytes[0] += len(manifest.content)
    config, layers = _manifest_blob_descriptors(manifest)
    _config_media_type, _config_digest, config_size = _descriptor(
        config, frozenset({_CONFIG_MEDIA_TYPE})
    )
    _require(
        total_bytes[0] + config_size <= _MAXIMUM_TOTAL_BYTES,
        "oci_registry_total_too_large",
    )
    total_bytes[0] += _stream_blob(
        layout,
        config,
        media_types=frozenset({_CONFIG_MEDIA_TYPE}),
        maximum_bytes=_MAXIMUM_CONFIG_BYTES,
        parsed=parsed,
        transport=transport,
        token=token,
    )
    _require(total_bytes[0] <= _MAXIMUM_TOTAL_BYTES, "oci_registry_total_too_large")
    for layer in layers:
        _layer_media_type, _layer_digest, layer_size = _descriptor(
            layer, _LAYER_MEDIA_TYPES
        )
        _require(
            total_bytes[0] + layer_size <= _MAXIMUM_TOTAL_BYTES,
            "oci_registry_total_too_large",
        )
        total_bytes[0] += _stream_blob(
            layout,
            layer,
            media_types=_LAYER_MEDIA_TYPES,
            maximum_bytes=_MAXIMUM_LAYER_BYTES,
            parsed=parsed,
            transport=transport,
            token=token,
        )
        _require(total_bytes[0] <= _MAXIMUM_TOTAL_BYTES, "oci_registry_total_too_large")


def acquire_oci_base(
    request: OciRegistryAcquisitionRequest,
    *,
    registered_state: Path,
    transport: OciRegistryTransport | None = None,
) -> AcquiredOciBase:
    """Acquire an exact anonymous OCI root and the requested complete children.

    ``registered_state`` must not exist and its existing parent must be an
    absolute, non-symlinked private directory.  On any failure this function
    removes the state directory it created.
    """

    parsed = _parse_request(request)
    _require(isinstance(registered_state, Path) and registered_state.is_absolute(), "oci_registry_state_invalid")
    parent = registered_state.parent
    try:
        parent_info = parent.stat()
        _require(
            parent.resolve(strict=True) == parent
            and not parent.is_symlink()
            and stat.S_ISDIR(parent_info.st_mode)
            and parent_info.st_mode & 0o022 == 0,
            "oci_registry_state_invalid",
        )
        registered_state.mkdir(mode=0o700)
    except OciRegistryAcquisitionError:
        raise
    except OSError as error:
        raise OciRegistryAcquisitionError("oci_registry_state_invalid") from error
    opener = transport or _open_https
    root_layout = registered_state / "root"
    children: dict[str, Path] = {}
    total_bytes = [0]
    try:
        root, token = _authenticated_manifest(
            parsed.reference_digest,
            parsed=parsed,
            transport=opener,
            token=None,
        )
        root_descriptor: Mapping[str, object] = {
            "mediaType": root.media_type,
            "digest": root.digest,
            "size": len(root.content),
        }
        if root.media_type == _INDEX_MEDIA_TYPE:
            selected = _index_descriptors(root, parsed.platform_digests)
            _create_layout(root_layout)
            _initialize_layout(root_layout, root_descriptor)
            _write_blob(root_layout, root)
            total_bytes[0] += len(root.content)
            children_root = registered_state / "children"
            children_root.mkdir(mode=0o700)
            for platform, descriptor in selected.items():
                _media_type, digest, expected_size = _descriptor(
                    descriptor, frozenset({_MANIFEST_MEDIA_TYPE})
                )
                manifest, token = _authenticated_manifest(
                    digest, parsed=parsed, transport=opener, token=token
                )
                _require(
                    manifest.media_type == _MANIFEST_MEDIA_TYPE
                    and len(manifest.content) == expected_size,
                    "oci_registry_size_mismatch",
                )
                child = children_root / platform.replace("/", "-")
                _materialize_manifest_layout(
                    child,
                    descriptor,
                    manifest,
                    parsed=parsed,
                    transport=opener,
                    token=token,
                    total_bytes=total_bytes,
                )
                children[platform] = child
        else:
            _require(len(parsed.platform_digests) == 1, "oci_registry_platform_invalid")
            expected_digest = next(iter(parsed.platform_digests.values()))
            _require(expected_digest == root.digest, "oci_registry_lock_mismatch")
            _materialize_manifest_layout(
                root_layout,
                root_descriptor,
                root,
                parsed=parsed,
                transport=opener,
                token=token,
                total_bytes=total_bytes,
            )
        _require(total_bytes[0] <= _MAXIMUM_TOTAL_BYTES, "oci_registry_total_too_large")
        return AcquiredOciBase(
            root_layout=root_layout,
            child_layouts=MappingProxyType(dict(sorted(children.items()))),
            root_digest=root.digest,
            root_media_type=root.media_type,
        )
    except BaseException:
        try:
            shutil.rmtree(registered_state)
        except OSError as cleanup_error:
            raise OciRegistryAcquisitionError("oci_registry_cleanup_failed") from cleanup_error
        raise
