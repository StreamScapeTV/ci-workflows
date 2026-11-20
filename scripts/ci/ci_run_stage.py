#!/usr/bin/env python3
"""Run one ecosystem stage and stream its scrubbed log as numbered chunks.

* The command comes from ``ci_stage.resolve`` (convention, or a transition ``.ci`` script).
* The product process never receives ``CI_SECRET_*`` values; it only sees credential *files* under
  the ephemeral home. Those values are used here to redact its output.
* Output is written to ``<log-dir>/<seq>.log`` every ``--interval`` seconds (S3 has no append), each
  chunk is handed to the uploader as soon as it exists, and ``final.log`` (the merged scrubbed log,
  bounded) is handed over at the end. The uploader is a command template taken from the
  ``CI_LOG_UPLOADER`` environment variable (JSON argv list with ``{path}`` and ``{key}``); it is
  the shared S3 uploader. With no uploader the chunks stay local and everything is also echoed to
  the GitHub step log (already scrubbed).
* Object keys: ``logs/<project>/<request_id>/<stage>/<seq>.log`` and ``.../<stage>/final.log``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_scrub_log  # noqa: E402
import ci_stage  # noqa: E402
import ci_validate_inputs  # noqa: E402

MAX_TOTAL_BYTES = 48 * 1024 * 1024
TAIL_BYTES = 8 * 1024 * 1024


class ChunkStream:
    def __init__(self, directory: Path, prefix: str, secrets: list, uploader, interval: float):
        self.directory, self.prefix, self.secrets = directory, prefix, secrets
        self.uploader, self.interval = uploader, interval
        self.lock = threading.Lock()
        self.buffer = bytearray()
        self.sequence = 0
        self.total = 0
        self.truncated = False
        self.tail = bytearray()
        self.upload_failures = 0
        self.stop = threading.Event()
        directory.mkdir(parents=True, exist_ok=True)
        self.flusher = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.flusher.start()

    def add(self, line: bytes):
        data, _ = ci_scrub_log.scrub(line, self.secrets)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        with self.lock:
            self.buffer += data
            self.tail += data
            if len(self.tail) > TAIL_BYTES:
                del self.tail[: len(self.tail) - TAIL_BYTES]

    def _upload(self, path: Path, name: str):
        if not self.uploader:
            return
        argv = [part.replace("{path}", str(path)).replace("{key}", f"{self.prefix}/{name}") for part in self.uploader]
        try:
            subprocess.run(argv, check=True, timeout=120, stdout=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError):
            self.upload_failures += 1
            print(f"::warning::log chunk upload failed ({name})", file=sys.stderr)

    def flush(self):
        with self.lock:
            data, self.buffer = bytes(self.buffer), bytearray()
        if not data:
            return
        if self.total >= MAX_TOTAL_BYTES:
            self.truncated = True
            return
        self.total += len(data)
        self.sequence += 1
        path = self.directory / f"{self.sequence:06d}.log"
        path.write_bytes(data)
        path.chmod(0o600)
        self._upload(path, path.name)

    def _loop(self):
        while not self.stop.wait(self.interval):
            self.flush()

    def finish(self):
        self.stop.set()
        self.flusher.join(timeout=self.interval + 5)
        self.flush()
        final = self.directory / "final.log"
        if self.truncated:
            final.write_bytes(b"[log truncated: showing the last " + str(TAIL_BYTES).encode() + b" bytes]\n" + bytes(self.tail))
        else:
            with final.open("wb") as merged:
                for chunk in sorted(self.directory.glob("[0-9]*.log")):
                    merged.write(chunk.read_bytes())
        final.chmod(0o600)
        self._upload(final, "final.log")


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
    parser.add_argument("--project", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--host-os", required=True, choices=("linux", "macos"))
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--home", default="")
    parser.add_argument("--gradle-user-home", default="")
    parser.add_argument("--git-config", default="")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args(argv)

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    options = json.loads(os.environ.get("CI_OPTIONS_JSON", "{}"))
    source = Path(args.source).resolve()
    command = ci_stage.resolve(args.ecosystem, args.stage, source, options,
                               derived_data=str(Path(args.work_dir) / f"derived-{args.stage}"))
    stage_inputs = ci_validate_inputs.stage_inputs(contract, args.stage, options)
    env = child_environment(args, stage_inputs)

    uploader = json.loads(os.environ["CI_LOG_UPLOADER"]) if os.environ.get("CI_LOG_UPLOADER") else None
    prefix = f"logs/{args.project}/{args.request_id}/{args.stage}"
    stream = ChunkStream(Path(args.log_dir), prefix, secrets_from_environment(), uploader, args.interval)
    stream.start()
    stream.add(f"== stage {args.stage} ({command['mode']}): {' '.join(command['argv'][:3])} ==\n".encode())
    process = subprocess.Popen(command["argv"], cwd=source, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, bufsize=0)
    for line in iter(process.stdout.readline, b""):
        stream.add(line)
    status = process.wait()
    stream.add(f"== stage {args.stage} exit {status} ==\n".encode())
    stream.finish()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
