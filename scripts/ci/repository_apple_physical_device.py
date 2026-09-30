#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from typing import BinaryIO, NoReturn

LOCK_PATH = Path('/tmp/streamscapetv-central-apple-physical-device-v1.lock')
ENTRYPOINT = Path('.ci/device-test.sh')
CONTEXT_NAME = 'central-apple-physical-device-context.json'
SECRET_NAME = 'central-apple-physical-device-secrets.txt'
IDENTIFIER_RE = re.compile(r'[A-Za-z0-9-]{16,128}')
IPHONE_MODEL_RE = re.compile(r'iPhone[0-9]+,[0-9]+')
PLATFORM = 'com.apple.platform.iphoneos'
ALLOWED_INTERFACES = {'usb', 'wired'}


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

    runner_temp_raw = os.environ.get('RUNNER_TEMP', '')
    if not runner_temp_raw:
        fail('Apple physical-device execution requires one Central runner temp directory')
    runner_temp = Path(runner_temp_raw)
    if not runner_temp.is_absolute() or runner_temp.is_symlink() or not runner_temp.is_dir():
        fail('Apple physical-device runner temp directory is invalid')
    context_path = runner_temp / CONTEXT_NAME
    secret_path = runner_temp / SECRET_NAME
    return context_path, secret_path, expires_at


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
    if value.get('simulator') is not False or value.get('available') is not True:
        return None
    if value.get('platform') != PLATFORM:
        return None
    model_code = value.get('modelCode')
    if not isinstance(model_code, str) or IPHONE_MODEL_RE.fullmatch(model_code) is None:
        return None
    interface = value.get('interface')
    if not isinstance(interface, str) or interface.lower() not in ALLOWED_INTERFACES:
        return None
    identifier = value.get('identifier')
    if not isinstance(identifier, str) or IDENTIFIER_RE.fullmatch(identifier) is None:
        return None
    return identifier


def discover_attached_iphone() -> str:
    try:
        completed = subprocess.run(
            ['xcrun', 'xcdevice', 'list', '--timeout', '5'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeviceError('Apple physical-device discovery failed before product execution') from exc
    if completed.returncode != 0:
        fail('Apple physical-device discovery failed before product execution')
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DeviceError('Apple physical-device discovery returned invalid data') from exc
    if not isinstance(payload, list):
        fail('Apple physical-device discovery returned invalid data')
    devices = sorted({identifier for item in payload if (identifier := _eligible_device(item)) is not None})
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


def write_private(path: Path, data: bytes, *, exclusive: bool = True) -> None:
    if len(data) > 4096:
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


def _redact(data: bytes, secrets: tuple[bytes, ...]) -> bytes:
    for secret in secrets:
        if secret:
            # Preserve byte length so chunk-boundary accounting remains exact.
            data = data.replace(secret, b'*' * len(secret))
    return data


def stream_redacted(handle: BinaryIO, secrets: tuple[bytes, ...]) -> None:
    bounded = tuple(secret for secret in secrets if secret)
    if not bounded:
        fail('Apple physical-device redaction requires at least one private value')
    maximum = max(len(secret) for secret in bounded)
    if maximum > 4096:
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


def run_entrypoint(entrypoint: Path, source_root: Path, context_path: Path, identifier: str) -> int:
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
            (identifier.encode('utf-8'), str(context_path).encode('utf-8')),
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


def execute() -> int:
    context_path, secret_path, _expires_at = validate_environment()
    source_root = Path.cwd()
    entrypoint = validate_entrypoint(source_root)
    fence_fd = acquire_fence()
    try:
        identifier = discover_attached_iphone()
        prepare_private_state(context_path, secret_path, identifier)
        try:
            return run_entrypoint(entrypoint, source_root, context_path, identifier)
        finally:
            try:
                if context_path.is_symlink():
                    fail('Apple physical-device context became a symlink during execution')
                if context_path.exists():
                    context_path.unlink()
            finally:
                if context_path.exists() or context_path.is_symlink():
                    fail('Apple physical-device context cleanup did not complete')
    finally:
        try:
            fcntl.flock(fence_fd, fcntl.LOCK_UN)
        finally:
            os.close(fence_fd)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ['execute']:
        print('usage: repository_apple_physical_device.py execute', file=sys.stderr)
        return 2
    try:
        return execute()
    except DeviceError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
