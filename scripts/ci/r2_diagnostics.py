#!/usr/bin/env python3
"""Upload and verify one bounded private CI diagnostic in Cloudflare R2."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw_types import CIWError, write_command_file
from ci_workflows.r2_diagnostics import R2DiagnosticError, upload_private_diagnostic


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--diagnostic", type=Path, required=True)
    result.add_argument("--request-id", required=True)
    result.add_argument("--run-id", type=int, required=True)
    result.add_argument("--attempt", type=int, required=True)
    result.add_argument("--github-output", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        uploaded = upload_private_diagnostic(
            diagnostic_path=args.diagnostic,
            request_id=args.request_id,
            run_id=args.run_id,
            attempt=args.attempt,
            account_id=os.environ.get("R2_ACCOUNT_ID", ""),
            bucket=os.environ.get("R2_BUCKET", ""),
            access_key_id=os.environ.get("R2_ACCESS_KEY_ID", ""),
            secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", ""),
        )
        write_command_file(
            args.github_output,
            {
                "object_key": uploaded.object_key,
                "sha256": uploaded.sha256,
                "compressed_bytes": str(uploaded.compressed_bytes),
            },
        )
    except (R2DiagnosticError, CIWError) as error:
        print(error.code, file=sys.stderr)
        return 2
    print("Private diagnostic uploaded and verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
