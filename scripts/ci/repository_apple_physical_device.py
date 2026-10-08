#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, BinaryIO, NoReturn
import urllib.error
import urllib.parse
import urllib.request

LOCK_PATH = Path('/tmp/streamscapetv-central-apple-physical-device-v1.lock')
ENTRYPOINT = Path('.ci/device-test.sh')
CONTEXT_NAME = 'central-apple-physical-device-context.json'
SECRET_NAME = 'central-apple-physical-device-secrets.txt'
INVENTORY_NAME = 'central-apple-physical-device-inventory.json'
SIGNING_STATE_NAME = 'central-apple-development-signing-state.json'
SIGNING_KEYCHAIN_NAME = 'central-apple-development-signing.keychain-db'
SIGNING_API_KEY_NAME = 'central-apple-app-store-connect-key.p8'
SIGNING_STATE_SCHEMA = 'streamscape-central-apple-development-signing-v1'
API_BASE = 'https://api.appstoreconnect.apple.com'
IDENTIFIER_RE = re.compile(r'[A-Za-z0-9-]{16,128}')
IPHONE_MODEL_RE = re.compile(r'iPhone[0-9]+,[0-9]+')
TEAM_RE = re.compile(r'[A-Z0-9]{10}')
KEY_ID_RE = re.compile(r'[A-Za-z0-9]{10}')
ISSUER_RE = re.compile(r'[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}')
CERT_ID_RE = re.compile(r'[A-Za-z0-9_-]{1,128}')
IDENTITY_SHA1_RE = re.compile(r'[0-9A-Fa-f]{40}')
MAX_API_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CERTIFICATE_BYTES = 65536
MAX_STATE_BYTES = 16384
AMBIGUOUS_RECONCILIATION_ATTEMPTS = 6
AMBIGUOUS_RECONCILIATION_DELAY_SECONDS = 5


class DeviceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        ambiguous_outcome: bool = False,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.ambiguous_outcome = ambiguous_outcome
        self.http_status = http_status


def fail(message: str) -> NoReturn:
    raise DeviceError(message)


def _parse_utc(value: str, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith('Z'):
        fail(f'{label} must be one UTC timestamp')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as exc:
        raise DeviceError(f'{label} must be one UTC timestamp') from exc
    if parsed.tzinfo is None:
        fail(f'{label} must be one UTC timestamp')
    return parsed.astimezone(timezone.utc)


def _runner_temp() -> Path:
    runner_temp_raw = os.environ.get('RUNNER_TEMP', '')
    if not runner_temp_raw:
        fail('Apple physical-device execution requires one Central runner temp directory')
    runner_temp = Path(runner_temp_raw)
    if not runner_temp.is_absolute() or runner_temp.is_symlink() or not runner_temp.is_dir():
        fail('Apple physical-device runner temp directory is invalid')
    return runner_temp


def validate_environment() -> tuple[Path, Path, datetime]:
    if os.environ.get('CI_HOST_OS') != 'macos':
        fail('Apple physical-device execution requires macOS')
    if os.environ.get('CI_HOST_CLASS') != 'macos-high-capacity':
        fail('Apple physical-device execution requires the reviewed high-capacity macOS host')
    if os.environ.get('CI_OPERATION') != 'device-test':
        fail('Apple physical-device execution requires repository device-test')
    if os.environ.get('CI_APPLE_PHYSICAL_DEVICE_AUTHORIZED') != 'true':
        fail('Apple physical-device execution is not authorized')

    authorized_at = _parse_utc(
        os.environ.get('CI_APPLE_PHYSICAL_DEVICE_AUTHORIZED_AT', ''),
        label='Apple physical-device authorization start',
    )
    expires_at = _parse_utc(
        os.environ.get('CI_APPLE_PHYSICAL_DEVICE_EXPIRES_AT', ''),
        label='Apple physical-device authorization expiry',
    )
    now = datetime.now(timezone.utc)
    if expires_at <= authorized_at or (expires_at - authorized_at).total_seconds() > 86400:
        fail('Apple physical-device authorization window is invalid')
    if now < authorized_at or now >= expires_at:
        fail('Apple physical-device authorization is not currently active')

    try:
        inputs = json.loads(os.environ.get('CI_INPUTS_JSON', ''))
    except json.JSONDecodeError as exc:
        raise DeviceError('repository device-test inputs are invalid') from exc
    if (
        not isinstance(inputs, dict)
        or inputs.get('schemaVersion') != 1
        or not isinstance(inputs.get('inputs'), dict)
        or inputs['inputs'].get('device_platform') != 'ios'
        or not isinstance(inputs['inputs'].get('test_selectors'), list)
        or not inputs['inputs']['test_selectors']
    ):
        fail('repository device-test inputs are not one bounded iOS physical-device request')

    runner_temp = _runner_temp()
    return runner_temp / CONTEXT_NAME, runner_temp / SECRET_NAME, expires_at


def validate_entrypoint(source_root: Path) -> Path:
    if source_root.is_symlink() or not source_root.is_dir():
        fail('repository device-test source root is invalid')
    entrypoint = source_root / ENTRYPOINT
    completed = subprocess.run(
        ['git', '-C', str(source_root), 'ls-files', '-s', '--', ENTRYPOINT.as_posix()],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        fail('repository device-test requires one tracked .ci/device-test.sh entrypoint')
    fields = completed.stdout.strip().split()
    if len(fields) < 4 or fields[0] != '100755':
        fail('repository device-test entrypoint must be tracked executable mode 100755')
    if entrypoint.is_symlink() or not entrypoint.is_file() or not os.access(entrypoint, os.X_OK):
        fail('repository device-test entrypoint must be one executable regular file')
    return entrypoint


def _eligible_device(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    hardware = value.get('hardwareProperties')
    properties = value.get('deviceProperties')
    connection = value.get('connectionProperties')
    if not isinstance(hardware, dict) or not isinstance(properties, dict) or not isinstance(connection, dict):
        return None
    if hardware.get('platform') != 'iOS' or hardware.get('reality') != 'physical':
        return None
    if connection.get('pairingState') != 'paired':
        return None
    if properties.get('developerModeStatus') != 'enabled':
        return None
    product_type = hardware.get('productType')
    if not isinstance(product_type, str) or IPHONE_MODEL_RE.fullmatch(product_type) is None:
        return None
    transport = connection.get('transportType')
    if not isinstance(transport, str) or transport.casefold() not in {'usb', 'wired'}:
        return None
    identifier = hardware.get('udid') or value.get('identifier')
    if not isinstance(identifier, str) or IDENTIFIER_RE.fullmatch(identifier) is None:
        return None
    return identifier


def _cleanup_private_inventory(inventory_path: Path) -> None:
    if inventory_path.is_symlink() or inventory_path.is_file():
        inventory_path.unlink()
    elif inventory_path.exists():
        fail('Apple physical-device discovery private inventory is not a regular file')
    if inventory_path.exists() or inventory_path.is_symlink():
        fail('Apple physical-device discovery private inventory cleanup failed')


def discover_attached_iphone(runner_temp: Path) -> str:
    inventory_path = runner_temp / INVENTORY_NAME
    if inventory_path.exists() or inventory_path.is_symlink():
        fail('Apple physical-device discovery private inventory already exists')
    payload: object | None = None
    try:
        try:
            completed = subprocess.run(
                ['xcrun', 'devicectl', 'list', 'devices', '--json-output', str(inventory_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeviceError('Apple physical-device discovery failed before product execution') from exc
        if completed.returncode != 0:
            fail('Apple physical-device discovery failed before product execution')
        if inventory_path.is_symlink() or not inventory_path.is_file():
            fail('Apple physical-device discovery returned no private inventory')
        if inventory_path.stat().st_size > 1024 * 1024:
            fail('Apple physical-device discovery inventory exceeds the reviewed bound')
        try:
            payload = json.loads(inventory_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DeviceError('Apple physical-device discovery returned invalid data') from exc
    finally:
        _cleanup_private_inventory(inventory_path)

    if not isinstance(payload, dict):
        fail('Apple physical-device discovery returned invalid data')
    result = payload.get('result')
    rows = result.get('devices') if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) > 64:
        fail('Apple physical-device discovery returned invalid data')
    devices = sorted({identifier for item in rows if (identifier := _eligible_device(item)) is not None})
    if len(devices) != 1:
        fail(f'Apple physical-device discovery requires exactly one eligible attached iPhone; found {len(devices)}')
    return devices[0]


def _open_private_file(path: Path, *, exclusive: bool) -> int:
    if path.is_symlink():
        fail('Apple physical-device private state contains a symlink')
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if exclusive:
        flags |= os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    mode = os.fstat(fd).st_mode
    if not stat.S_ISREG(mode):
        os.close(fd)
        fail('Apple physical-device private state is not a regular file')
    return fd


def write_private(path: Path, data: bytes, *, exclusive: bool = True, max_bytes: int = 4096) -> None:
    if len(data) > max_bytes:
        fail('Apple physical-device private state exceeds the reviewed bound')
    fd = _open_private_file(path, exclusive=exclusive)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare_private_state(context_path: Path, secret_path: Path, identifier: str) -> None:
    for path in (context_path, secret_path):
        if path.exists() or path.is_symlink():
            fail('Apple physical-device private state already exists')
    context = {
        'schemaVersion': 1,
        'platform': 'ios',
        'deviceClass': 'iphone',
        'identifier': identifier,
    }
    payload = (json.dumps(context, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    write_private(context_path, payload)
    secret_payload = (identifier + '\n' + str(context_path) + '\n').encode('utf-8')
    write_private(secret_path, secret_payload)


def append_dynamic_secrets(secret_path: Path, values: tuple[str, ...]) -> None:
    if secret_path.is_symlink() or not secret_path.is_file():
        fail('Apple physical-device dynamic secret file is unavailable')
    current = secret_path.read_bytes()
    additions = b''.join((value + '\n').encode('utf-8') for value in values if value)
    if len(current) + len(additions) > 8192:
        fail('Apple physical-device dynamic secret evidence exceeds the reviewed bound')
    write_private(secret_path, current + additions, exclusive=False, max_bytes=8192)


def _redact(data: bytes, secrets_to_mask: tuple[bytes, ...]) -> bytes:
    for secret in secrets_to_mask:
        if secret:
            data = data.replace(secret, b'*' * len(secret))
    return data


def stream_redacted(handle: BinaryIO, secrets_to_mask: tuple[bytes, ...]) -> None:
    bounded = tuple(secret for secret in secrets_to_mask if secret)
    if not bounded:
        fail('Apple physical-device redaction requires at least one private value')
    maximum = max(len(secret) for secret in bounded)
    if maximum > 1024 * 1024:
        fail('Apple physical-device redaction secret exceeds the reviewed bound')
    overlap = maximum - 1
    tail = b''
    while True:
        chunk = handle.read(64 * 1024)
        if not chunk:
            break
        data = tail + chunk
        masked = _redact(data, bounded)
        emit_length = max(0, len(data) - overlap) if overlap else len(data)
        if emit_length:
            sys.stdout.buffer.write(masked[:emit_length])
            sys.stdout.buffer.flush()
        tail = masked[emit_length:]
    if tail:
        sys.stdout.buffer.write(_redact(tail, bounded))
        sys.stdout.buffer.flush()


def _run_private(arguments: list[str], *, input_bytes: bytes | None = None, timeout: int = 60) -> bytes:
    try:
        completed = subprocess.run(
            arguments,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeviceError('Central Apple Development signing command is unavailable') from exc
    if completed.returncode != 0:
        fail('Central Apple Development signing command failed')
    return completed.stdout


def _keychains(raw: bytes, label: str) -> list[str]:
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise DeviceError(f'{label} is invalid') from exc
    result: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            raise DeviceError(f'{label} is invalid') from exc
        if len(parts) != 1:
            fail(f'{label} is invalid')
        value = parts[0]
        if not value.startswith('/') or any(character in value for character in '\r\n\0'):
            fail(f'{label} is invalid')
        if value not in result:
            result.append(value)
    return result


def _signing_paths(runner_temp: Path) -> tuple[Path, Path, Path]:
    return (
        runner_temp / SIGNING_STATE_NAME,
        runner_temp / SIGNING_KEYCHAIN_NAME,
        runner_temp / SIGNING_API_KEY_NAME,
    )


def _write_signing_state(path: Path, document: dict[str, object], *, exclusive: bool = False) -> None:
    if path.parent != _runner_temp() or path.name != SIGNING_STATE_NAME:
        fail('Central Apple Development signing state path is invalid')
    payload = (json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    if len(payload) > MAX_STATE_BYTES:
        fail('Central Apple Development signing state is oversized')
    temporary = path.parent / f'.{path.name}.{os.getpid()}.tmp'
    if temporary.exists() or temporary.is_symlink():
        fail('Central Apple Development signing temporary state already exists')
    write_private(temporary, payload, max_bytes=MAX_STATE_BYTES)
    try:
        if exclusive and (path.exists() or path.is_symlink()):
            fail('Central Apple Development signing state already exists')
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _read_signing_state(path: Path) -> dict[str, object]:
    runner_temp = _runner_temp()
    if path != runner_temp / SIGNING_STATE_NAME or path.is_symlink() or not path.is_file():
        fail('Central Apple Development signing state is unavailable')
    metadata = path.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > MAX_STATE_BYTES:
        fail('Central Apple Development signing state is invalid')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeviceError('Central Apple Development signing state is invalid') from exc
    expected = {'schema', 'teamId', 'originalDefaultKeychain', 'originalSearchKeychains', 'managedKeychain', 'certificateId'}
    if not isinstance(value, dict) or set(value) != expected or value.get('schema') != SIGNING_STATE_SCHEMA:
        fail('Central Apple Development signing state is invalid')
    team_id = value.get('teamId')
    default = value.get('originalDefaultKeychain')
    search = value.get('originalSearchKeychains')
    managed = value.get('managedKeychain')
    certificate_id = value.get('certificateId')
    if not isinstance(team_id, str) or TEAM_RE.fullmatch(team_id) is None:
        fail('Central Apple Development signing state is invalid')
    if not isinstance(default, str) or not default.startswith('/'):
        fail('Central Apple Development signing state is invalid')
    if not isinstance(search, list) or not search or len(search) != len(set(search)) or any(
        not isinstance(item, str) or not item.startswith('/') or any(c in item for c in '\r\n\0') for item in search
    ):
        fail('Central Apple Development signing state is invalid')
    if managed != str(runner_temp / SIGNING_KEYCHAIN_NAME):
        fail('Central Apple Development signing state is invalid')
    if certificate_id is not None and (not isinstance(certificate_id, str) or CERT_ID_RE.fullmatch(certificate_id) is None):
        fail('Central Apple Development signing state is invalid')
    return value


def _apple_credentials() -> tuple[str, str, str, bytes]:
    team_id = os.environ.get('CI_APPLE_TEAM_ID', '')
    key_id = os.environ.get('CI_APP_STORE_CONNECT_KEY_ID', '')
    issuer_id = os.environ.get('CI_APP_STORE_CONNECT_ISSUER_ID', '')
    encoded = os.environ.get('CI_APP_STORE_CONNECT_API_KEY_P8_BASE64', '')
    if TEAM_RE.fullmatch(team_id) is None or KEY_ID_RE.fullmatch(key_id) is None or ISSUER_RE.fullmatch(issuer_id) is None:
        fail('Central Apple Development signing credentials are unavailable')
    if not encoded or len(encoded) > 131072 or any(character in encoded for character in '\r\n'):
        fail('Central Apple Development signing credentials are unavailable')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DeviceError('Central Apple Development signing credentials are unavailable') from exc
    if not raw or len(raw) > 65536 or b'PRIVATE KEY' not in raw:
        fail('Central Apple Development signing credentials are unavailable')
    return team_id, key_id, issuer_id, raw


def _materialize_api_key(runner_temp: Path, raw: bytes) -> Path:
    path = runner_temp / SIGNING_API_KEY_NAME
    if path.exists() or path.is_symlink():
        fail('Central Apple Development authentication key residue is present')
    write_private(path, raw, max_bytes=65536)
    return path


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _der_ecdsa_to_raw(signature: bytes) -> bytes:
    if len(signature) < 8 or signature[0] != 0x30:
        fail('Central Apple Development authentication signature is invalid')
    index = 1
    length = signature[index]
    index += 1
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 2 or index + count > len(signature):
            fail('Central Apple Development authentication signature is invalid')
        length = int.from_bytes(signature[index:index + count], 'big')
        index += count
    if index + length != len(signature):
        fail('Central Apple Development authentication signature is invalid')
    values: list[int] = []
    for _ in range(2):
        if index + 2 > len(signature) or signature[index] != 0x02:
            fail('Central Apple Development authentication signature is invalid')
        index += 1
        size = signature[index]
        index += 1
        if size & 0x80:
            count = size & 0x7F
            if count == 0 or count > 2 or index + count > len(signature):
                fail('Central Apple Development authentication signature is invalid')
            size = int.from_bytes(signature[index:index + count], 'big')
            index += count
        if size == 0 or index + size > len(signature):
            fail('Central Apple Development authentication signature is invalid')
        value = int.from_bytes(signature[index:index + size], 'big', signed=False)
        index += size
        if value.bit_length() > 256:
            fail('Central Apple Development authentication signature is invalid')
        values.append(value)
    if index != len(signature):
        fail('Central Apple Development authentication signature is invalid')
    return b''.join(value.to_bytes(32, 'big') for value in values)


def _make_token(key_path: Path, key_id: str, issuer_id: str) -> str:
    issued = int(time.time())
    header = {'alg': 'ES256', 'kid': key_id, 'typ': 'JWT'}
    payload = {'iss': issuer_id, 'iat': issued - 5, 'exp': issued + 600, 'aud': 'appstoreconnect-v1'}
    signing_input = (
        _b64url(json.dumps(header, sort_keys=True, separators=(',', ':')).encode()) + '.' +
        _b64url(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())
    ).encode('ascii')
    signature = _run_private(['openssl', 'dgst', '-sha256', '-sign', str(key_path)], input_bytes=signing_input, timeout=30)
    return signing_input.decode('ascii') + '.' + _b64url(_der_ecdsa_to_raw(signature))


def _request_json(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    encoded_payload = None
    if payload is not None:
        encoded_payload = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        if len(encoded_payload) > 131072:
            fail('Central Apple Development certificate request is oversized')
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    if encoded_payload is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=encoded_payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(MAX_API_RESPONSE_BYTES + 1)
            if len(body) > MAX_API_RESPONSE_BYTES:
                raise DeviceError('Central Apple Development certificate response is oversized', ambiguous_outcome=(method == 'POST'))
    except urllib.error.HTTPError as exc:
        raise DeviceError(
            f'Central Apple Development certificate request failed with HTTP {exc.code}',
            ambiguous_outcome=(method == 'POST' and exc.code >= 500),
            http_status=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DeviceError('Central Apple Development certificate request failed', ambiguous_outcome=(method == 'POST')) from exc
    try:
        document = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeviceError('Central Apple Development certificate response is invalid', ambiguous_outcome=(method == 'POST')) from exc
    if not isinstance(document, dict):
        raise DeviceError('Central Apple Development certificate response is invalid', ambiguous_outcome=(method == 'POST'))
    return document


def _request_empty(method: str, url: str, token: str) -> None:
    request = urllib.request.Request(url, method=method, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 204:
                fail('Central Apple Development certificate revocation returned an unexpected status')
            response.read()
    except urllib.error.HTTPError as exc:
        raise DeviceError(f'Central Apple Development certificate revocation failed with HTTP {exc.code}') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DeviceError('Central Apple Development certificate revocation failed') from exc


def _certificate_der(row: dict[str, Any]) -> bytes:
    attributes = row.get('attributes')
    raw = attributes.get('certificateContent') if isinstance(attributes, dict) else None
    if not isinstance(raw, str) or not raw or len(raw) > MAX_CERTIFICATE_BYTES * 2:
        fail('Central Apple Development certificate response is invalid')
    try:
        value = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DeviceError('Central Apple Development certificate response is invalid') from exc
    if not value or len(value) > MAX_CERTIFICATE_BYTES:
        fail('Central Apple Development certificate response is invalid')
    return value


def _csr_public_key_der(csr_path: Path) -> bytes:
    pem = _run_private(['openssl', 'req', '-in', str(csr_path), '-pubkey', '-noout'])
    return _run_private(['openssl', 'pkey', '-pubin', '-outform', 'DER'], input_bytes=pem)


def _certificate_public_key_der(certificate_der: bytes) -> bytes:
    pem = _run_private(['openssl', 'x509', '-inform', 'DER', '-pubkey', '-noout'], input_bytes=certificate_der)
    return _run_private(['openssl', 'pkey', '-pubin', '-outform', 'DER'], input_bytes=pem)


def _update_certificate_id(state_path: Path, certificate_id: str) -> None:
    state = _read_signing_state(state_path)
    if state['certificateId'] is not None or CERT_ID_RE.fullmatch(certificate_id) is None:
        fail('Central Apple Development certificate cleanup identity is invalid')
    state['certificateId'] = certificate_id
    _write_signing_state(state_path, state)


def _recover_ambiguous_certificate(token: str, public_key_der: bytes, state_path: Path) -> bool:
    query = urllib.parse.urlencode({
        'filter[certificateType]': 'DEVELOPMENT,IOS_DEVELOPMENT',
        'fields[certificates]': 'certificateType,certificateContent',
        'limit': '200',
    })
    for attempt in range(AMBIGUOUS_RECONCILIATION_ATTEMPTS):
        try:
            inventory = _request_json('GET', f'{API_BASE}/v1/certificates?{query}', token)
        except DeviceError as exc:
            if (exc.http_status is not None and exc.http_status < 500) or attempt + 1 >= AMBIGUOUS_RECONCILIATION_ATTEMPTS:
                raise DeviceError('Central Apple Development certificate reconciliation failed') from exc
            time.sleep(AMBIGUOUS_RECONCILIATION_DELAY_SECONDS)
            continue
        rows = inventory.get('data')
        if not isinstance(rows, list) or len(rows) > 200:
            fail('Central Apple Development certificate reconciliation response is invalid')
        matches: list[str] = []
        for row in rows:
            if not isinstance(row, dict) or row.get('type') != 'certificates':
                fail('Central Apple Development certificate reconciliation response is invalid')
            certificate_id = row.get('id')
            attributes = row.get('attributes')
            if not isinstance(certificate_id, str) or CERT_ID_RE.fullmatch(certificate_id) is None or not isinstance(attributes, dict):
                fail('Central Apple Development certificate reconciliation response is invalid')
            if attributes.get('certificateType') not in {'DEVELOPMENT', 'IOS_DEVELOPMENT'}:
                continue
            if _certificate_public_key_der(_certificate_der(row)) == public_key_der:
                matches.append(certificate_id)
        if len(matches) == 1:
            _update_certificate_id(state_path, matches[0])
            return True
        if len(matches) > 1:
            fail('Central Apple Development certificate creation outcome is ambiguous')
        if attempt + 1 < AMBIGUOUS_RECONCILIATION_ATTEMPTS:
            time.sleep(AMBIGUOUS_RECONCILIATION_DELAY_SECONDS)
    return False


def _create_request_owned_identity(runner_temp: Path, state_path: Path, keychain: Path, keychain_password: str, key_path: Path, key_id: str, issuer_id: str) -> str:
    token = _make_token(key_path, key_id, issuer_id)
    with tempfile.TemporaryDirectory(prefix='central-apple-devsign-', dir=runner_temp) as temporary:
        private_dir = Path(temporary)
        private_dir.chmod(0o700)
        private_key = private_dir / 'request-private-key.pem'
        csr_path = private_dir / 'request.csr'
        certificate_path = private_dir / 'request-certificate.der'
        certificate_pem_path = private_dir / 'request-certificate.pem'
        bundle_path = private_dir / 'request-identity.p12'
        _run_private(['openssl', 'genrsa', '-out', str(private_key), '2048'])
        private_key.chmod(0o600)
        _run_private(['openssl', 'req', '-new', '-key', str(private_key), '-out', str(csr_path), '-subj', '/CN=StreamScapeTV Central Physical Signing'])
        csr_path.chmod(0o600)
        try:
            csr_content = csr_path.read_text(encoding='ascii')
        except (OSError, UnicodeError) as exc:
            raise DeviceError('Central Apple Development certificate signing request is invalid') from exc
        if len(csr_content) > 65536 or not csr_content.startswith('-----BEGIN CERTIFICATE REQUEST-----') or '-----END CERTIFICATE REQUEST-----' not in csr_content:
            fail('Central Apple Development certificate signing request is invalid')
        public_key_der = _csr_public_key_der(csr_path)
        if not public_key_der:
            fail('Central Apple Development certificate signing request is invalid')
        payload = {'data': {'type': 'certificates', 'attributes': {'certificateType': 'DEVELOPMENT', 'csrContent': csr_content}}}
        try:
            created = _request_json('POST', f'{API_BASE}/v1/certificates', token, payload)
        except DeviceError as exc:
            if not exc.ambiguous_outcome:
                raise
            recovered = _recover_ambiguous_certificate(token, public_key_der, state_path)
            if recovered:
                fail('Central Apple Development certificate creation outcome was ambiguous; exact cleanup target was retained')
            raise DeviceError('Central Apple Development certificate creation outcome was ambiguous; no exact cleanup target was found') from exc
        row = created.get('data')
        if not isinstance(row, dict) or row.get('type') != 'certificates' or not isinstance(row.get('id'), str) or CERT_ID_RE.fullmatch(row['id']) is None:
            recovered = _recover_ambiguous_certificate(token, public_key_der, state_path)
            if recovered:
                fail('Central Apple Development certificate response was invalid; exact cleanup target was retained')
            fail('Central Apple Development certificate response is invalid')
        certificate_id = row['id']
        _update_certificate_id(state_path, certificate_id)
        attributes = row.get('attributes')
        if not isinstance(attributes, dict) or attributes.get('certificateType') != 'DEVELOPMENT':
            fail('Central Apple Development certificate response type is invalid')
        try:
            certificate_der = _certificate_der(row)
        except DeviceError:
            detail = urllib.parse.urlencode({'fields[certificates]': 'certificateType,certificateContent'})
            fetched = _request_json('GET', f'{API_BASE}/v1/certificates/{certificate_id}?{detail}', token)
            fetched_row = fetched.get('data')
            if not isinstance(fetched_row, dict) or fetched_row.get('type') != 'certificates' or fetched_row.get('id') != certificate_id:
                fail('Central Apple Development certificate response is invalid')
            certificate_der = _certificate_der(fetched_row)
        if _certificate_public_key_der(certificate_der) != public_key_der:
            fail('Central Apple Development certificate does not match its request-owned private key')
        certificate_path.write_bytes(certificate_der)
        certificate_path.chmod(0o600)
        _run_private(['openssl', 'x509', '-inform', 'DER', '-in', str(certificate_path), '-out', str(certificate_pem_path)])
        certificate_pem_path.chmod(0o600)
        bundle_password = secrets.token_urlsafe(32)
        _run_private(['openssl', 'pkcs12', '-export', '-inkey', str(private_key), '-in', str(certificate_pem_path), '-out', str(bundle_path), '-passout', f'pass:{bundle_password}'])
        bundle_path.chmod(0o600)
        _run_private(['security', 'import', str(bundle_path), '-k', str(keychain), '-P', bundle_password, '-T', '/usr/bin/codesign', '-T', '/usr/bin/security'])
        _run_private(['security', 'set-key-partition-list', '-S', 'apple-tool:,apple:', '-s', '-k', keychain_password, str(keychain)])
        fingerprint = hashlib.sha1(certificate_der).hexdigest().upper()
        inventory_raw = _run_private(['security', 'find-identity', '-v', '-p', 'codesigning', str(keychain)])
        try:
            inventory = inventory_raw.decode('utf-8', errors='strict')
        except UnicodeError as exc:
            raise DeviceError('Central Apple Development signing identity validation failed') from exc
        rows = [
            (match.group(1).upper(), match.group(2))
            for line in inventory.splitlines()
            if (match := re.fullmatch(r'\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"]+)"\s*', line))
        ]
        team_id = str(_read_signing_state(state_path)['teamId'])
        exact = [(sha1, name) for sha1, name in rows if sha1 == fingerprint]
        if len(exact) != 1 or not exact[0][1].startswith(('Apple Development:', 'iOS Development:')) or not exact[0][1].endswith(f'({team_id})'):
            fail('Central Apple Development signing identity validation failed')
        probe_dir = private_dir / 'codesign-probe'
        probe_dir.mkdir(mode=0o700)
        source = probe_dir / 'main.c'
        binary = probe_dir / 'probe'
        source.write_text('int main(void) { return 0; }\n', encoding='ascii')
        _run_private(['xcrun', 'clang', str(source), '-o', str(binary)])
        _run_private(['codesign', '--force', '--sign', fingerprint, '--timestamp=none', str(binary)])
        _run_private(['codesign', '--verify', '--strict', str(binary)])
        return fingerprint


def setup_signing(runner_temp: Path, secret_path: Path) -> tuple[bytes, ...]:
    state_path, keychain, api_key_path = _signing_paths(runner_temp)
    for path in (state_path, keychain, api_key_path):
        if path.exists() or path.is_symlink():
            fail('Central Apple Development signing residue is present before setup')
    team_id, key_id, issuer_id, api_key = _apple_credentials()
    original_search = _keychains(_run_private(['security', 'list-keychains', '-d', 'user']), 'Central Apple keychain search list')
    original_default_rows = _keychains(_run_private(['security', 'default-keychain', '-d', 'user']), 'Central Apple default keychain')
    if not original_search or len(original_default_rows) != 1:
        fail('Central Apple signing keychain context is unavailable')
    state: dict[str, object] = {
        'schema': SIGNING_STATE_SCHEMA,
        'teamId': team_id,
        'originalDefaultKeychain': original_default_rows[0],
        'originalSearchKeychains': original_search,
        'managedKeychain': str(keychain),
        'certificateId': None,
    }
    _write_signing_state(state_path, state, exclusive=True)
    _materialize_api_key(runner_temp, api_key)
    keychain_password = secrets.token_urlsafe(36)
    _run_private(['security', 'create-keychain', '-p', keychain_password, str(keychain)])
    _run_private(['security', 'unlock-keychain', '-p', keychain_password, str(keychain)])
    _run_private(['security', 'set-keychain-settings', '-lut', '21600', str(keychain)])
    _run_private(['security', 'list-keychains', '-d', 'user', '-s', str(keychain)])
    _run_private(['security', 'default-keychain', '-d', 'user', '-s', str(keychain)])
    if _keychains(_run_private(['security', 'list-keychains', '-d', 'user']), 'Central Apple managed keychain search list') != [str(keychain)]:
        fail('Central Apple Development signing keychain isolation failed')
    if _keychains(_run_private(['security', 'default-keychain', '-d', 'user']), 'Central Apple managed default keychain') != [str(keychain)]:
        fail('Central Apple Development signing keychain isolation failed')
    fingerprint = _create_request_owned_identity(runner_temp, state_path, keychain, keychain_password, api_key_path, key_id, issuer_id)
    append_dynamic_secrets(secret_path, (str(keychain), str(api_key_path), fingerprint))
    print('Central Apple Development signing probe succeeded')
    return tuple(
        value.encode('utf-8')
        for value in (
            str(keychain), str(api_key_path), fingerprint,
            os.environ.get('CI_APPLE_TEAM_ID', ''),
            os.environ.get('CI_APP_STORE_CONNECT_KEY_ID', ''),
            os.environ.get('CI_APP_STORE_CONNECT_ISSUER_ID', ''),
            os.environ.get('CI_APP_STORE_CONNECT_API_KEY_P8_BASE64', ''),
        )
        if value
    )


def cleanup_signing(*, tolerate_absent: bool = True) -> None:
    runner_temp = _runner_temp()
    state_path, keychain, api_key_path = _signing_paths(runner_temp)
    if not state_path.exists() and not state_path.is_symlink():
        if keychain.exists() or keychain.is_symlink():
            fail('Central Apple Development signing keychain exists without cleanup state')
        if api_key_path.is_symlink():
            fail('Central Apple Development authentication key residue is invalid')
        if api_key_path.is_file():
            api_key_path.unlink()
        elif api_key_path.exists():
            fail('Central Apple Development authentication key residue is invalid')
        if tolerate_absent:
            return
        fail('Central Apple Development signing state is unavailable')
    state = _read_signing_state(state_path)
    errors: list[str] = []
    certificate_id = state['certificateId']
    if isinstance(certificate_id, str):
        try:
            _team_id, key_id, issuer_id, api_key = _apple_credentials()
            if not api_key_path.exists():
                _materialize_api_key(runner_temp, api_key)
            token = _make_token(api_key_path, key_id, issuer_id)
            _request_empty('DELETE', f'{API_BASE}/v1/certificates/{certificate_id}', token)
            state['certificateId'] = None
            _write_signing_state(state_path, state)
        except DeviceError:
            errors.append('certificate')
    try:
        _run_private(['security', 'list-keychains', '-d', 'user', '-s', *[str(item) for item in state['originalSearchKeychains']]])
        _run_private(['security', 'default-keychain', '-d', 'user', '-s', str(state['originalDefaultKeychain'])])
    except DeviceError:
        errors.append('keychain-context')
    if keychain.is_symlink():
        errors.append('keychain')
    elif keychain.exists():
        try:
            _run_private(['security', 'delete-keychain', str(keychain)])
        except DeviceError:
            errors.append('keychain')
        if keychain.exists() or keychain.is_symlink():
            errors.append('keychain')
    if api_key_path.is_symlink():
        errors.append('api-key')
    elif api_key_path.is_file():
        api_key_path.unlink()
        if api_key_path.exists() or api_key_path.is_symlink():
            errors.append('api-key')
    elif api_key_path.exists():
        errors.append('api-key')
    if errors:
        fail('Central Apple Development signing cleanup failed')
    state_path.unlink()
    for path in (state_path, keychain, api_key_path):
        if path.exists() or path.is_symlink():
            fail('Central Apple Development signing cleanup left private residue')


def run_entrypoint(entrypoint: Path, source_root: Path, context_path: Path, identifier: str, signing_redactions: tuple[bytes, ...] = ()) -> int:
    environment = dict(os.environ)
    environment['CI_DEVICE_CONTEXT_FILE'] = str(context_path)
    environment['CI_APPLE_PHYSICAL_DEVICE'] = 'true'
    process = subprocess.Popen(
        [str(entrypoint)],
        cwd=source_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    try:
        stream_redacted(
            process.stdout,
            (identifier.encode('utf-8'), str(context_path).encode('utf-8'), *signing_redactions),
        )
        returncode = process.wait()
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    if returncode >= 0:
        return min(returncode, 255)
    return min(255, 128 + (-returncode))


def acquire_fence() -> int:
    if LOCK_PATH.is_symlink():
        fail('Apple physical-device fence path is invalid')
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, 'O_CLOEXEC'):
        flags |= os.O_CLOEXEC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(LOCK_PATH, flags, 0o600)
    os.fchmod(fd, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        fail('Apple physical-device fence is not a regular file')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise DeviceError('Apple physical-device fence is already held by another execution') from exc
    return fd


def _signal_interrupt(signum: int, _frame: object) -> NoReturn:
    raise DeviceError(f'Apple physical-device execution interrupted by signal {signum}')


def execute() -> int:
    context_path, secret_path, _expires_at = validate_environment()
    source_root = Path.cwd()
    entrypoint = validate_entrypoint(source_root)
    fence_fd = acquire_fence()
    original_handlers = {signal.SIGTERM: signal.getsignal(signal.SIGTERM), signal.SIGINT: signal.getsignal(signal.SIGINT)}
    for signum in original_handlers:
        signal.signal(signum, _signal_interrupt)
    try:
        identifier = discover_attached_iphone(context_path.parent)
        prepare_private_state(context_path, secret_path, identifier)
        signing_redactions: tuple[bytes, ...] = ()
        try:
            signing_redactions = setup_signing(context_path.parent, secret_path)
            return run_entrypoint(entrypoint, source_root, context_path, identifier, signing_redactions)
        finally:
            cleanup_error: DeviceError | None = None
            try:
                cleanup_signing()
            except DeviceError as exc:
                cleanup_error = exc
            try:
                if context_path.is_symlink():
                    fail('Apple physical-device context became a symlink during execution')
                if context_path.exists():
                    context_path.unlink()
            finally:
                if context_path.exists() or context_path.is_symlink():
                    fail('Apple physical-device context cleanup did not complete')
            if cleanup_error is not None:
                raise cleanup_error
    finally:
        for signum, handler in original_handlers.items():
            signal.signal(signum, handler)
        try:
            fcntl.flock(fence_fd, fcntl.LOCK_UN)
        finally:
            os.close(fence_fd)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ['execute']:
            return execute()
        if args == ['cleanup']:
            cleanup_signing(tolerate_absent=True)
            return 0
        print('usage: repository_apple_physical_device.py execute|cleanup', file=sys.stderr)
        return 2
    except DeviceError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
