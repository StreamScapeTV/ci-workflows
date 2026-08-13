import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[2]
lock = json.loads((root / '.ciw/oci-build-inputs/runner-general-linux-amd64.json').read_text())
for item in lock['external_inputs']:
    req = Request(item['url'], headers={'User-Agent': 'ci-workflows-validation'})
    with urlopen(req, timeout=60) as response:
        data = response.read(item['maximum_bytes'] + 1)
    print(item['input_id'])
    print(hashlib.sha256(data).hexdigest())
