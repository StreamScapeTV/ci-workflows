#!/usr/bin/env bash
set -Eeuo pipefail
python3 - <<'PY'
from __future__ import annotations
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
layout = Path(os.environ["OCI_LAYOUT"])
index = layout / "index.json"
digest = "sha256:" + hashlib.sha256(index.read_bytes()).hexdigest()
if digest != os.environ["OCI_INDEX_DIGEST"]:
    raise SystemExit("smoke index digest mismatch")
value = json.loads(index.read_text(encoding="utf-8"))
if value.get("schemaVersion") != 2 or len(value.get("manifests", [])) != 1:
    raise SystemExit("smoke layout is malformed")
descriptor = value["manifests"][0]
manifest_digest = descriptor["digest"].removeprefix("sha256:")
manifest = json.loads((layout / "blobs" / "sha256" / manifest_digest).read_text())
files: set[str] = set()
for layer in manifest["layers"]:
    blob = (layout / "blobs" / "sha256" / layer["digest"].removeprefix("sha256:")).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
        files.update(member.name.lstrip("./") for member in archive.getmembers())
if "hello" not in files:
    raise SystemExit("smoke image is missing /hello")
if os.environ["OCI_REQUIRED_FILES_JSON"] != '["/hello"]':
    raise SystemExit("smoke required-file contract drift")
if json.loads(os.environ["OCI_FORBIDDEN_TOOLS_JSON"]) != ["docker", "dockerd", "kubectl"]:
    raise SystemExit("smoke forbidden-tool contract drift")
PY
