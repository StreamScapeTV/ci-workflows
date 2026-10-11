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
import ssl
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import BinaryIO, Callable, NoReturn

LOCK_PATH = Path('/tmp/streamscapetv-central-apple-physical-device-v1.lock')
ENTRYPOINT = Path('.ci/device-test.sh')
CONTEXT_NAME = 'central-apple-physical-device-context.json'
SECRET_NAME = 'central-apple-physical-device-secrets.txt'
INVENTORY_NAME = 'central-apple-physical-device-inventory.json'
SIGNING_STATE_NAME = 'central-apple-development-signing-state.json'
SIGNING_KEYCHAIN_NAME = 'central-apple-development-signing.keychain-db'
SIGNING_BUNDLE_NAME = 'central-apple-development-identity.p12'
SIGNING_STATE_SCHEMA = 'streamscape-central-apple-development-signing-v2'
IDENTIFIER_RE = re.compile(r'[A-Za-z0-9-]{16,128}')
IPHONE_MODEL_RE = re.compile(r'iPhone[0-9]+,[0-9]+')
TEAM_RE = re.compile(r'[A-Z0-9]{10}')
MAX_STATE_BYTES = 16384
MAX_P12_BYTES = 128 * 1024
MAX_P12_PASSWORD_BYTES = 1024
MAX_IDENTITY_INVENTORY_BYTES = 32 * 1024
MAX_CERTIFICATE_INVENTORY_BYTES = 256 * 1024
MAX_CERTIFICATE_BYTES = 32 * 1024
CERTIFICATE_PEM_RE = re.compile(
    rb'-----BEGIN CERTIFICATE-----\r?\n([A-Za-z0-9+/=\r\n]+)-----END CERTIFICATE-----',
)


class DeviceError(RuntimeError):
    pass

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
        runner_temp / SIGNING_BUNDLE_NAME,
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
    expected = {'schema', 'teamId', 'originalDefaultKeychain', 'originalSearchKeychains', 'managedKeychain'}
    if not isinstance(value, dict) or set(value) != expected or value.get('schema') != SIGNING_STATE_SCHEMA:
        fail('Central Apple Development signing state is invalid')
    team_id = value.get('teamId')
    default = value.get('originalDefaultKeychain')
    search = value.get('originalSearchKeychains')
    managed = value.get('managedKeychain')
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
    return value


def _p12_credentials() -> tuple[str, bytes, str]:
    """Read owner-provisioned Central secrets, never returning them to product code."""
    team_id = os.environ.get('CI_APPLE_TEAM_ID', '')
    encoded = os.environ.get('CI_APPLE_DEVELOPMENT_P12_BASE64', '')
    password = os.environ.get('CI_APPLE_DEVELOPMENT_P12_PASSWORD', '')
    if TEAM_RE.fullmatch(team_id) is None:
        fail('Central Apple Development signing team is unavailable')
    try:
        password_bytes = password.encode('utf-8')
    except UnicodeError as exc:
        raise DeviceError('Central Apple Development PKCS12 credentials are unavailable') from exc
    if (not password or any(character in password for character in '\r\n\0')
            or len(password_bytes) > MAX_P12_PASSWORD_BYTES):
        fail('Central Apple Development PKCS12 credentials are unavailable')
    if (not encoded or not encoded.isascii() or len(encoded) > ((MAX_P12_BYTES + 2) // 3) * 4
            or any(character in encoded for character in '\r\n\0')):
        fail('Central Apple Development PKCS12 credentials are unavailable')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DeviceError('Central Apple Development PKCS12 credentials are invalid') from exc
    if len(raw) < 32 or len(raw) > MAX_P12_BYTES or raw[0] != 0x30:
        fail('Central Apple Development PKCS12 credentials are invalid')
    return team_id, raw, password


def _materialize_p12(runner_temp: Path, raw: bytes) -> Path:
    path = runner_temp / SIGNING_BUNDLE_NAME
    if path.exists() or path.is_symlink():
        fail('Central Apple Development PKCS12 residue is present')
    write_private(path, raw, max_bytes=MAX_P12_BYTES)
    return path


def _identity_records(raw: bytes, *, valid_only: bool = True) -> list[tuple[str, str]]:
    """Parse Security's one- or two-section identity inventory without disclosing it."""
    invalid = 'Central Apple Development signing identity inventory is invalid'
    if len(raw) > MAX_IDENTITY_INVENTORY_BYTES:
        fail(invalid)
    try:
        inventory = raw.decode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise DeviceError(invalid) from exc

    # Without -v, Security can print both "Matching identities" and
    # "Valid identities only", with distinct counts and overlapping rows.
    # Never combine them: an invalid key-backed identity is not a valid one.
    rows: dict[str, list[tuple[str, str]]] = {
        'plain': [], 'matching': [], 'valid': [],
    }
    counts: dict[str, int] = {}
    sections: set[str] = set()
    section = 'plain'
    for line in inventory.splitlines():
        stripped = line.strip()
        if stripped in ('Matching identities', 'Valid identities only'):
            new_section = 'matching' if stripped == 'Matching identities' else 'valid'
            if (new_section in sections or (new_section == 'matching' and 'valid' in sections)
                    or rows['plain'] or (section == 'plain' and counts)):
                fail(invalid)
            section = new_section
            sections.add(section)
            continue
        summary = re.fullmatch(
            r'\s*(\d{1,4})\s+(valid\s+)?identit(?:y|ies)\s+found\s*', line,
        )
        if summary is not None:
            category = 'valid' if summary.group(2) else 'matching'
            if category in counts or (sections and section != category):
                fail(invalid)
            counts[category] = int(summary.group(1))
            continue
        # A malformed/oversized summary must not be silently ignored as a
        # harmless Security policy heading.
        if re.match(r'\s*\d+\s+.*identit', line):
            fail(invalid)
        if re.match(r'\s*\d+\)', line) is None:
            continue
        match = re.fullmatch(
            r'\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"\r\n]{1,256})"'
            r'(?:\s+\([^()\r\n]{1,100}\))?\s*', line,
        )
        if match is None or section in counts or (section == 'plain' and counts):
            fail(invalid)
        rows[section].append((match.group(1).upper(), match.group(2)))

    if sections:
        for name in sections:
            if name in counts and counts[name] != len(rows[name]):
                fail(invalid)
        if ('matching' in sections and 'valid' in sections
                and not set(rows['valid']).issubset(set(rows['matching']))):
            fail(invalid)
        # An explicit valid-only section selects validated entries. The
        # fallback non--v call selects raw key-backed entries instead.
        chosen = 'valid' if valid_only and 'valid' in sections else 'matching'
        if chosen not in sections:
            chosen = 'valid'
        return rows[chosen]
    if len(counts) > 1 or (counts and next(iter(counts.values())) != len(rows['plain'])):
        fail(invalid)
    return rows['plain']


def _identity_failure(reason: str) -> NoReturn:
    """Emit only a fixed, non-sensitive category, never an identity inventory."""
    fail(f'Central Apple Development signing identity validation failed ({reason})')


def _imported_certificate_metadata(
    keychain: Path, fingerprint: str, *, command: Callable[[list[str]], bytes] | None = None,
) -> tuple[str, str]:
    """Read signed OU/serial from the public leaf bound to a key-backed identity.

    Security owns PKCS12 import and trust validation. Never decrypt the original
    bundle a second time or infer the team from its certificate's display name.
    """
    command = _run_private if command is None else command
    try:
        if (re.fullmatch(r'[0-9A-F]{40}', fingerprint) is None or keychain.is_symlink()
                or not keychain.parent.is_absolute() or keychain.parent.is_symlink()
                or not keychain.parent.is_dir()):
            raise ValueError
        raw = command(['security', 'find-certificate', '-a', '-p', str(keychain)])
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CERTIFICATE_INVENTORY_BYTES:
            raise ValueError
        blocks = list(CERTIFICATE_PEM_RE.finditer(raw))
        if not 1 <= len(blocks) <= 16:
            raise ValueError
        matching: list[bytes] = []
        end = 0
        for block in blocks:
            if raw[end:block.start()].strip():
                raise ValueError
            encoded = block.group(1).replace(b'\r', b'').replace(b'\n', b'')
            der = base64.b64decode(encoded, validate=True)
            if not 32 <= len(der) <= MAX_CERTIFICATE_BYTES:
                raise ValueError
            if hashlib.sha1(der).hexdigest().upper() == fingerprint:
                matching.append(block.group(0))
            end = block.end()
        if raw[end:].strip() or len(matching) != 1:
            raise ValueError
        with tempfile.TemporaryDirectory(prefix='central-apple-public-leaf-', dir=keychain.parent) as temporary:
            leaf = Path(temporary) / 'leaf.pem'
            write_private(leaf, matching[0], max_bytes=MAX_CERTIFICATE_INVENTORY_BYTES)
            # Decode public certificate fields only; this does not replace the
            # selected Security identity's separate trust/private-key checks.
            decoded = ssl._ssl._test_decode_cert(str(leaf))
        units = [value for rdn in decoded['subject'] for name, value in rdn
                 if name == 'organizationalUnitName']
        serial = decoded['serialNumber']
        if (len(units) != 1 or not isinstance(units[0], str) or TEAM_RE.fullmatch(units[0]) is None
                or not isinstance(serial, str) or re.fullmatch(r'[0-9A-Fa-f]{1,64}', serial) is None):
            raise ValueError
        return units[0], _canonical_certificate_serial(serial)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        raise DeviceError('Central Apple Development certificate metadata could not be read') from None


def _canonical_certificate_serial(serial: str) -> str:
    """Normalize an X.509 serial to the unsigned hexadecimal API representation."""
    if re.fullmatch(r'[0-9A-Fa-f]{1,64}', serial) is None:
        raise ValueError('certificate serial is invalid')
    return serial.upper().lstrip('0') or '0'


# Fixed categories only. These describe the imported leaf and what is visible
# inside *our own* keychain; neither proves that macOS trusts the certificate.
TRUST_DIAGNOSTICS = frozenset({
    'UNDETERMINED', 'LEAF_EXPIRED', 'LEAF_NOT_YET_VALID',
    'VALID_LEAF_G3_ISSUER_IN_KEYCHAIN', 'VALID_LEAF_G3_ISSUER_NOT_IN_KEYCHAIN',
    'VALID_LEAF_OTHER_ISSUER', 'WWDR_G3_PINNED_INTERMEDIATE_IMPORTED',
})
WWDR_G3_ISSUER = frozenset({
    ('commonName', 'Apple Worldwide Developer Relations Certification Authority'),
    ('organizationalUnitName', 'G3'),
    ('organizationName', 'Apple Inc.'),
    ('countryName', 'US'),
})
WWDR_G3_DER_BASE64 = (
    b'MIIEUTCCAzmgAwIBAgIQfK9pCiW3Of57m0R6wXjF7jANBgkqhkiG9w0BAQsFADBiMQswCQYDVQQGEwJV'
    b'UzETMBEGA1UEChMKQXBwbGUgSW5jLjEmMCQGA1UECxMdQXBwbGUgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkx'
    b'FjAUBgNVBAMTDUFwcGxlIFJvb3QgQ0EwHhcNMjAwMjE5MTgxMzQ3WhcNMzAwMjIwMDAwMDAwWjB1MUQwQgYD'
    b'VQQDDDtBcHBsZSBXb3JsZHdpZGUgRGV2ZWxvcGVyIFJlbGF0aW9ucyBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0'
    b'eTELMAkGA1UECwwCRzMxEzARBgNVBAoMCkFwcGxlIEluYy4xCzAJBgNVBAYTAlVTMIIBIjANBgkqhkiG9w0B'
    b'AQEFAAOCAQ8AMIIBCgKCAQEA2PWJ/KhZC4fHTJEuLVaQ03gdpDDppUjvC0O/LYT7JF1FG+XrWTYSXFRknmxi'
    b'LbTGl8rMPPbWBpH85QKmHGq0edVny6zpPwcR4YS8Rx1mjjmi6LRJ7TrS4RBgeo6TjMrA2gzAg9Dj+ZHWp4z'
    b'IwXPirkbRYp2SqJBgN31ols2N4Pyb+ni743uvLRfdW/6AWSN1F7gSwe0b5TTO/iK1nkmw5VW/j4SiPKi6xY'
    b'aVFuQAyZ8D0MyzOhZ71gVcnetHrg21LYwOaU1A0EtMOwSejSGxrC5DVDDOwYqGlJhL32oNP/77HK6XF8J4C'
    b'jDgXx9UO0m3JQAaN4LSVpelUkl8YDib7wIDAQABo4HvMIHsMBIGA1UdEwEB/wQIMAYBAf8CAQAwHwYDVR0j'
    b'BBgwFoAUK9BpR5R2Cf70a40uQKb3R01/CF4wRAYIKwYBBQUHAQEEODA2MDQGCCsGAQUFBzABhihodHRwOi8v'
    b'b2NzcC5hcHBsZS5jb20vb2NzcDAzLWFwcGxlcm9vdGNhMC4GA1UdHwQnMCUwI6AhoB+GHWh0dHA6Ly9jcmwu'
    b'YXBwbGUuY29tL3Jvb3QuY3JsMB0GA1UdDgQWBBQJ/sAVkPmvZAqSErkmKGMMl+ynsjAOBgNVHQ8BAf8EBAMC'
    b'AQYwEAYKKoZIhvdjZAYCAQQCBQAwDQYJKoZIhvcNAQELBQADggEBAK1lE+j24IF3RAJHQr5fpTkg6mKp/cWQ'
    b'yXMT1Z6b0KoPjY3L7QHPbChAW8dVJEH4/M/BtSPp3Ozxb8qAHXfCxGFJJWevD8o5Ja3T43rMMygNDi6hV0Bz'
    b'+uZcrgZRKe3jhQxPYdwyFot30ETKXXIDMUacrptAGvr04NM++i+MZp+XxFRZ79JI9AeZSWBZGcfdlNHAwWx/'
    b'eCHvDOs7bJmCS1JgOLU5gm3sUjFTvg+RTElJdI+mUcuER04ddSduvfnSXPN/wmwLCTbiZOTCNwMUGdXqapSq'
    b'qdv+9poIZ4vvK7iqF0mDr8/LvOnP6pVxsLRFoszlh6oKw0E6eVzaUDSdlTs='
)
WWDR_G3_DER_SHA256 = 'dcf21878c77f4198e4b4614f03d696d89c66c66008d4244e1b99161aac91601f'


def import_pinned_wwdr_g3(keychain: Path, *, command: Callable[[list[str]], bytes] | None = None) -> None:
    """Import Apple's pinned public G3 intermediate into only one managed keychain."""
    command = _run_private if command is None else command
    try:
        if (keychain.is_symlink() or not keychain.parent.is_absolute() or keychain.parent.is_symlink()
                or not keychain.parent.is_dir()):
            raise ValueError
        certificate = base64.b64decode(WWDR_G3_DER_BASE64, validate=True)
        if (not 32 <= len(certificate) <= MAX_CERTIFICATE_BYTES
                or hashlib.sha256(certificate).hexdigest() != WWDR_G3_DER_SHA256):
            raise ValueError
        with tempfile.TemporaryDirectory(prefix='central-apple-wwdr-g3-', dir=keychain.parent) as temporary:
            path = Path(temporary) / 'AppleWWDRCAG3.cer'
            write_private(path, certificate, max_bytes=MAX_CERTIFICATE_BYTES)
            command(['security', 'import', str(path), '-k', str(keychain)])
    except (OSError, ValueError, RuntimeError, DeviceError):
        raise DeviceError('Central Apple WWDR G3 intermediate import failed') from None


def _imported_certificate_trust_diagnostic(
    keychain: Path, fingerprint: str, *, command: Callable[[list[str]], bytes] | None = None,
) -> str:
    """Safely classify a public leaf after Security reports no valid identity.

    The diagnostic never changes trust settings or accepts an identity. An issuer
    absent from the isolated keychain might still exist in other trust domains;
    only Security's ordinary -v check can establish actual signing validity.
    """
    command = _run_private if command is None else command
    try:
        if (re.fullmatch(r'[0-9A-F]{40}', fingerprint) is None or keychain.is_symlink()
                or not keychain.parent.is_absolute() or keychain.parent.is_symlink()
                or not keychain.parent.is_dir()):
            raise ValueError
        raw = command(['security', 'find-certificate', '-a', '-p', str(keychain)])
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CERTIFICATE_INVENTORY_BYTES:
            raise ValueError
        blocks = list(CERTIFICATE_PEM_RE.finditer(raw))
        if not 1 <= len(blocks) <= 16:
            raise ValueError
        decoded = []
        leaf_index = None
        end = 0
        with tempfile.TemporaryDirectory(prefix='central-apple-public-diagnostic-', dir=keychain.parent) as temporary:
            for index, block in enumerate(blocks):
                if raw[end:block.start()].strip():
                    raise ValueError
                der = base64.b64decode(block.group(1).replace(b'\r', b'').replace(b'\n', b''), validate=True)
                if not 32 <= len(der) <= MAX_CERTIFICATE_BYTES:
                    raise ValueError
                if hashlib.sha1(der).hexdigest().upper() == fingerprint:
                    if leaf_index is not None:
                        raise ValueError
                    leaf_index = index
                certificate = Path(temporary) / f'public-{index}.pem'
                write_private(certificate, block.group(0), max_bytes=MAX_CERTIFICATE_BYTES)
                decoded.append(ssl._ssl._test_decode_cert(str(certificate)))
                end = block.end()
        if raw[end:].strip() or leaf_index is None:
            raise ValueError
        leaf = decoded[leaf_index]
        not_before = ssl.cert_time_to_seconds(leaf['notBefore'])
        not_after = ssl.cert_time_to_seconds(leaf['notAfter'])
        now = datetime.now(timezone.utc).timestamp()
        if not_before > now:
            return 'LEAF_NOT_YET_VALID'
        if not_after < now:
            return 'LEAF_EXPIRED'
        issuer = frozenset((name, value) for rdn in leaf['issuer'] for name, value in rdn)
        if issuer != WWDR_G3_ISSUER:
            return 'VALID_LEAF_OTHER_ISSUER'
        available = any(
            i != leaf_index and frozenset((name, value) for rdn in certificate['subject']
                                          for name, value in rdn) == issuer
            for i, certificate in enumerate(decoded)
        )
        return ('VALID_LEAF_G3_ISSUER_IN_KEYCHAIN' if available
                else 'VALID_LEAF_G3_ISSUER_NOT_IN_KEYCHAIN')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, UnicodeError):
        return 'UNDETERMINED'


def _selected_signing_identity(keychain: Path, team_id: str) -> str:
    """Allow precisely one currently valid private-key-backed Apple Development identity."""
    valid = _identity_records(_run_private([
        'security', 'find-identity', '-v', '-p', 'codesigning', str(keychain),
    ]))
    if not valid:
        # Non-`-v` enumerates *key-backed* identities even when none pass
        # Security's trust/expiry validation. This distinguishes an absent
        # private key from an imported but invalid/untrusted identity.
        all_key_backed = _identity_records(_run_private([
            'security', 'find-identity', '-p', 'codesigning', str(keychain),
        ]), valid_only=False)
        if not all_key_backed:
            _identity_failure('no-key-backed-identity')
        if len(all_key_backed) != 1:
            _identity_failure('ambiguous-key-backed-identities')
        fingerprint, common_name = all_key_backed[0]
        if _imported_certificate_trust_diagnostic(keychain, fingerprint) == 'VALID_LEAF_G3_ISSUER_NOT_IN_KEYCHAIN':
            if not common_name.startswith(('Apple Development:', 'iOS Development:')):
                _identity_failure('wrong-certificate-type')
            signed_team, _serial = _imported_certificate_metadata(keychain, fingerprint)
            if signed_team != team_id:
                _identity_failure('team-mismatch')
            import_pinned_wwdr_g3(keychain)
            valid = _identity_records(_run_private([
                'security', 'find-identity', '-v', '-p', 'codesigning', str(keychain),
            ]))
        if not valid:
            _identity_failure('no-valid-identity')
    if len(valid) != 1:
        _identity_failure('ambiguous-valid-identities')
    fingerprint, common_name = valid[0]
    if not common_name.startswith(('Apple Development:', 'iOS Development:')):
        _identity_failure('wrong-certificate-type')
    signed_team, _serial = _imported_certificate_metadata(keychain, fingerprint)
    if signed_team != team_id:
        _identity_failure('team-mismatch')
    return fingerprint


def _probe_signing_identity(runner_temp: Path, keychain: Path, team_id: str) -> str:
    fingerprint = _selected_signing_identity(keychain, team_id)
    with tempfile.TemporaryDirectory(prefix='central-apple-signing-probe-', dir=runner_temp) as temporary:
        probe = Path(temporary)
        probe.chmod(0o700)
        source = probe / 'main.c'
        binary = probe / 'probe'
        source.write_text('int main(void) { return 0; }\n', encoding='ascii')
        _run_private(['xcrun', 'clang', str(source), '-o', str(binary)])
        # Deliberately omit --keychain: prove codesign sees the same isolated default
        # search context inherited by the repository-owned Xcode build.
        try:
            _run_private(['codesign', '--force', '--sign', fingerprint, '--timestamp=none', str(binary)])
        except DeviceError as exc:
            raise DeviceError('Central Apple Development signing probe failed (codesign)') from exc
        try:
            _run_private(['codesign', '--verify', '--strict', str(binary)])
        except DeviceError as exc:
            raise DeviceError('Central Apple Development signing probe failed (verification)') from exc
    return fingerprint



def setup_signing(runner_temp: Path, secret_path: Path) -> tuple[bytes, ...]:
    state_path, keychain, bundle_path = _signing_paths(runner_temp)
    for path in (state_path, keychain, bundle_path):
        if path.exists() or path.is_symlink():
            fail('Central Apple Development signing residue is present before setup')
    encoded = os.environ.get('CI_APPLE_DEVELOPMENT_P12_BASE64', '')
    team_id, bundle, p12_password = _p12_credentials()
    # A trusted Central wrapper may read the secrets, but its subprocesses and
    # the repository-owned entrypoint must never inherit the P12/password env.
    os.environ.pop('CI_APPLE_DEVELOPMENT_P12_BASE64', None)
    os.environ.pop('CI_APPLE_DEVELOPMENT_P12_PASSWORD', None)
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
    }
    # Durable private cleanup state precedes every potentially failing import step.
    _write_signing_state(state_path, state, exclusive=True)
    _materialize_p12(runner_temp, bundle)
    keychain_password = secrets.token_urlsafe(36)
    _run_private(['security', 'create-keychain', '-p', keychain_password, str(keychain)])
    _run_private(['security', 'unlock-keychain', '-p', keychain_password, str(keychain)])
    _run_private(['security', 'set-keychain-settings', '-lut', '21600', str(keychain)])
    _run_private(['security', 'import', str(bundle_path), '-k', str(keychain), '-P', p12_password,
                  '-T', '/usr/bin/codesign', '-T', '/usr/bin/security'])
    _run_private(['security', 'set-key-partition-list', '-S', 'apple-tool:,apple:', '-s', '-k',
                  keychain_password, str(keychain)])
    _run_private(['security', 'list-keychains', '-d', 'user', '-s', str(keychain)])
    _run_private(['security', 'default-keychain', '-d', 'user', '-s', str(keychain)])
    if _keychains(_run_private(['security', 'list-keychains', '-d', 'user']), 'Central Apple managed keychain search list') != [str(keychain)]:
        fail('Central Apple Development signing keychain isolation failed')
    if _keychains(_run_private(['security', 'default-keychain', '-d', 'user']), 'Central Apple managed default keychain') != [str(keychain)]:
        fail('Central Apple Development signing keychain isolation failed')
    fingerprint = _probe_signing_identity(runner_temp, keychain, team_id)
    append_dynamic_secrets(secret_path, (str(keychain), str(bundle_path), fingerprint))
    print('Central Apple Development signing probe succeeded')
    return tuple(value.encode('utf-8') for value in (
        str(keychain), str(bundle_path), fingerprint,
        encoded, p12_password,
    ) if value)


def cleanup_signing(*, tolerate_absent: bool = True) -> None:
    runner_temp = _runner_temp()
    state_path, keychain, bundle_path = _signing_paths(runner_temp)
    errors: list[str] = []
    # Raw credential bytes are never needed to restore keychain context. Purge
    # them before reading recovery state, including when that state is damaged.
    try:
        if bundle_path.is_symlink():
            errors.append('pkcs12')
        elif bundle_path.is_file():
            bundle_path.unlink()
        elif bundle_path.exists():
            errors.append('pkcs12')
        if bundle_path.exists() or bundle_path.is_symlink():
            errors.append('pkcs12')
    except OSError:
        errors.append('pkcs12')
    try:
        if not state_path.exists() and not state_path.is_symlink():
            if errors:
                fail('Central Apple Development signing cleanup failed')
            if keychain.exists() or keychain.is_symlink():
                fail('Central Apple Development signing keychain exists without cleanup state')
            if tolerate_absent:
                return
            fail('Central Apple Development signing state is unavailable')
        state = _read_signing_state(state_path)
    except (OSError, ValueError, TypeError):
        raise DeviceError('Central Apple Development signing cleanup state is unavailable') from None
    for command in (
        ['security', 'list-keychains', '-d', 'user', '-s', *[str(item) for item in state['originalSearchKeychains']]],
        ['security', 'default-keychain', '-d', 'user', '-s', str(state['originalDefaultKeychain'])],
    ):
        try:
            _run_private(command)
        except DeviceError:
            errors.append('keychain-context')
    try:
        if keychain.is_symlink():
            errors.append('keychain')
        elif keychain.exists():
            _run_private(['security', 'delete-keychain', str(keychain)])
        if keychain.exists() or keychain.is_symlink():
            errors.append('keychain')
    except (DeviceError, OSError):
        errors.append('keychain')
    if errors:
        fail('Central Apple Development signing cleanup failed')
    try:
        state_path.unlink()
        for path in (state_path, keychain, bundle_path):
            if path.exists() or path.is_symlink():
                fail('Central Apple Development signing cleanup left private residue')
    except OSError:
        raise DeviceError('Central Apple Development signing cleanup failed') from None



def run_entrypoint(entrypoint: Path, source_root: Path, context_path: Path, identifier: str, signing_redactions: tuple[bytes, ...] = ()) -> int:
    environment = dict(os.environ)
    # Product scripts must never inherit the raw Central-owned signing bundle/password.
    environment.pop('CI_APPLE_DEVELOPMENT_P12_BASE64', None)
    environment.pop('CI_APPLE_DEVELOPMENT_P12_PASSWORD', None)
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
