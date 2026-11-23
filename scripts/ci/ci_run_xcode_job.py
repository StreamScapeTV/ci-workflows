#!/usr/bin/env python3
"""Run every requested Xcode stage (lint, build, test) in ONE job on ONE macOS runner.

* build and test share one DerivedData: on an Xcode project the build is ``build-for-testing`` and the test
  ``test-without-building``, so nothing compiles twice (Swift packages build once in ``.build``).
* lint (SwiftLint) runs as a parallel process next to that chain, never in front of it.
* A test that cannot run because the build failed is ``failed`` (note in its log). A stage the convention
  does not cover is ``skipped``.
* Tracked ``.ci/<stage>.sh`` transition scripts keep working: the stages then run one after the other,
  build first, and a failed stage never stops the next.
* One scrubbed log per stage (``<log-dir>/stage-<stage>.log``) for the S3 streams; only status lines reach
  the Actions log. The test fixture file is visible to the test stage only.

Results go to ``$GITHUB_OUTPUT`` as ``<stage>=passed|failed|skipped`` and ``request_ok``; the step exits 0
once the stages ran (a later step fails the job). Lint decides the request only as the top operation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_cache  # noqa: E402
import ci_run_job  # noqa: E402
import ci_run_stage  # noqa: E402
import ci_stage  # noqa: E402
import ci_validate_inputs  # noqa: E402

ORDER = ("build", "test", "lint")


def run_chain(chain, source, env_for, logs, results) -> None:
    broken = False
    for stage, command in chain:
        if command["mode"] == "skip":
            logs[stage].add(f"== stage {stage} skipped: {command['reason']} ==\n".encode())
            results[stage] = "skipped"
        elif broken:
            logs[stage].add(b"== stage not run: the build it needs failed ==\n")
            results[stage] = "failed"
        else:
            code = ci_run_job.run_logged(command["argv"], source, env_for(stage), [logs[stage]])
            results[stage] = "passed" if code == 0 else "failed"
            broken = broken or (code != 0 and stage == "build")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
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
    shared = str(Path(args.work_dir) / "derived-shared")
    # The compilation cache directory is the one the cache action saves and restores (native user home).
    cache_dir = str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ci_cache.COMPILATION_CACHE_RELATIVE)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    commands = {stage: ci_stage.resolve("xcode", stage, source, options, derived_data=shared, cache_dir=cache_dir)
                for stage in stages}
    namespace = dict(home=args.home, gradle_user_home="", git_config=args.git_config, host_os="macos", work_dir=args.work_dir)

    def env_for(stage: str) -> dict:
        env = ci_run_stage.child_environment(argparse.Namespace(**namespace, stage=stage),
                                             ci_validate_inputs.stage_inputs(contract, stage, options))
        env["CI_COMPILATION_CACHE_DIR"] = cache_dir  # for repository scripts (.ci) that run xcodebuild themselves
        if stage != "test":
            env.pop("CI_TEST_FIXTURE_FILE", None)  # the integration fixtures belong to the test stage only
        return env

    secrets = ci_run_stage.secrets_from_environment()
    logs = {stage: ci_run_stage.LogFile(Path(args.log_dir) / f"stage-{stage}.log", secrets) for stage in stages}

    def where(stage):
        return f" s3={args.s3_base}{stage}/" if args.s3_base else ""

    for stage in stages:
        ci_run_stage.status(f"stage {stage} started{where(stage)}")
        logs[stage].add(f"== stage {stage} ({commands[stage]['mode']}) ==\n".encode())
    results: dict = {}
    if any(command["mode"] == "script" for command in commands.values()):
        results = ci_run_job.run_scripts(stages, commands, source, env_for, logs)
    else:
        plan = ci_stage.xcode_chain(source, stages, options, shared, cache_dir)
        chain = threading.Thread(target=run_chain, args=(plan["chain"], source, env_for, logs, results))
        chain.start()
        lint = plan["lint"]
        if lint is not None:
            if lint["mode"] == "skip":
                logs["lint"].add(f"== stage lint skipped: {lint['reason']} ==\n".encode())
                results["lint"] = "skipped"
            else:
                code = ci_run_job.run_logged(lint["argv"], source, env_for("lint"), [logs["lint"]])
                results["lint"] = "passed" if code == 0 else "failed"
        chain.join()
    for stage in stages:
        logs[stage].add(f"== stage {stage} exit {1 if results[stage] == 'failed' else 0} ==\n".encode())
        logs[stage].close()
        outcome = {"passed": "ok", "failed": "failed", "skipped": "skipped"}[results[stage]]
        ci_run_stage.status(f"stage {stage} finished result={outcome}{where(stage)}")
    ok = ci_run_job.request_ok(stages, results, args.top)
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
