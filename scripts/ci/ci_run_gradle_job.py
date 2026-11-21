#!/usr/bin/env python3
"""Run every requested Gradle stage (lint, test, build) in ONE job and ONE Gradle invocation.

Compilation, the daemon and the build cache are shared: the requested stages become one command
(``assemble`` / ``test`` / ``check``) run with ``--continue --parallel``. The output is split into one
scrubbed log file per stage (``<log-dir>/stage-<stage>.log``), which the S3 log streams upload; the
Actions log gets status lines only (see ci_run_stage.py).

Which stage a task belongs to is decided by a Gradle ``--dry-run`` of each stage's own task list: a task
in the closure of a stage is that stage's output, and a ``FAILED`` task in the closure fails the stage.
A failure outside every closure (configuration) fails all stages.

A project that still ships tracked ``.ci/<stage>.sh`` transition scripts keeps them: the stages then run
one after the other in one job, build first, never stopping at a failed stage.

Results go to ``$GITHUB_OUTPUT`` as ``<stage>=passed|failed`` and ``request_ok=true|false``. The step
itself exits 0 once the stages ran; a later step fails the job from ``request_ok``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_run_stage  # noqa: E402
import ci_stage  # noqa: E402
import ci_validate_inputs  # noqa: E402

ORDER = ("build", "test", "lint")  # execution and reporting priority: the build starts first
# Two workers: a hosted runner has 4 cores and ~16 GB; lint, R8 and Robolectric in parallel froze it for an hour.
COMMON = ["./gradlew", "--no-daemon", "--console=plain", "--continue", "--parallel", "--max-workers=2"]
HEADER = re.compile(r"^> Task (:\S*)")
FAILED_TASK = re.compile(r"^> Task (:\S*) FAILED$")
DRY_TASK = re.compile(r"^(?:> Task )?(:\S+) (?:SKIPPED|UP-TO-DATE|NO-SOURCE|FROM-CACHE)$")
END_OF_TASKS = re.compile(r"^(FAILURE:|BUILD (?:SUCCESSFUL|FAILED)|\* What went wrong)")


def request_ok(stages: list, results: dict, top: str) -> bool:
    """Build and test (and release) decide the request; lint only when lint is the requested top operation."""
    return all(results.get(stage) == "passed" for stage in stages if stage != "lint" or top == "lint")


def root_tasks(stages: list, source: Path) -> list:
    """One command for all requested stages (generic: ``check`` already contains ``test``)."""
    if ci_stage.android_project(source):
        wanted = [t for stage in ORDER if stage in stages for t in ci_stage.gradle_tasks(source, stage)]
        return list(dict.fromkeys(wanted))
    tasks: list = []
    if "build" in stages:
        tasks.append("assemble")
    if "lint" in stages and "test" in stages:
        tasks.append("check")
    elif "lint" in stages:
        tasks += ["check", "-x", "test"]
    elif "test" in stages:
        tasks.append("test")
    return tasks


def closures(stages: list, source: Path, env: dict) -> dict:
    """Task paths each stage would run (Gradle --dry-run of the stage's own tasks)."""
    result = {}
    for stage in stages:
        done = subprocess.run([*COMMON, "--dry-run", *ci_stage.gradle_tasks(source, stage)], cwd=source, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        result[stage] = {m.group(1) for m in map(DRY_TASK.match, done.stdout.splitlines()) if m}
    return result


def thread_dump(logs: list, reason: str) -> None:
    """jstack of every JVM on the runner (Gradle daemon, test workers, Kotlin daemon), into the given logs."""
    def add(text: str) -> None:
        for log in logs:
            log.add((text + "\n").encode())

    add(f"== watchdog: {reason}; thread dump of every JVM ==")
    try:
        listing = subprocess.run(["jps", "-l"], text=True, capture_output=True, timeout=60).stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as error:
        add(f"== watchdog: jps unavailable ({type(error).__name__}) ==")
        return
    for line in listing:
        pid = line.split(" ", 1)[0]
        if not pid.isdigit() or "Jps" in line:
            continue
        add(f"== jstack {line} ==")
        try:
            dump = subprocess.run(["jstack", pid], text=True, capture_output=True, timeout=60)
            add(dump.stdout + dump.stderr)
        except (OSError, subprocess.SubprocessError) as error:
            add(f"== jstack failed ({type(error).__name__}) ==")


def run_combined(stages, source, env, logs, closure, timeout_s=90 * 60, stall_s=20 * 60) -> dict:
    """One Gradle run with a watchdog: a thread dump after ``stall_s`` without output, and thread dump plus kill
    of the whole process group after ``timeout_s``, so the stages still end with an exit line and a final log."""
    process = subprocess.Popen([*COMMON, *root_tasks(stages, source)], cwd=source, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, bufsize=0, start_new_session=True)
    state = {"last": time.monotonic(), "dumped": False, "killed": False}
    everyone = [logs[stage] for stage in stages]
    started = time.monotonic()

    def watch() -> None:
        while process.poll() is None:
            time.sleep(1)
            now = time.monotonic()
            if not state["dumped"] and now - state["last"] > stall_s:
                state["dumped"] = True
                thread_dump(everyone, f"no output for {int(stall_s)} s")
            if now - started > timeout_s:
                state["killed"] = True
                thread_dump(everyone, f"run time limit {int(timeout_s)} s reached")
                for log in everyone:
                    log.add(f"== watchdog: killing the Gradle process group after {int(timeout_s)} s ==\n".encode())
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                return

    threading.Thread(target=watch, daemon=True).start()
    current: set | None = None
    failed_tasks: list = []
    for raw in iter(process.stdout.readline, b""):
        state["last"] = time.monotonic()
        text = raw.decode("utf-8", "replace").rstrip("\n")
        header = HEADER.match(text)
        if header:
            task = header.group(1)
            owners = {s for s in stages if task in closure[s]}
            current = owners or None
            bad = FAILED_TASK.match(text)
            if bad:
                failed_tasks.append(task)
        elif END_OF_TASKS.match(text):
            current = None
        for stage in (current if current else stages):
            logs[stage].add(raw)
    code = process.wait()
    results = {stage: "passed" for stage in stages}
    if state["killed"]:
        return {stage: "failed" for stage in stages}  # unfinished: which stage was stuck is in the thread dump
    if code != 0:
        hit = {s for s in stages if any(t in closure[s] for t in failed_tasks)}
        for stage in (hit or stages):  # no failed task in any closure: a configuration failure fails everything
            results[stage] = "failed"
    return results


def run_scripts(stages, source, commands, env_for, logs) -> dict:
    results = {}
    for stage in stages:
        command = commands[stage]
        process = subprocess.Popen(command["argv"], cwd=source, env=env_for(stage), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=0)
        for raw in iter(process.stdout.readline, b""):
            logs[stage].add(raw)
        results[stage] = "passed" if process.wait() == 0 else "failed"
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stages", required=True, help="JSON array of requested stages")
    parser.add_argument("--top", required=True)
    parser.add_argument("--source", default="source")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--home", default="")
    parser.add_argument("--gradle-user-home", default="")
    parser.add_argument("--git-config", default="")
    parser.add_argument("--timeout-minutes", type=float, default=90, help="kill the Gradle run after this long")
    parser.add_argument("--stall-minutes", type=float, default=20, help="thread dump after this long without output")
    parser.add_argument("--s3-base", default="", help="logs/<repo>/<request_id>/ ; the stage name is appended")
    args = parser.parse_args(argv)

    requested = json.loads(args.stages)
    stages = [s for s in ORDER if s in requested]
    if not stages or sorted(stages) != sorted(requested):
        raise SystemExit("stages must be a non-empty subset of lint, test, build")
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    options = json.loads(os.environ.get("CI_OPTIONS_JSON", "{}"))
    source = Path(args.source).resolve()
    commands = {stage: ci_stage.resolve("gradle", stage, source, options) for stage in stages}
    plain_args = argparse.Namespace(home=args.home, gradle_user_home=args.gradle_user_home,
                                    git_config=args.git_config, host_os="linux", stage=args.top,
                                    work_dir=args.work_dir)

    def env_for(stage: str) -> dict:
        args_for = argparse.Namespace(**{**vars(plain_args), "stage": stage})
        return ci_run_stage.child_environment(args_for, ci_validate_inputs.stage_inputs(contract, stage, options))

    secrets = ci_run_stage.secrets_from_environment()
    logs = {stage: ci_run_stage.LogFile(Path(args.log_dir) / f"stage-{stage}.log", secrets) for stage in stages}

    def where(stage):
        return f" s3={args.s3_base}{stage}/" if args.s3_base else ""

    for stage in stages:
        ci_run_stage.status(f"stage {stage} started{where(stage)}")
        logs[stage].add(f"== stage {stage} ({commands[stage]['mode']}) ==\n".encode())
    if all(command["mode"] == "convention" for command in commands.values()):
        env = env_for(args.top)
        closure = closures(stages, source, env)
        ci_run_stage.status("gradle closures " + " ".join(f"{s}={len(closure[s])}" for s in stages)
                            + " (one combined invocation)")
        results = run_combined(stages, source, env, logs, closure, args.timeout_minutes * 60, args.stall_minutes * 60)
    else:
        results = run_scripts(stages, source, commands, env_for, logs)
    for stage in stages:
        logs[stage].add(f"== stage {stage} exit {0 if results[stage] == 'passed' else 1} ==\n".encode())
        logs[stage].close()
        outcome = "ok" if results[stage] == "passed" else "failed"
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
