#!/usr/bin/env python3
"""Redact configured credential values from one private text log, in place.

Values come from environment variables named ``CI_SECRET_*``. A value is also redacted in its JSON
escaped, base64 and URL-quoted forms. Short values (under 6 bytes) are ignored so that ordinary
words are not mangled. The log is rewritten atomically; nothing is printed except a count.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.parse

MIN_SECRET_BYTES = 6
MAX_LOG_BYTES = 16 * 1024 * 1024


def variants(value: str) -> set:
    raw = value.encode()
    forms = {raw, json.dumps(value)[1:-1].encode(), urllib.parse.quote(value, safe="").encode(),
             base64.b64encode(raw), base64.urlsafe_b64encode(raw)}
    for line in value.splitlines():
        if len(line.strip().encode()) >= MIN_SECRET_BYTES:
            forms.add(line.strip().encode())
    return {form for form in forms if len(form) >= MIN_SECRET_BYTES}


def scrub(data: bytes, secrets: list) -> tuple:
    count = 0
    for secret in sorted(secrets, key=len, reverse=True):
        count += data.count(secret)
        data = data.replace(secret, b"***")
    return data, count


def main() -> int:
    path = Path(sys.argv[1])
    if path.is_symlink() or not path.is_file():
        raise SystemExit("log must be one regular non-symlink file")
    if path.stat().st_size > MAX_LOG_BYTES:
        raise SystemExit("log exceeds the reviewed text-evidence bound")
    secrets = set()
    for name, value in os.environ.items():
        if name.startswith("CI_SECRET_") and value:
            secrets |= variants(value)
    data, count = scrub(path.read_bytes(), list(secrets))
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".scrub-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"scrubbed {count} credential occurrence(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
