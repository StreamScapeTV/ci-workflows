import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[2]
lock = json.loads((root / '.ciw/oci-build-inputs/runner-general-linux-amd64.json').read_text())
dest = Path(__file__).resolve().parent / '.ciw-build-inputs'
dest.mkdir(exist_ok=False)
for item in lock['external_inputs']:
    with urlopen(item['url'], timeout=60) as response:
        data = response.read(item['maximum_bytes'] + 1)
    if len(data) > item['maximum_bytes']:
        raise SystemExit('input too large')
    if hashlib.sha256(data).hexdigest() != item['sha256']:
        raise SystemExit('input digest mismatch')
    (dest / Path(item['destination']).name).write_bytes(data)
