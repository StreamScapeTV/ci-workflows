import hashlib
import json
import shutil
from pathlib import Path
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[2]
lock = json.loads((root / '.ciw/oci-build-inputs/runner-general-linux-amd64.json').read_text())
dest = Path(__file__).resolve().parent / '.ciw-build-inputs'
dest.mkdir(exist_ok=False)
try:
    for item in lock['external_inputs']:
        req = Request(item['url'], headers={'User-Agent': 'ci-workflows-validation'})
        with urlopen(req, timeout=60) as response:
            data = response.read(item['maximum_bytes'] + 1)
        if len(data) > item['maximum_bytes']:
            raise SystemExit(f"input too large: {item['input_id']}")
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise SystemExit(f"input digest mismatch: {item['input_id']}")
        (dest / Path(item['destination']).name).write_bytes(data)
except BaseException:
    shutil.rmtree(dest, ignore_errors=True)
    raise
