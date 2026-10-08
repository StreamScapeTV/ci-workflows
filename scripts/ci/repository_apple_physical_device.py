#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import fcntl
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
from datetime import datetime, timezone
from typing import BinaryIO, NoReturn

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
    if (not password or any(character in password for character in '\r\n\0')
            or len(password.encode('utf-8')) > MAX_P12_PASSWORD_BYTES):
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


def _selected_signing_identity(keychain: Path, team_id: str) -> str:
    """Allow precisely one currently valid private-key-backed Apple Development identity."""
    raw = _run_private(['security', 'find-identity', '-v', '-p', 'codesigning', str(keychain)])
    try:
        inventory = raw.decode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise DeviceError('Central Apple Development signing identity is invalid') from exc
    identities = [
        (match.group(1).upper(), match.group(2))
        for line in inventory.splitlines()
        if (match := re.fullmatch(r'\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"\r\n]{1,256})"\s*', line))
    ]
    if (len(identities) != 1
            or not identities[0][1].startswith(('Apple Development:', 'iOS Development:'))
            or not identities[0][1].endswith(f'({team_id})')):
        fail('Central Apple Development signing identity validation failed')
    return identities[0][0]


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
        _run_private(['codesign', '--force', '--sign', fingerprint, '--timestamp=none', str(binary)])
        _run_private(['codesign', '--verify', '--strict', str(binary)])
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
    if not state_path.exists() and not state_path.is_symlink():
        if keychain.exists() or keychain.is_symlink():
            fail('Central Apple Development signing keychain exists without cleanup state')
        if bundle_path.is_symlink():
            fail('Central Apple Development PKCS12 residue is invalid')
        if bundle_path.is_file():
            bundle_path.unlink()
        elif bundle_path.exists():
            fail('Central Apple Development PKCS12 residue is invalid')
        if tolerate_absent:
            return
        fail('Central Apple Development signing state is unavailable')
    state = _read_signing_state(state_path)
    errors: list[str] = []
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
    if bundle_path.is_symlink():
        errors.append('pkcs12')
    elif bundle_path.is_file():
        bundle_path.unlink()
        if bundle_path.exists() or bundle_path.is_symlink():
            errors.append('pkcs12')
    elif bundle_path.exists():
        errors.append('pkcs12')
    if errors:
        fail('Central Apple Development signing cleanup failed')
    state_path.unlink()
    for path in (state_path, keychain, bundle_path):
        if path.exists() or path.is_symlink():
            fail('Central Apple Development signing cleanup left private residue')



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
