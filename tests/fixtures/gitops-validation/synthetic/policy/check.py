#!/usr/bin/env python3
"""Inert source-only policy used by the exact-head synthetic smoke."""
from __future__ import annotations

import json
from pathlib import Path

root = Path.cwd()
required = (
    "tests/fixtures/gitops-validation/synthetic/yaml/configmap.yaml",
    "tests/fixtures/gitops-validation/synthetic/helm/Chart.lock",
    "tests/fixtures/gitops-validation/synthetic/kustomize/kustomization.yaml",
)
missing = [path for path in required if not (root / path).is_file()]
if missing:
    raise SystemExit("required fixture path is missing")
print(json.dumps({"policy": "synthetic-source-only", "result": "passed"}, sort_keys=True))
