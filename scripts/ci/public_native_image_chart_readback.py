#!/usr/bin/env python3
"""Anonymous read-back verification for the public native image/chart release."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment: {name}")
    return value


def _inspect(reference: str, authfile: Path, *, raw: bool) -> bytes:
    command = ["skopeo", "inspect", "--authfile", str(authfile)]
    if raw:
        command.append("--raw")
    command.append(f"docker://{reference}")
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    return completed.stdout


def _manifest_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _image_identity(reference: str, authfile: Path) -> tuple[str, dict[str, object]]:
    raw = _inspect(reference, authfile, raw=True)
    digest = _manifest_digest(raw)
    inspect = json.loads(_inspect(reference, authfile, raw=False).decode("utf-8"))
    if not isinstance(inspect, dict):
        raise SystemExit("anonymous image read-back returned invalid JSON")
    if inspect.get("Os") != "linux" or inspect.get("Architecture") != "amd64":
        raise SystemExit("anonymous image read-back platform mismatch")
    if inspect.get("Digest") != digest:
        raise SystemExit("anonymous image read-back identity mismatch")
    return digest, inspect


def main() -> int:
    state = Path(_required("RUNNER_TEMP")) / "public-native-image-chart"
    state.mkdir(parents=True, exist_ok=True)
    authfile = state / "anonymous-auth.json"
    authfile.write_text("{}\n", encoding="utf-8")
    authfile.chmod(0o600)

    image_reference = _required("IMAGE_REFERENCE")
    latest_reference = _required("LATEST_IMAGE_REFERENCE")
    chart_reference = (
        f"{_required('REGISTRY')}/{_required('CHART_NAMESPACE')}/"
        f"{_required('CHART_NAME')}:{_required('VERSION')}"
    )

    image_digest, _ = _image_identity(image_reference, authfile)
    chart_digest = _manifest_digest(_inspect(chart_reference, authfile, raw=True))

    if os.environ.get("PUBLISH_LATEST_IMAGE", "false").lower() == "true":
        latest_digest, _ = _image_identity(latest_reference, authfile)
        if latest_digest != image_digest:
            raise SystemExit("latest image alias does not match immutable release image")

    output = Path(_required("GITHUB_OUTPUT"))
    with output.open("a", encoding="utf-8") as handle:
        handle.write(f"image_reference={image_reference}\n")
        handle.write(f"image_digest={image_digest}\n")
        handle.write(f"chart_reference=oci://{chart_reference}\n")
        handle.write(f"chart_digest={chart_digest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
