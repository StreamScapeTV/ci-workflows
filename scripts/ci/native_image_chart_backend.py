#!/usr/bin/env python3
"""Resolve the bounded execution and registry backend for native image + Helm release."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import runners
from ci_workflows.ciw_types import write_command_file
from ci_workflows.execution_backends import ExecutionBackendError, resolve_execution_backend


_BACKENDS = {
    "organization": {
        "registry": "git.faruqi.dev",
        "registry_namespace": "mimranfaruqi",
        "chart_namespace": "mimranfaruqi/helm-charts",
        "credential_mode": "private-secrets",
    },
    "github-hosted": {
        "registry": "ghcr.io",
        "registry_namespace": "streamscapetv",
        "chart_namespace": "streamscapetv/helm-charts",
        "credential_mode": "github-token",
    },
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=ROOT)
    result.add_argument("--execution-backend", required=True)
    result.add_argument("--github-output", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    root = args.root.resolve()
    try:
        organization = runners.resolve_runner_profile(
            runners.load_runner_contract(root),
            workflow_api="oci.publish",
            source_trust="trusted-exact",
            requested_profile="buildah-high",
        )
        resolved = resolve_execution_backend(
            execution_backend=args.execution_backend,
            execution_profile=organization.execution_profile,
            organization_runs_on=organization.runs_on,
            workflow_api="release.native-image-chart",
        )
        publication = _BACKENDS[resolved.execution_backend]
    except (runners.RunnerContractError, ExecutionBackendError, KeyError) as error:
        print(getattr(error, "code", "invalid_execution_backend"), file=sys.stderr)
        return 2

    payload = resolved.as_dict()
    payload.update(publication)
    outputs = {
        "execution_backend": str(payload["execution_backend"]),
        "runs_on_json": str(payload["runs_on_json"]),
        "registry": str(payload["registry"]),
        "registry_namespace": str(payload["registry_namespace"]),
        "chart_namespace": str(payload["chart_namespace"]),
        "credential_mode": str(payload["credential_mode"]),
    }
    if args.github_output is not None:
        write_command_file(args.github_output, outputs)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
