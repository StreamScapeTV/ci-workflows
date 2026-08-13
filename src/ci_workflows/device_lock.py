"""Production-capable cross-run fencing for physical-device validation.

The backend is intentionally runner-infrastructure owned.  Callers never select a
backend or filesystem path: a reviewed runner host provides ``CIW_DEVICE_LOCK_ROOT``
and every run capable of reaching the same physical device must share that root.
Only hashed device/owner identities and opaque receipts are persisted.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

CONTRACT_PATH = Path("contracts/device-lock.json")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
REPOSITORY = re.compile(r"^StreamScapeTV/[A-Za-z0-9._-]{1,100}$")
REQUEST_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{7,127}$")
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")
RUN_ATTEMPT = re.compile(r"^[1-9][0-9]{0,3}$")
RECEIPT_PREFIX = "dlr1."
MAX_RECEIPT_BYTES = 8192
MAX_STATE_BYTES = 16384
RECEIPT_FIELDS = (
    "receipt_version",
    "backend",
    "resource_key_hash",
    "device_family",
    "device_capability",
    "device_identity_hash",
    "tested_source_sha",
    "authorization_receipt_hash",
    "owner_identity_hash",
    "request_id",
    "fencing_token",
    "acquired_at_epoch",
    "expires_at_epoch",
)
RELEASE_FIELDS = (
    "receipt_id",
    "resource_key_hash",
    "owner_identity_hash",
    "request_id",
    "release_evidence",
    "cleanup_evidence",
)


class DeviceLockError(ValueError):
    """Fail closed with one stable, non-sensitive device-lock code."""

    def __init__(self, code: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", code) is None:
            raise ValueError("device-lock error code must be safe")
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise DeviceLockError(code)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _opaque_text(value: object, *, minimum: int = 8, maximum: int = 4096) -> str:
    _require(isinstance(value, str), "invalid_input")
    result = str(value)
    _require(minimum <= len(result) <= maximum, "invalid_input")
    _require(not any(character in result for character in ("\x00", "\r", "\n")), "invalid_input")
    return result


@dataclass(frozen=True, slots=True)
class DeviceLockContract:
    version: str
    receipt_version: str
    backend_id: str
    root_environment: str
    required_root_mode: int
    transaction_lock: str
    lease_directory: str
    release_directory: str
    minimum_lease_seconds: int
    maximum_lease_seconds: int
    families: tuple[str, ...]
    receipt_fields: tuple[str, ...]
    release_fields: tuple[str, ...]


def load_device_lock_contract(root: Path) -> DeviceLockContract:
    try:
        payload = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeviceLockError("contract_unavailable") from error
    _require(isinstance(payload, Mapping), "contract_invalid")
    _require(payload.get("schema_version") == 1, "contract_invalid")
    _require(payload.get("contract_version") == "1.0.0", "contract_invalid")
    _require(payload.get("receipt_version") == "device-lock/1", "contract_invalid")

    backend = payload.get("backend")
    _require(isinstance(backend, Mapping), "contract_invalid")
    _require(
        set(backend)
        == {
            "id",
            "root_environment",
            "caller_selectable",
            "required_root_mode",
            "transaction_lock",
            "lease_directory",
            "release_directory",
        },
        "contract_invalid",
    )
    _require(backend.get("id") == "posix-shared-root-v1", "contract_invalid")
    _require(backend.get("root_environment") == "CIW_DEVICE_LOCK_ROOT", "contract_invalid")
    _require(backend.get("caller_selectable") is False, "contract_invalid")
    _require(backend.get("required_root_mode") == "0700", "contract_invalid")
    for name in ("transaction_lock", "lease_directory", "release_directory"):
        value = backend.get(name)
        _require(
            isinstance(value, str)
            and bool(value)
            and "/" not in value
            and "\\" not in value
            and value not in {".", ".."},
            "contract_invalid",
        )

    lease = payload.get("lease")
    _require(isinstance(lease, Mapping) and set(lease) == {"minimum_seconds", "maximum_seconds"}, "contract_invalid")
    minimum = lease.get("minimum_seconds")
    maximum = lease.get("maximum_seconds")
    _require(isinstance(minimum, int) and isinstance(maximum, int), "contract_invalid")
    _require(1 <= minimum <= maximum <= 24 * 60 * 60, "contract_invalid")

    families = payload.get("families")
    _require(
        isinstance(families, list)
        and tuple(families) == ("android", "ios", "tvos"),
        "contract_invalid",
    )
    _require(tuple(payload.get("receipt_fields", ())) == RECEIPT_FIELDS, "contract_invalid")
    _require(tuple(payload.get("release_fields", ())) == RELEASE_FIELDS, "contract_invalid")

    identity = payload.get("identity")
    _require(isinstance(identity, Mapping), "contract_invalid")
    _require(
        identity
        == {
            "device": "sha256",
            "owner": "sha256",
            "authorization_receipt": "sha256",
            "raw_device_identity_forbidden": True,
            "infrastructure_identity_forbidden": True,
        },
        "contract_invalid",
    )
    forbidden = payload.get("forbidden_public_fields")
    _require(isinstance(forbidden, list), "contract_invalid")
    _require(
        {
            "serial",
            "udid",
            "raw_device_identity",
            "runner_name",
            "runner_host",
            "backend_root",
            "endpoint",
            "credential",
            "secret",
        }
        <= set(forbidden),
        "contract_invalid",
    )

    return DeviceLockContract(
        version=str(payload["contract_version"]),
        receipt_version=str(payload["receipt_version"]),
        backend_id=str(backend["id"]),
        root_environment=str(backend["root_environment"]),
        required_root_mode=0o700,
        transaction_lock=str(backend["transaction_lock"]),
        lease_directory=str(backend["lease_directory"]),
        release_directory=str(backend["release_directory"]),
        minimum_lease_seconds=minimum,
        maximum_lease_seconds=maximum,
        families=tuple(str(item) for item in families),
        receipt_fields=RECEIPT_FIELDS,
        release_fields=RELEASE_FIELDS,
    )


@dataclass(frozen=True, slots=True)
class DeviceLockOwner:
    repository: str
    run_id: str
    run_attempt: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "DeviceLockOwner":
        repository = str(environment.get("GITHUB_REPOSITORY", ""))
        run_id = str(environment.get("GITHUB_RUN_ID", ""))
        run_attempt = str(environment.get("GITHUB_RUN_ATTEMPT", ""))
        _require(REPOSITORY.fullmatch(repository) is not None, "owner_identity_rejected")
        _require(RUN_ID.fullmatch(run_id) is not None, "owner_identity_rejected")
        _require(RUN_ATTEMPT.fullmatch(run_attempt) is not None, "owner_identity_rejected")
        return cls(repository, run_id, run_attempt)

    @property
    def identity_hash(self) -> str:
        return _sha256_text(f"{self.repository}\0{self.run_id}\0{self.run_attempt}")


@dataclass(frozen=True, slots=True)
class DeviceLockRequest:
    device_family: str
    device_capability: str
    device_identity_hash: str
    tested_source_sha: str
    authorization_receipt_hash: str
    owner_identity_hash: str
    request_id: str
    lease_seconds: int

    @classmethod
    def create(
        cls,
        contract: DeviceLockContract,
        *,
        device_family: str,
        device_capability: str,
        device_identity_hash: str,
        tested_source_sha: str,
        authorization_receipt: str,
        owner: DeviceLockOwner,
        request_id: str,
        lease_seconds: int,
    ) -> "DeviceLockRequest":
        _require(device_family in contract.families, "device_family_mismatch")
        _require(CAPABILITY.fullmatch(device_capability) is not None, "device_capability_rejected")
        _require(HEX64.fullmatch(device_identity_hash) is not None, "device_identity_rejected")
        _require(SHA40.fullmatch(tested_source_sha) is not None, "source_mismatch")
        authorization = _opaque_text(authorization_receipt)
        _require(REQUEST_ID.fullmatch(request_id) is not None, "request_identity_rejected")
        _require(isinstance(lease_seconds, int), "lease_rejected")
        _require(
            contract.minimum_lease_seconds <= lease_seconds <= contract.maximum_lease_seconds,
            "lease_rejected",
        )
        return cls(
            device_family=device_family,
            device_capability=device_capability,
            device_identity_hash=device_identity_hash,
            tested_source_sha=tested_source_sha,
            authorization_receipt_hash=_sha256_text(authorization),
            owner_identity_hash=owner.identity_hash,
            request_id=request_id,
            lease_seconds=lease_seconds,
        )

    @property
    def resource_key_hash(self) -> str:
        return _sha256_text(
            f"{self.device_family}\0{self.device_capability}\0{self.device_identity_hash}"
        )


@dataclass(frozen=True, slots=True)
class DeviceLockReceipt:
    receipt_version: str
    backend: str
    resource_key_hash: str
    device_family: str
    device_capability: str
    device_identity_hash: str
    tested_source_sha: str
    authorization_receipt_hash: str
    owner_identity_hash: str
    request_id: str
    fencing_token: str
    acquired_at_epoch: int
    expires_at_epoch: int

    def payload(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in RECEIPT_FIELDS}

    def encode(self) -> str:
        raw = _canonical(self.payload()).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return RECEIPT_PREFIX + encoded

    @property
    def receipt_id(self) -> str:
        return _sha256_text(self.encode())

    @classmethod
    def from_payload(
        cls,
        contract: DeviceLockContract,
        payload: Mapping[str, object],
    ) -> "DeviceLockReceipt":
        _require(set(payload) == set(contract.receipt_fields), "receipt_invalid")
        _require(payload.get("receipt_version") == contract.receipt_version, "receipt_invalid")
        _require(payload.get("backend") == contract.backend_id, "receipt_invalid")
        for name in (
            "resource_key_hash",
            "device_identity_hash",
            "authorization_receipt_hash",
            "owner_identity_hash",
            "fencing_token",
        ):
            _require(isinstance(payload.get(name), str) and HEX64.fullmatch(str(payload[name])) is not None, "receipt_invalid")
        _require(payload.get("device_family") in contract.families, "receipt_invalid")
        _require(
            isinstance(payload.get("device_capability"), str)
            and CAPABILITY.fullmatch(str(payload["device_capability"])) is not None,
            "receipt_invalid",
        )
        _require(
            isinstance(payload.get("tested_source_sha"), str)
            and SHA40.fullmatch(str(payload["tested_source_sha"])) is not None,
            "receipt_invalid",
        )
        _require(
            isinstance(payload.get("request_id"), str)
            and REQUEST_ID.fullmatch(str(payload["request_id"])) is not None,
            "receipt_invalid",
        )
        acquired = payload.get("acquired_at_epoch")
        expires = payload.get("expires_at_epoch")
        _require(isinstance(acquired, int) and isinstance(expires, int), "receipt_invalid")
        _require(0 <= acquired < expires, "receipt_invalid")
        return cls(
            receipt_version=str(payload["receipt_version"]),
            backend=str(payload["backend"]),
            resource_key_hash=str(payload["resource_key_hash"]),
            device_family=str(payload["device_family"]),
            device_capability=str(payload["device_capability"]),
            device_identity_hash=str(payload["device_identity_hash"]),
            tested_source_sha=str(payload["tested_source_sha"]),
            authorization_receipt_hash=str(payload["authorization_receipt_hash"]),
            owner_identity_hash=str(payload["owner_identity_hash"]),
            request_id=str(payload["request_id"]),
            fencing_token=str(payload["fencing_token"]),
            acquired_at_epoch=acquired,
            expires_at_epoch=expires,
        )

    @classmethod
    def decode(cls, contract: DeviceLockContract, value: str) -> "DeviceLockReceipt":
        text = _opaque_text(value, minimum=len(RECEIPT_PREFIX) + 8, maximum=MAX_RECEIPT_BYTES)
        _require(text.startswith(RECEIPT_PREFIX), "receipt_invalid")
        encoded = text[len(RECEIPT_PREFIX) :]
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        try:
            raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DeviceLockError("receipt_invalid") from error
        _require(isinstance(payload, Mapping), "receipt_invalid")
        return cls.from_payload(contract, payload)


@dataclass(frozen=True, slots=True)
class DeviceLockReleaseEvidence:
    receipt_id: str
    resource_key_hash: str
    owner_identity_hash: str
    request_id: str
    release_evidence: str
    cleanup_evidence: str
    idempotent: bool = False

    def marker_payload(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in RELEASE_FIELDS}


def _release_evidence(receipt: DeviceLockReceipt, *, idempotent: bool = False) -> DeviceLockReleaseEvidence:
    receipt_id = receipt.receipt_id
    release = _sha256_text(f"device-lock-release-v1\0{receipt_id}")
    cleanup = _sha256_text(f"device-lock-cleanup-v1\0{receipt_id}\0{release}")
    return DeviceLockReleaseEvidence(
        receipt_id=receipt_id,
        resource_key_hash=receipt.resource_key_hash,
        owner_identity_hash=receipt.owner_identity_hash,
        request_id=receipt.request_id,
        release_evidence=release,
        cleanup_evidence=cleanup,
        idempotent=idempotent,
    )


def _validate_release_marker(
    contract: DeviceLockContract,
    payload: Mapping[str, object],
) -> DeviceLockReleaseEvidence:
    _require(set(payload) == set(contract.release_fields), "backend_corrupt")
    for name in ("receipt_id", "resource_key_hash", "owner_identity_hash", "release_evidence", "cleanup_evidence"):
        _require(isinstance(payload.get(name), str) and HEX64.fullmatch(str(payload[name])) is not None, "backend_corrupt")
    _require(
        isinstance(payload.get("request_id"), str)
        and REQUEST_ID.fullmatch(str(payload["request_id"])) is not None,
        "backend_corrupt",
    )
    return DeviceLockReleaseEvidence(
        receipt_id=str(payload["receipt_id"]),
        resource_key_hash=str(payload["resource_key_hash"]),
        owner_identity_hash=str(payload["owner_identity_hash"]),
        request_id=str(payload["request_id"]),
        release_evidence=str(payload["release_evidence"]),
        cleanup_evidence=str(payload["cleanup_evidence"]),
    )


def _mode(path_stat: os.stat_result) -> int:
    return stat.S_IMODE(path_stat.st_mode)


def _safe_control_directory(path: Path, *, create: bool) -> None:
    try:
        if create:
            path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise DeviceLockError("backend_unavailable") from error
    try:
        info = path.lstat()
    except OSError as error:
        raise DeviceLockError("backend_unavailable") from error
    _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), "backend_unavailable")
    _require(info.st_uid == os.geteuid(), "backend_unavailable")
    _require(_mode(info) == 0o700, "backend_unavailable")


def resolve_backend_root(
    contract: DeviceLockContract,
    environment: Mapping[str, str] | None = None,
) -> Path:
    values = os.environ if environment is None else environment
    raw = str(values.get(contract.root_environment, ""))
    _require(bool(raw) and len(raw) <= 4096, "backend_unavailable")
    _require(not any(character in raw for character in ("\x00", "\r", "\n")), "backend_unavailable")
    root = Path(raw)
    _require(root.is_absolute(), "backend_unavailable")
    _safe_control_directory(root, create=False)
    _safe_control_directory(root / contract.lease_directory, create=True)
    _safe_control_directory(root / contract.release_directory, create=True)
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise DeviceLockError("backend_unavailable") from error
    _require(resolved == root, "backend_unavailable")
    return root


def _open_flags(base: int) -> int:
    return base | int(getattr(os, "O_NOFOLLOW", 0))


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, _open_flags(os.O_RDONLY))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise DeviceLockError("backend_unavailable") from error


def _read_json_file(path: Path) -> Mapping[str, object] | None:
    try:
        descriptor = os.open(path, _open_flags(os.O_RDONLY))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise DeviceLockError("backend_corrupt") from error
    try:
        info = os.fstat(descriptor)
        _require(stat.S_ISREG(info.st_mode), "backend_corrupt")
        _require(info.st_uid == os.geteuid() and _mode(info) == 0o600, "backend_corrupt")
        _require(0 < info.st_size <= MAX_STATE_BYTES, "backend_corrupt")
        data = bytearray()
        while len(data) <= MAX_STATE_BYTES:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            data.extend(chunk)
        _require(len(data) <= MAX_STATE_BYTES, "backend_corrupt")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeviceLockError("backend_corrupt") from error
    _require(isinstance(payload, Mapping), "backend_corrupt")
    return payload


def _write_json_file(path: Path, payload: Mapping[str, object]) -> None:
    encoded = (_canonical(dict(payload)) + "\n").encode("utf-8")
    _require(len(encoded) <= MAX_STATE_BYTES, "backend_corrupt")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            _open_flags(os.O_CREAT | os.O_EXCL | os.O_WRONLY),
            0o600,
        )
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as error:
        raise DeviceLockError("backend_unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise DeviceLockError("backend_residue") from error


def _unlink_regular(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise DeviceLockError("backend_unavailable") from error
    _require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode), "backend_corrupt")
    _require(info.st_uid == os.geteuid() and _mode(info) == 0o600, "backend_corrupt")
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        raise DeviceLockError("backend_unavailable") from error


@contextlib.contextmanager
def _transaction(root: Path, contract: DeviceLockContract) -> Iterator[None]:
    path = root / contract.transaction_lock
    try:
        descriptor = os.open(
            path,
            _open_flags(os.O_CREAT | os.O_RDWR),
            0o600,
        )
        info = os.fstat(descriptor)
        _require(stat.S_ISREG(info.st_mode), "backend_corrupt")
        _require(info.st_uid == os.geteuid() and _mode(info) == 0o600, "backend_corrupt")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as error:
        raise DeviceLockError("backend_unavailable") from error
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _matches_request(receipt: DeviceLockReceipt, request: DeviceLockRequest) -> bool:
    return (
        receipt.resource_key_hash == request.resource_key_hash
        and receipt.device_family == request.device_family
        and receipt.device_capability == request.device_capability
        and receipt.device_identity_hash == request.device_identity_hash
        and receipt.tested_source_sha == request.tested_source_sha
        and receipt.authorization_receipt_hash == request.authorization_receipt_hash
        and receipt.owner_identity_hash == request.owner_identity_hash
        and receipt.request_id == request.request_id
    )


class PosixDeviceLockBackend:
    """Cross-process lease backend over a pre-provisioned shared POSIX root."""

    def __init__(
        self,
        *,
        contract_root: Path,
        environment: Mapping[str, str] | None = None,
        now: Callable[[], int] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.contract = load_device_lock_contract(contract_root)
        self.root = resolve_backend_root(self.contract, environment)
        self._now = now or (lambda: int(time.time()))
        self._token_factory = token_factory or (lambda: secrets.token_hex(32))

    def _lease_path(self, resource_key_hash: str) -> Path:
        _require(HEX64.fullmatch(resource_key_hash) is not None, "invalid_input")
        return self.root / self.contract.lease_directory / f"{resource_key_hash}.json"

    def _release_path(self, resource_key_hash: str) -> Path:
        _require(HEX64.fullmatch(resource_key_hash) is not None, "invalid_input")
        return self.root / self.contract.release_directory / f"{resource_key_hash}.json"

    def _current(self, resource_key_hash: str) -> DeviceLockReceipt | None:
        payload = _read_json_file(self._lease_path(resource_key_hash))
        if payload is None:
            return None
        return DeviceLockReceipt.from_payload(self.contract, payload)

    def _last_release(self, resource_key_hash: str) -> DeviceLockReleaseEvidence | None:
        payload = _read_json_file(self._release_path(resource_key_hash))
        if payload is None:
            return None
        return _validate_release_marker(self.contract, payload)

    def acquire(self, request: DeviceLockRequest) -> DeviceLockReceipt:
        now = int(self._now())
        _require(now >= 0, "backend_unavailable")
        with _transaction(self.root, self.contract):
            current = self._current(request.resource_key_hash)
            if current is not None and current.expires_at_epoch > now:
                if _matches_request(current, request):
                    return current
                raise DeviceLockError("lock_held")

            token = str(self._token_factory())
            _require(HEX64.fullmatch(token) is not None, "backend_unavailable")
            receipt = DeviceLockReceipt(
                receipt_version=self.contract.receipt_version,
                backend=self.contract.backend_id,
                resource_key_hash=request.resource_key_hash,
                device_family=request.device_family,
                device_capability=request.device_capability,
                device_identity_hash=request.device_identity_hash,
                tested_source_sha=request.tested_source_sha,
                authorization_receipt_hash=request.authorization_receipt_hash,
                owner_identity_hash=request.owner_identity_hash,
                request_id=request.request_id,
                fencing_token=token,
                acquired_at_epoch=now,
                expires_at_epoch=now + request.lease_seconds,
            )
            _write_json_file(self._lease_path(request.resource_key_hash), receipt.payload())
            return receipt

    def verify(
        self,
        encoded_receipt: str,
        request: DeviceLockRequest,
        *,
        minimum_remaining_seconds: int = 1,
    ) -> DeviceLockReceipt:
        _require(isinstance(minimum_remaining_seconds, int) and minimum_remaining_seconds >= 0, "lease_rejected")
        receipt = DeviceLockReceipt.decode(self.contract, encoded_receipt)
        _require(_matches_request(receipt, request), "receipt_mismatch")
        now = int(self._now())
        with _transaction(self.root, self.contract):
            current = self._current(request.resource_key_hash)
            if current is None:
                released = self._last_release(request.resource_key_hash)
                if released is not None and released.receipt_id == receipt.receipt_id:
                    raise DeviceLockError("lock_released")
                raise DeviceLockError("lock_not_current")
            if current.receipt_id != receipt.receipt_id or current.fencing_token != receipt.fencing_token:
                raise DeviceLockError("lock_stale")
            if now >= current.expires_at_epoch:
                raise DeviceLockError("lock_expired")
            if current.expires_at_epoch - now < minimum_remaining_seconds:
                raise DeviceLockError("lock_expiring")
            return current

    def release(
        self,
        encoded_receipt: str,
        request: DeviceLockRequest,
    ) -> DeviceLockReleaseEvidence:
        receipt = DeviceLockReceipt.decode(self.contract, encoded_receipt)
        _require(_matches_request(receipt, request), "receipt_mismatch")
        expected = _release_evidence(receipt)
        with _transaction(self.root, self.contract):
            current = self._current(request.resource_key_hash)
            if current is None:
                previous = self._last_release(request.resource_key_hash)
                if previous is None or previous.receipt_id != receipt.receipt_id:
                    raise DeviceLockError("release_state_mismatch")
                _require(previous.marker_payload() == expected.marker_payload(), "backend_corrupt")
                return DeviceLockReleaseEvidence(**previous.marker_payload(), idempotent=True)
            if current.receipt_id != receipt.receipt_id or current.fencing_token != receipt.fencing_token:
                raise DeviceLockError("release_state_mismatch")
            _unlink_regular(self._lease_path(request.resource_key_hash))
            _write_json_file(self._release_path(request.resource_key_hash), expected.marker_payload())
            return expected

    def assert_released(
        self,
        encoded_receipt: str,
        request: DeviceLockRequest,
    ) -> DeviceLockReleaseEvidence:
        receipt = DeviceLockReceipt.decode(self.contract, encoded_receipt)
        _require(_matches_request(receipt, request), "receipt_mismatch")
        expected = _release_evidence(receipt)
        with _transaction(self.root, self.contract):
            current = self._current(request.resource_key_hash)
            _require(current is None, "lock_residue")
            previous = self._last_release(request.resource_key_hash)
            _require(previous is not None, "release_state_mismatch")
            _require(previous.marker_payload() == expected.marker_payload(), "release_state_mismatch")
            return previous


@contextlib.contextmanager
def locked_device_session(
    backend: PosixDeviceLockBackend,
    request: DeviceLockRequest,
) -> Iterator[DeviceLockReceipt]:
    """Acquire one lease and expected-state release it on every Python exit path."""

    receipt = backend.acquire(request)
    try:
        yield receipt
    finally:
        backend.release(receipt.encode(), request)


def request_from_environment(
    *,
    contract_root: Path,
    environment: Mapping[str, str],
) -> tuple[DeviceLockRequest, DeviceLockOwner]:
    contract = load_device_lock_contract(contract_root)
    owner = DeviceLockOwner.from_environment(environment)
    try:
        lease_seconds = int(str(environment.get("CIW_LOCK_LEASE_SECONDS", "")))
    except ValueError as error:
        raise DeviceLockError("lease_rejected") from error
    request = DeviceLockRequest.create(
        contract,
        device_family=str(environment.get("CIW_LOCK_DEVICE_FAMILY", "")),
        device_capability=str(environment.get("CIW_LOCK_DEVICE_CAPABILITY", "")),
        device_identity_hash=str(environment.get("CIW_LOCK_DEVICE_IDENTITY_HASH", "")),
        tested_source_sha=str(environment.get("CIW_LOCK_TESTED_SOURCE_SHA", "")),
        authorization_receipt=str(environment.get("CIW_LOCK_AUTHORIZATION_RECEIPT", "")),
        owner=owner,
        request_id=str(environment.get("CIW_LOCK_REQUEST_ID", "")),
        lease_seconds=lease_seconds,
    )
    return request, owner
