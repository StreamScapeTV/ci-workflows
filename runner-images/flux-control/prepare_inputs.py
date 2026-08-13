from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / ".ciw/oci-build-inputs/runner-flux-control-linux-amd64.json"
DESTINATION = Path(__file__).resolve().parent / ".ciw-build-inputs"


def main() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    DESTINATION.mkdir(exist_ok=False)
    try:
        for item in lock["external_inputs"]:
            request = Request(item["url"], headers={"User-Agent": "ci-workflows-runner-image-validation"})
            with urlopen(request, timeout=90) as response:
                data = response.read(item["maximum_bytes"] + 1)
            if len(data) > item["maximum_bytes"]:
                raise RuntimeError(f"input too large: {item['input_id']}")
            actual = hashlib.sha256(data).hexdigest()
            if actual != item["sha256"]:
                raise RuntimeError(f"input digest mismatch: {item['input_id']}")
            (DESTINATION / Path(item["destination"]).name).write_bytes(data)
    except BaseException:
        shutil.rmtree(DESTINATION, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
