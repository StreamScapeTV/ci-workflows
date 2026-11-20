#!/usr/bin/env python3
"""Run one ecosystem stage and write its scrubbed output to a growing log file.

* The command comes from ``ci_stage.resolve`` (convention, or a transition ``.ci`` script).
* The product process never receives ``CI_SECRET_*`` values; it only sees credential *files* under
  the ephemeral home. Those values are used here to redact its output.
* Output is scrubbed line by line, appended to ``--log-path`` (flushed per line) and echoed to the
  GitHub step log. The shared ``actions/s3-log-stream`` action (started before this step, stopped
  with ``if: always()`` after it) uploads that growing file as numbered chunks
  ``logs/<project>/<request_id>/<stage>/<seq>.log`` plus a merged ``final.log``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_scrub_log  # noqa: E402
import ci_stage  # noqa: E402
import ci_validate_inputs  # noqa: E402


class LogFile:
    def __init__(self, path: Path, secrets: list):
        self.secrets = secrets
        self.handle = path.open("ab", buffering=0)
        path.chmod(0o600)

    def add(self, line: bytes):
        data, _ = ci_scrub_log.scrub(line, self.secrets)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        self.handle.write(data)

    def close(self):
        self.handle.close()


def secrets_from_environment() -> list:
    secrets = set()
    for name, value in os.environ.items():
        if name.startswith("CI_SECRET_") and value:
            secrets |= ci_scrub_log.variants(value)
    return sorted(secrets, key=len, reverse=True)


def child_environment(args, stage_inputs: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CI_SECRET_", "INPUT_", "ACTIONS_RUNTIME", "ACTIONS_ID_TOKEN"))}
    if args.home:
        env["HOME"] = args.home
    if args.gradle_user_home:
        env["GRADLE_USER_HOME"] = args.gradle_user_home
    if args.git_config:
        env["GIT_CONFIG_GLOBAL"] = args.git_config
        env["GIT_TERMINAL_PROMPT"] = "0"
    env.update({
        "CI_HOST_OS": args.host_os,
        "CI_HOST_CLASS": f"{args.host_os}-hosted",
        "CI_OPERATION": args.stage,
        "CI_INPUTS_JSON": json.dumps(stage_inputs, sort_keys=True, separators=(",", ":")),
        "CI_LOG_DIR": args.work_dir + "/logs",
        "CI_ARTIFACT_DIR": args.work_dir + "/artifacts",
        "CI_PROGRESS_FILE": args.work_dir + "/progress.txt",
    })
    for key in ("CI_LOG_DIR", "CI_ARTIFACT_DIR"):
        Path(env[key]).mkdir(parents=True, exist_ok=True, mode=0o700)
    Path(env["CI_PROGRESS_FILE"]).touch(mode=0o600)
    return env


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ecosystem", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--source", default="source")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--host-os", required=True, choices=("linux", "macos"))
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--home", default="")
    parser.add_argument("--gradle-user-home", default="")
    parser.add_argument("--git-config", default="")
    args = parser.parse_args(argv)

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    options = json.loads(os.environ.get("CI_OPTIONS_JSON", "{}"))
    source = Path(args.source).resolve()
    command = ci_stage.resolve(args.ecosystem, args.stage, source, options,
                               derived_data=str(Path(args.work_dir) / f"derived-{args.stage}"))
    stage_inputs = ci_validate_inputs.stage_inputs(contract, args.stage, options)
    env = child_environment(args, stage_inputs)

    stream = LogFile(Path(args.log_path), secrets_from_environment())
    if command["mode"] == "skip":
        stream.add(f"== stage {args.stage} skipped: {command['reason']} ==\n".encode())
        stream.close()
        return 0
    stream.add(f"== stage {args.stage} ({command['mode']}) ==\n".encode())
    process = subprocess.Popen(command["argv"], cwd=source, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, bufsize=0)
    for line in iter(process.stdout.readline, b""):
        stream.add(line)
    status = process.wait()
    stream.add(f"== stage {args.stage} exit {status} ==\n".encode())
    stream.close()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
