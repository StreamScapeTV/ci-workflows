#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--apk", required=True)
parser.add_argument("--aab", required=True)
parser.add_argument("--budget", required=True)
parser.add_argument("--baseline", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

apk = Path(args.apk)
aab = Path(args.aab)
budget = json.loads(Path(args.budget).read_text(encoding="utf-8"))
baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
apk_bytes = apk.stat().st_size
aab_bytes = aab.stat().st_size
if apk_bytes > int(budget["maximum_apk_bytes"]):
    raise SystemExit("synthetic APK exceeds budget")
if aab_bytes > int(budget["maximum_aab_bytes"]):
    raise SystemExit("synthetic AAB exceeds budget")
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(
    json.dumps(
        {
            "apk_bytes": apk_bytes,
            "aab_bytes": aab_bytes,
            "baseline_apk_bytes": int(baseline["apk_bytes"]),
            "baseline_aab_bytes": int(baseline["aab_bytes"]),
            "status": "success",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n",
    encoding="utf-8",
)
