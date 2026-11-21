#!/usr/bin/env python3
"""Run every requested stage (lint, test, build) of a Node or Python project in ONE job.

The dependencies are installed ONCE (npm ci / the Python venv with its dependencies and tools); then the
stage commands run as parallel processes, each writing its own scrubbed log file
(``<log-dir>/stage-<stage>.log``) that the S3 log streams upload. The Actions log gets status lines only.

* A stage without a convention is ``skipped`` (never a failure).
* A failed install fails every stage that needed it. Its output goes to the logs of those stages.
* A project that ships tracked ``.ci/<stage>.sh`` transition scripts keeps them: every stage then runs its
  own full command one after the other in the job (build first) and a failed stage never stops the next.
* Results go to ``$GITHUB_OUTPUT`` as ``<stage>=passed|failed|skipped`` and ``request_ok=true|false``; the
  step exits 0 once the stages ran (the job is failed by a later step from ``request_ok``). Lint decides the
  request only when it is the requested top operation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_run_stage  # noqa: E402
import ci_stage  # noqa: E402
import ci_validate_inputs  # noqa: E402

ORDER = ("build", "test", "lint")
PARTS = {"node": ci_stage.node_parts, "python": ci_stage.python_parts}


def request_ok(stages: list, results: dict, top: str) -> bool:
    """Skipped counts as fine; lint decides only as the requested top operation."""
    return all(results.get(stage) in ("passed", "skipped") for stage in stages if stage != "lint" or top == "lint")


def stream(process, log) -> None:
    for raw in iter(process.stdout.readline, b""):
        log.add(raw)


def run_logged(argv, source, env, logs: list) -> int:
    """Run one command; every line goes to each given log (the scrubbing happens inside the log)."""
    process = subprocess.Popen(argv, cwd=source, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)

    class Fan:
        def add(self, line):
            for log in logs:
                log.add(line)

    stream(process, Fan())
    return process.wait()


def run_convention(ecosystem, stages, parts, source, env_for, logs) -> dict:
    """One install, then the stage commands in parallel."""
    active = [s for s in stages if parts[s][0] == "ok"]
    results = {s: "skipped" for s in stages if s not in active}
    if not active:
        return results
    if ecosystem == "node":
        install = parts[active[0]][1]["prep"]
        wrapper = {s: parts[s][1]["run"] for s in active}
    else:
        values = [parts[s][1] for s in active]
        install = " && ".join([values[0]["venv"]] + [v for v in dict.fromkeys(x[k] for x in values for k in ("deps", "tools")) if v])
        wrapper = {s: f". .ci-venv/bin/activate && {parts[s][1]['run']}" for s in active}
    code = run_logged(["bash", "-ec", install], source, env_for(active[0]), [logs[s] for s in active])
    if code != 0:
        return {**results, **{s: "failed" for s in active}}
    codes: dict = {}

    def one(*sequence):
        for stage in sequence:  # a sequence runs one stage after the other (the earlier one never gates the later)
            codes[stage] = run_logged(["bash", "-ec", wrapper[stage]], source, env_for(stage), [logs[stage]])

    # Node: `next build` rewrites .next/types and the tsbuildinfo that the typecheck (lint) reads, so lint starts
    # after the build; the test stage stays parallel with both. Python has no such shared output.
    groups = [[s] for s in active]
    if ecosystem == "node" and "build" in active and "lint" in active:
        groups = [["build", "lint"]] + [[s] for s in active if s not in ("build", "lint")]
    threads = [threading.Thread(target=one, args=tuple(group)) for group in groups]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return {**results, **{s: "passed" if codes[s] == 0 else "failed" for s in active}}


def run_scripts(stages, commands, source, env_for, logs) -> dict:
    results = {}
    for stage in stages:
        command = commands[stage]
        if command["mode"] == "skip":
            logs[stage].add(f"== stage {stage} skipped: {command['reason']} ==\n".encode())
            results[stage] = "skipped"
            continue
        results[stage] = "passed" if run_logged(command["argv"], source, env_for(stage), [logs[stage]]) == 0 else "failed"
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ecosystem", required=True, choices=sorted(PARTS))
    parser.add_argument("--stages", required=True, help="JSON array of requested stages")
    parser.add_argument("--top", required=True)
    parser.add_argument("--source", default="source")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--home", default="")
    parser.add_argument("--git-config", default="")
    parser.add_argument("--s3-base", default="", help="logs/<repo>/<request_id>/ ; the stage name is appended")
    args = parser.parse_args(argv)

    requested = json.loads(args.stages)
    stages = [s for s in ORDER if s in requested]
    if not stages or sorted(stages) != sorted(requested):
        raise SystemExit("stages must be a non-empty subset of lint, test, build")
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    options = json.loads(os.environ.get("CI_OPTIONS_JSON", "{}"))
    source = Path(args.source).resolve()
    commands = {stage: ci_stage.resolve(args.ecosystem, stage, source, options) for stage in stages}
    namespace = dict(home=args.home, gradle_user_home="", git_config=args.git_config, host_os="linux", work_dir=args.work_dir)

    def env_for(stage: str) -> dict:
        return ci_run_stage.child_environment(argparse.Namespace(**namespace, stage=stage),
                                              ci_validate_inputs.stage_inputs(contract, stage, options))

    secrets = ci_run_stage.secrets_from_environment()
    logs = {stage: ci_run_stage.LogFile(Path(args.log_dir) / f"stage-{stage}.log", secrets) for stage in stages}

    def where(stage):
        return f" s3={args.s3_base}{stage}/" if args.s3_base else ""

    for stage in stages:
        ci_run_stage.status(f"stage {stage} started{where(stage)}")
        logs[stage].add(f"== stage {stage} ({commands[stage]['mode']}) ==\n".encode())
    if any(command["mode"] == "script" for command in commands.values()):
        results = run_scripts(stages, commands, source, env_for, logs)
    else:
        parts = {stage: PARTS[args.ecosystem](source, stage) for stage in stages}
        for stage in stages:
            if parts[stage][0] == "skip":
                logs[stage].add(f"== stage {stage} skipped: {parts[stage][1]} ==\n".encode())
        results = run_convention(args.ecosystem, stages, parts, source, env_for, logs)
    for stage in stages:
        logs[stage].add(f"== stage {stage} exit {1 if results[stage] == 'failed' else 0} ==\n".encode())
        logs[stage].close()
        outcome = {"passed": "ok", "failed": "failed", "skipped": "skipped"}[results[stage]]
        ci_run_stage.status(f"stage {stage} finished result={outcome}{where(stage)}")
    ok = request_ok(stages, results, args.top)
    ci_run_stage.status(f"request {'ok' if ok else 'failed'} lint_is_warning_only={args.top != 'lint'}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for stage in stages:
                handle.write(f"{stage}={results[stage]}\n")
            handle.write(f"request_ok={'true' if ok else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
