#!/usr/bin/env python3
"""Central-only, manually admitted, no-device macOS Apple credential preflight.

No credential/certificate/provider values or subprocess output are ever logged.
This helper deliberately cannot sign or install a product app or modify Apple state.
"""
from __future__ import annotations

import base64
import fcntl
import binascii
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import repository_apple_physical_device as signing

ROOT_NAME = "central-apple-credential-preflight-v1"
STAGES = (
    "P12_IMPORT", "PRIVATE_KEY_PRESENT", "CERT_TEAM_MATCH", "CERT_NAME_POLICY", "CERT_TRUST_VALID",
    "CODESIGN", "CODESIGN_VERIFY", "API_AUTH", "API_CERT_ACCESS", "API_CERT_ACCOUNT_MATCH",
)
SAFE_RESULTS = frozenset((
    "PASS", "NOT_APPLICABLE", "MISSING", "INVALID_P12", "IMPORT_FAILED", "NO_PRIVATE_KEY",
    "AMBIGUOUS", "WRONG_TYPE", "WRONG_TEAM", "NAME_MISMATCH", "CERT_INVALID", "NO_VALID_IDENTITY",
    "SIGN_FAILED", "VERIFY_FAILED", "KEYCHAIN_SETUP_FAILED", "INVALID_KEY", "INVALID_JWT", "UNAUTHORIZED_401",
    "FORBIDDEN_403", "NETWORK_ERROR", "HTTP_ERROR", "BAD_RESPONSE", "NOT_VISIBLE",
    "UNEXPECTED_FAILURE", "CLEANUP_FAILED",
))
MAX_P8_BYTES = 16 * 1024
API_URL = "https://api.appstoreconnect.apple.com/v1/certificates"


class PreflightFailure(Exception):
    def __init__(self, stage: str, code: str):
        if stage not in STAGES or code not in SAFE_RESULTS:
            raise ValueError("preflight failure is not a fixed safe category")
        self.stage, self.code = stage, code
        super().__init__("Apple credential preflight failed")


def _call(arguments: list[str], *, input_bytes: bytes | None = None,
          env: dict[str, str] | None = None, timeout: int = 45) -> bytes:
    """Capture private stdout/stderr; never convert a provider error into text evidence."""
    try:
        result = subprocess.run(
            arguments, input=input_bytes, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("private command unavailable") from exc
    if result.returncode:
        raise RuntimeError("private command unsuccessful")
    return result.stdout


def _write_secret(path: Path, content: bytes, *, limit: int) -> None:
    if path.exists() or path.is_symlink() or not content or len(content) > limit:
        raise RuntimeError("private file unavailable")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(content)
    path.chmod(0o600)


def _credentials() -> tuple[str, bytes, str, bytes, str, str]:
    team = os.environ.pop("PREFLIGHT_APPLE_TEAM_ID", "")
    encoded = os.environ.pop("PREFLIGHT_P12_BASE64", "")
    password = os.environ.pop("PREFLIGHT_P12_PASSWORD", "")
    api_encoded = os.environ.pop("PREFLIGHT_ASC_P8_BASE64", "")
    key_id = os.environ.pop("PREFLIGHT_ASC_KEY_ID", "")
    issuer = os.environ.pop("PREFLIGHT_ASC_ISSUER_ID", "")
    if (len(encoded) > ((signing.MAX_P12_BYTES+2)//3)*4 or not encoded.isascii()
            or any(c in encoded for c in "\x00\r\n")):
        encoded = ""
    if (len(api_encoded) > ((MAX_P8_BYTES+2)//3)*4 or not api_encoded.isascii()
            or any(c in api_encoded for c in "\x00\r\n")):
        api_encoded = ""
    try:
        p12 = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        p12 = b""
    try:
        p8 = base64.b64decode(api_encoded, validate=True)
    except (ValueError, binascii.Error):
        p8 = b""
    return team, p12, password, p8, key_id, issuer


def _subject_ou(subject: bytes) -> str:
    """Extract exact unescaped 10-character X.509 subject OU, without logging CN."""
    if len(subject) > 8192:
        return ""
    try:
        line = subject.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        return ""
    if not line.startswith("subject="):
        return ""
    groups = re.findall(r"(?:^|(?<!\\),)OU=([A-Z0-9]{10})(?=,|$)", line[8:])
    return groups[0] if len(groups) == 1 else ""


def _leaf_fingerprint(raw: bytes) -> str:
    if len(raw)>256:
        return ""
    try:
        value=raw.decode("ascii").strip()
    except UnicodeError:
        return ""
    match=re.fullmatch(r"(?:sha1 |SHA1 )?Fingerprint=((?:[0-9A-Fa-f]{2}:){19}[0-9A-Fa-f]{2})",value)
    return match.group(1).replace(":", "").upper() if match else ""


def _leaf_serial(raw: bytes) -> str:
    if len(raw) > 1024:
        return ""
    match = re.fullmatch(rb"serial=([0-9A-Fa-f]{1,64})\s*", raw)
    return match.group(1).decode("ascii").upper() if match else ""


def _ecdsa_der_to_jose(raw: bytes) -> bytes:
    """Strict ECDSA ASN.1 DER SEQUENCE of two positive P-256 INTEGERs -> JOSE r||s."""
    if len(raw) < 8 or raw[0] != 0x30:
        raise ValueError("signature invalid")
    pos = 1
    def read_length() -> int:
        nonlocal pos
        if pos >= len(raw):
            raise ValueError("signature invalid")
        length = raw[pos]
        pos += 1
        if length & 0x80:
            n = length & 0x7f
            if n != 1 or pos + n > len(raw):
                raise ValueError("signature invalid")
            length = raw[pos]
            pos += 1
            if length < 128:
                raise ValueError("signature invalid")
        return length
    total = read_length()
    if pos + total != len(raw):
        raise ValueError("signature invalid")
    ints = []
    for _ in range(2):
        if pos >= len(raw) or raw[pos] != 0x02:
            raise ValueError("signature invalid")
        pos += 1
        size = read_length()
        if not 1 <= size <= 33 or pos + size > len(raw):
            raise ValueError("signature invalid")
        value = raw[pos:pos+size]
        pos += size
        if value[0] & 0x80 or (len(value) > 1 and value[0] == 0 and not value[1] & 0x80):
            raise ValueError("signature invalid")
        number = int.from_bytes(value, "big")
        if not 0 < number < 2**256:
            raise ValueError("signature invalid")
        ints.append(number.to_bytes(32, "big"))
    if pos != len(raw):
        raise ValueError("signature invalid")
    return b"".join(ints)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _jwt(key_file: Path, key_id: str, issuer: str) -> str:
    if re.fullmatch(r"[A-Z0-9]{10}", key_id) is None:
        raise ValueError("credential invalid")
    if str(uuid.UUID(issuer)) != issuer.lower():
        raise ValueError("credential invalid")
    details = _call(["openssl", "pkey", "-in", str(key_file), "-pubout"])
    _write_secret(key_file.parent / "pub.pem", details, limit=4096)
    public = _call(["openssl", "pkey", "-pubin", "-in", str(key_file.parent / "pub.pem"), "-text_pub", "-noout"])
    if b"prime256v1" not in public and b"P-256" not in public:
        raise ValueError("credential invalid")
    timestamp = int(time.time())
    header = {"alg":"ES256", "kid":key_id, "typ":"JWT"}
    payload = {"iss":issuer, "iat":timestamp, "exp":timestamp+600, "aud":"appstoreconnect-v1"}
    message = ".".join(_b64(json.dumps(v,separators=(",",":"),sort_keys=True).encode()) for v in (header,payload))
    der = _call(["openssl", "dgst", "-sha256", "-sign", str(key_file)], input_bytes=message.encode("ascii"))
    return message + "." + _b64(_ecdsa_der_to_jose(der))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _apple_certificates(jwt: str, serial: str | None) -> tuple[int, bytes]:
    query = urllib.parse.urlencode({"filter[serialNumber]":serial} if serial else {"limit":"1"})
    req = urllib.request.Request(API_URL + "?" + query,
        headers={"Authorization":"Bearer " + jwt, "Accept":"application/json"}, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=20) as response:
            status = response.status
            data = response.read(128 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        # Never read/log provider error body/headers or URL (which may contain serial).
        return exc.code, b""
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, b""
    return status, data


class Preflight:
    def __init__(self, temp: Path):
        if temp.is_symlink() or not temp.is_dir() or not temp.is_absolute():
            raise RuntimeError("invalid runner temp")
        self.root = temp / ROOT_NAME
        self.state = self.root / "keychain-state.json"
        self.keychain = self.root / "preflight.keychain-db"
        self.status = dict.fromkeys(STAGES, "NOT_APPLICABLE")
        self.serial: str | None = None

    def _stage(self, stage: str, result: str) -> None:
        if stage not in STAGES or result not in SAFE_RESULTS:
            raise RuntimeError("invalid safe stage")
        self.status[stage] = result

    def _secure_root(self) -> None:
        if self.root.exists() or self.root.is_symlink():
            raise RuntimeError("private preflight residue is present")
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)

    def _create_keychain(self, password: str) -> None:
        search = signing._keychains(_call(["security","list-keychains","-d","user"]), "keychains")
        defaults = signing._keychains(_call(["security","default-keychain","-d","user"]), "keychain")
        if not search or len(defaults) != 1:
            raise RuntimeError("keychain context invalid")
        # Persist safe private cleanup state BEFORE touching the user keychain.
        state = json.dumps({"search":search,"default":defaults[0]},separators=(",", ":")).encode()
        _write_secret(self.state, state, limit=16 * 1024)
        _call(["security","create-keychain","-p",password,str(self.keychain)])
        _call(["security","unlock-keychain","-p",password,str(self.keychain)])
        _call(["security","set-keychain-settings","-lut","21600",str(self.keychain)])

    def _p12(self, team: str, bundle: bytes, password: str) -> None:
        if signing.TEAM_RE.fullmatch(team) is None:
            raise PreflightFailure("CERT_TEAM_MATCH", "MISSING")
        try:
            password_length = len(password.encode("utf-8"))
        except UnicodeError:
            password_length = 1025
        if (not password or password_length > 1024
                or any(c in password for c in "\x00\r\n")):
            raise PreflightFailure("P12_IMPORT", "MISSING")
        if len(bundle) < 32 or len(bundle)>signing.MAX_P12_BYTES or bundle[0] != 0x30:
            raise PreflightFailure("P12_IMPORT", "INVALID_P12")
        _write_secret(self.root/"owner.p12",bundle,limit=signing.MAX_P12_BYTES)
        keychain_password = secrets.token_urlsafe(36)
        try:
            self._create_keychain(keychain_password)
        except (RuntimeError, signing.DeviceError):
            raise PreflightFailure("P12_IMPORT", "KEYCHAIN_SETUP_FAILED") from None
        try:
            _call(["security","import",str(self.root/"owner.p12"),"-k",str(self.keychain),"-P",password,
                   "-T","/usr/bin/codesign","-T","/usr/bin/security"])
        except RuntimeError:
            raise PreflightFailure("P12_IMPORT", "IMPORT_FAILED") from None
        try:
            _call(["security","set-key-partition-list","-S","apple-tool:,apple:","-s","-k",keychain_password,str(self.keychain)])
            _call(["security","list-keychains","-d","user","-s",str(self.keychain)])
            _call(["security","default-keychain","-d","user","-s",str(self.keychain)])
        except RuntimeError:
            raise PreflightFailure("P12_IMPORT", "KEYCHAIN_SETUP_FAILED") from None
        self._stage("P12_IMPORT","PASS")
        try:
            found = signing._identity_records(_call(["security","find-identity","-p","codesigning",str(self.keychain)]),valid_only=False)
        except (RuntimeError, signing.DeviceError):
            raise PreflightFailure("PRIVATE_KEY_PRESENT","CERT_INVALID") from None
        if len(found)!=1:
            raise PreflightFailure("PRIVATE_KEY_PRESENT","NO_PRIVATE_KEY" if not found else "AMBIGUOUS")
        self._stage("PRIVATE_KEY_PRESENT","PASS")
        _, common_name = found[0]
        if not common_name.startswith(("Apple Development:","iOS Development:")):
            raise PreflightFailure("CERT_TEAM_MATCH","WRONG_TYPE")
        env = dict(os.environ, PREFLIGHT_PKCS12_PASS=password)
        try:
            _call(["openssl","pkcs12","-in",str(self.root/"owner.p12"),"-clcerts","-nokeys",
                   "-passin","env:PREFLIGHT_PKCS12_PASS","-out",str(self.root/"leaf.pem")],env=env)
            (self.root/"leaf.pem").chmod(0o600)
            subject = _call(["openssl","x509","-in",str(self.root/"leaf.pem"),"-noout","-subject","-nameopt","RFC2253"])
            serial = _call(["openssl","x509","-in",str(self.root/"leaf.pem"),"-noout","-serial"])
            digest = _call(["openssl","x509","-in",str(self.root/"leaf.pem"),"-noout","-fingerprint","-sha1"])
        except RuntimeError:
            raise PreflightFailure("CERT_TEAM_MATCH","CERT_INVALID") from None
        if _leaf_fingerprint(digest) != found[0][0]:
            raise PreflightFailure("CERT_TEAM_MATCH","CERT_INVALID")
        if _subject_ou(subject) != team:
            raise PreflightFailure("CERT_TEAM_MATCH","WRONG_TEAM")
        self.serial = _leaf_serial(serial) or None
        self._stage("CERT_TEAM_MATCH","PASS")
        # Diagnose the separate existing product-side CN-suffix selector without
        # misclassifying a correct signed subject OU as a bad Apple Developer team.
        self._stage("CERT_NAME_POLICY","PASS" if common_name.endswith("(" + team + ")") else "NAME_MISMATCH")
        try:
            valid = signing._identity_records(_call(["security","find-identity","-v","-p","codesigning",str(self.keychain)]))
        except (RuntimeError, signing.DeviceError):
            raise PreflightFailure("CERT_TRUST_VALID","CERT_INVALID") from None
        if len(valid)!=1 or valid[0] != found[0]:
            raise PreflightFailure("CERT_TRUST_VALID","NO_VALID_IDENTITY" if not valid else "AMBIGUOUS")
        self._stage("CERT_TRUST_VALID","PASS")
        source, binary = self.root/"probe.c",self.root/"probe"
        _write_secret(source,b"int main(void) { return 0; }\n",limit=1000)
        try:
            _call(["xcrun","clang",str(source),"-o",str(binary)])
            _call(["codesign","--force","--sign",valid[0][0],"--timestamp=none",str(binary)])
        except RuntimeError:
            raise PreflightFailure("CODESIGN","SIGN_FAILED") from None
        self._stage("CODESIGN","PASS")
        try:
            _call(["codesign","--verify","--strict",str(binary)])
        except RuntimeError:
            raise PreflightFailure("CODESIGN_VERIFY","VERIFY_FAILED") from None
        self._stage("CODESIGN_VERIFY","PASS")

    def _api(self, pem: bytes, kid: str, issuer: str) -> None:
        if not (0<len(pem)<=MAX_P8_BYTES and pem.startswith(b"-----BEGIN PRIVATE KEY-----")
                and b"-----END PRIVATE KEY-----" in pem):
            raise PreflightFailure("API_AUTH","INVALID_KEY")
        _write_secret(self.root/"api.p8",pem,limit=MAX_P8_BYTES)
        try:
            token = _jwt(self.root/"api.p8",kid,issuer)
        except (ValueError, RuntimeError, OSError):
            raise PreflightFailure("API_AUTH","INVALID_KEY") from None
        status, content = _apple_certificates(token,self.serial)
        if status == 401:
            raise PreflightFailure("API_AUTH","UNAUTHORIZED_401")
        if status == 403:
            self._stage("API_AUTH","PASS")
            raise PreflightFailure("API_CERT_ACCESS","FORBIDDEN_403")
        if status == 0:
            raise PreflightFailure("API_AUTH","NETWORK_ERROR")
        if status != 200:
            raise PreflightFailure("API_AUTH","HTTP_ERROR")
        self._stage("API_AUTH","PASS")
        if len(content)>128*1024:
            raise PreflightFailure("API_CERT_ACCESS","BAD_RESPONSE")
        try:
            data=json.loads(content)
            rows=data["data"]
            if not isinstance(rows,list) or len(rows)>200:
                raise ValueError
        except (UnicodeError, ValueError, KeyError, TypeError):
            raise PreflightFailure("API_CERT_ACCESS","BAD_RESPONSE") from None
        self._stage("API_CERT_ACCESS","PASS")
        if self.serial is None:
            return
        visible=[]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get("attributes"),dict):
                raise PreflightFailure("API_CERT_ACCESS","BAD_RESPONSE")
            visible.append(row["attributes"].get("serialNumber",""))
        self._stage("API_CERT_ACCOUNT_MATCH","PASS" if any(
            isinstance(value,str) and value.upper()==self.serial for value in visible
        ) else "NOT_VISIBLE")

    def run(self) -> int:
        self._secure_root()
        try:
            team,bundle,password,pem,key_id,issuer=_credentials()
            try:
                self._p12(team,bundle,password)
            except PreflightFailure as exc:
                self._stage(exc.stage,exc.code)
            try:
                self._api(pem,key_id,issuer)
            except PreflightFailure as exc:
                self._stage(exc.stage,exc.code)
        except (RuntimeError, OSError, signing.DeviceError):
            self._stage("P12_IMPORT","UNEXPECTED_FAILURE")
        finally:
            try:
                self.cleanup()
            except (RuntimeError, OSError, ValueError, signing.DeviceError):
                self._stage("P12_IMPORT","CLEANUP_FAILED")
        for key,value in self.status.items():
            print(key+"="+value)
        must_pass=("P12_IMPORT","PRIVATE_KEY_PRESENT","CERT_TEAM_MATCH","CERT_NAME_POLICY","CERT_TRUST_VALID", "CODESIGN","CODESIGN_VERIFY","API_AUTH","API_CERT_ACCESS")
        return 0 if all(self.status[k]=="PASS" for k in must_pass) and self.status["API_CERT_ACCOUNT_MATCH"] in ("PASS","NOT_APPLICABLE") else 1

    def cleanup(self) -> None:
        if self.root.is_symlink():
            raise RuntimeError("private cleanup symlink")
        if not self.root.exists():
            return
        if not self.root.is_dir():
            raise RuntimeError("private cleanup target invalid")
        errors=[]
        if self.state.exists():
            if self.state.is_symlink() or not self.state.is_file():
                raise RuntimeError("private keychain state invalid")
            try:
                state=json.loads(self.state.read_text(encoding="utf-8"))
                search=state["search"]
                default=state["default"]
                if (set(state)!={"search","default"} or not isinstance(search,list) or not search
                    or any(not isinstance(v,str) or not v.startswith("/") for v in search)
                    or not isinstance(default,str) or not default.startswith("/")):
                    raise ValueError
                _call(["security","list-keychains","-d","user","-s",*search])
                _call(["security","default-keychain","-d","user","-s",default])
            except (OSError,ValueError,RuntimeError,KeyError):
                errors.append("context")
        if self.keychain.exists() or self.keychain.is_symlink():
            try:
                if self.keychain.is_symlink():
                    raise RuntimeError("managed keychain symlink")
                _call(["security","delete-keychain",str(self.keychain)])
            except RuntimeError:
                errors.append("keychain")
        if errors or self.keychain.exists() or self.keychain.is_symlink():
            raise RuntimeError("private keychain cleanup failed")
        shutil.rmtree(self.root)
        if self.root.exists() or self.root.is_symlink():
            raise RuntimeError("private cleanup residue")


def _admission() -> Path:
    if (os.environ.get("GITHUB_REPOSITORY")!="StreamScapeTV/ci-workflows"
            or os.environ.get("GITHUB_REF")!="refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME")!="workflow_dispatch"
            or os.environ.get("RUNNER_OS")!="macOS"
            or os.environ.get("RUNNER_ENVIRONMENT")!="self-hosted"):
        raise RuntimeError("preflight request is not approved")
    root=Path(os.environ.get("RUNNER_TEMP",""))
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise RuntimeError("preflight runner temp invalid")
    return root


def main(argv: list[str]|None=None) -> int:
    arguments=sys.argv[1:] if argv is None else argv
    if arguments not in (["run"],["cleanup"]):
        return 2
    try:
        os.umask(0o077)
        root=_admission()
        fence=signing.acquire_fence()
        try:
            preflight=Preflight(root)
            if arguments==["cleanup"]:
                preflight.cleanup()
                print("PREFLIGHT_CLEANUP=PASS")
                return 0
            return preflight.run()
        finally:
            fcntl.flock(fence,fcntl.LOCK_UN)
            os.close(fence)
    except (RuntimeError,OSError,ValueError,signing.DeviceError):
        print("PREFLIGHT_CLEANUP_OR_ADMISSION=FAIL")
        return 2


if __name__=="__main__":
    raise SystemExit(main())
