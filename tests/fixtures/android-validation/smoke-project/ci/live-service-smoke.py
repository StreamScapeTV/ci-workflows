#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess

username = os.environ.get("CIW_SERVICE_USERNAME", "")
password = os.environ.get("CIW_SERVICE_PASSWORD", "")
if username != "synthetic-user" or password != "synthetic-password":
    raise SystemExit("synthetic service credentials were not projected")
for forbidden in (
    "STREAMSCAPE_BACKEND_USERNAME",
    "STREAMSCAPE_BACKEND_PASSWORD",
    "STREAMSCAPE_EMAIL",
    "STREAMSCAPE_PASSWORD",
    "GITHUB_TOKEN",
):
    if os.environ.get(forbidden):
        raise SystemExit(f"forbidden inherited credential: {forbidden}")
completed = subprocess.run(
    ["bash", "gradlew", "verifyToolchainSmoke"],
    check=False,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if completed.returncode != 0:
    raise SystemExit("live-service smoke Gradle probe failed")
print("synthetic live-service acceptance passed")
