#!/usr/bin/env python3
"""Resolve the command of one ecosystem stage without a per-project script.

Convention first: a project that follows its ecosystem's standard needs no ``.ci`` directory.
Transition: while a project still ships a tracked, executable ``.ci/<stage>.sh`` (iptv-apple,
iptv-android), that script is used instead; it is deleted once the project follows the standard.
A stage the project does not support fails with a message that says what to provide.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

SCRIPT = {"lint": ".ci/lint.sh", "build": ".ci/build.sh", "test": ".ci/test.sh"}


def _tracked(source: Path, relative: str):
    result = subprocess.run(["git", "-C", str(source), "ls-files", "-s", "--", relative],
                            capture_output=True, text=True, check=True).stdout.split()
    return result[0] if result else None


def _tracked_names(source: Path) -> list:
    raw = subprocess.run(["git", "-C", str(source), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [item.decode() for item in raw.split(b"\0") if item]


def _fail(message: str):
    raise SystemExit(message)


def _skip(reason: str):
    return {"mode": "skip", "argv": [], "reason": reason}


def _script(source: Path, stage: str):
    relative = SCRIPT[stage]
    mode = _tracked(source, relative)
    if mode is None:
        return None
    path = source / relative
    if mode != "100755" or path.is_symlink() or not path.is_file():
        _fail(f"{relative} must be a tracked regular executable file (mode 100755)")
    return {"mode": "script", "argv": ["./" + relative]}


ANDROID_MARKERS = ("build.gradle", "build.gradle.kts", "app/build.gradle", "app/build.gradle.kts", "gradle/libs.versions.toml")


def android_project(source: Path) -> bool:
    """An Android project applies a com.android.* plugin in a root/app build file or the version catalog."""
    for name in ANDROID_MARKERS:
        path = source / name
        if path.is_file() and "com.android." in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def gradle_tasks(source: Path, stage: str, options: dict | None = None) -> list:
    """Android: the debug variant only (all variants, benchmark and release included, is too slow and memory hungry)."""
    if android_project(source):
        release = (options or {}).get("build_configuration") == "release"  # only the build stage follows it
        return {"lint": ["lintDebug"], "build": ["assembleRelease" if release else "assembleDebug"],
                "test": ["testDebugUnitTest"]}[stage]
    return {"lint": ["check", "-x", "test"], "build": ["assemble"], "test": ["test"]}[stage]


def gradle(source: Path, stage: str, options: dict):
    if _tracked(source, "gradlew") != "100755" or (source / "gradlew").is_symlink():
        _fail("Gradle convention: commit an executable gradlew (mode 100755) and gradle/wrapper/gradle-wrapper.properties")
    return {"mode": "convention", "argv": ["./gradlew", "--no-daemon", *gradle_tasks(source, stage, options)]}


def xcode(source: Path, stage: str, options: dict, derived_data: str = "DerivedData"):
    names = _tracked_names(source)
    if stage == "lint":
        if ".swiftlint.yml" in names:
            return {"mode": "convention", "argv": ["swiftlint", "lint", "--strict"]}
        return _skip("no lint convention: add a tracked .swiftlint.yml (or a transition .ci/lint.sh) to enable it")
    workspaces = sorted({n.split("/")[0] for n in names if n.endswith(".xcworkspace/contents.xcworkspacedata") and n.count("/") == 1})
    projects = sorted({n.split("/")[0] for n in names if n.endswith(".xcodeproj/project.pbxproj") and n.count("/") == 1})
    configuration = {"debug": "Debug", "release": "Release", None: "Debug"}[options.get("build_configuration")]
    if not workspaces and not projects:
        if "Package.swift" not in names:
            _fail("Xcode convention: provide Package.swift, or one .xcworkspace/.xcodeproj at the repository root")
        argv = ["swift", "build" if stage == "build" else "test", "-c", configuration.lower()]
        return {"mode": "convention", "argv": argv}
    container = workspaces or projects
    if len(container) != 1:
        _fail("Xcode convention: exactly one .xcworkspace or .xcodeproj at the repository root")
    scheme = options.get("scheme")
    if not scheme:
        _fail("Xcode convention: pass options.scheme (the shared scheme to build)")
    platform = options.get("product_target")
    destinations = {"ios": "generic/platform=iOS Simulator", "tvos": "generic/platform=tvOS Simulator",
                    "macos": "platform=macOS"}
    if platform not in destinations:
        _fail("Xcode convention: pass options.product_target as ios, tvos or macos")
    if stage == "test" and platform != "macos":
        return _skip("simulator tests are not standardised yet: use product_target macos or keep .ci/test.sh (transition)")
    flag = "-workspace" if workspaces else "-project"
    argv = ["xcodebuild", flag, container[0], "-scheme", scheme, "-configuration", configuration,
            "-destination", destinations[platform], "-derivedDataPath", derived_data,
            "build" if stage == "build" else "test",
            "CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO"]
    return {"mode": "convention", "argv": argv}


def xcode_chain(source: Path, stages: list, options: dict, derived_data: str):
    """Plan of the Xcode convention for several stages sharing ONE DerivedData.

    Returns {"lint": command, "chain": [(stage, command), ...]}. The chain is the build and test stages in
    order: when both run on an Xcode project the build becomes ``build-for-testing`` and the test
    ``test-without-building`` (nothing compiles twice); a Swift package builds once in ``.build`` and tests
    on top of it. A stage the convention does not cover is a ``skip`` command.
    """
    commands = {stage: xcode(source, stage, options, derived_data) for stage in stages}
    if ("build" in commands and "test" in commands and commands["build"]["mode"] == "convention"
            and commands["test"]["mode"] == "convention" and commands["build"]["argv"][0] == "xcodebuild"
            and commands["test"]["argv"][0] == "xcodebuild"):
        for stage, before, after in (("build", "build", "build-for-testing"), ("test", "test", "test-without-building")):
            argv = list(commands[stage]["argv"])
            action = argv.index("CODE_SIGNING_ALLOWED=NO") - 1  # the action word, never a scheme or path of the same name
            assert argv[action] == before
            argv[action] = after
            commands[stage] = {**commands[stage], "argv": argv}
    return {"lint": commands.get("lint"), "chain": [(s, commands[s]) for s in ("build", "test") if s in commands]}


def python_parts(source: Path, stage: str):
    """Pieces of the Python convention: ('skip', reason) or ('ok', {venv, deps, tools, run})."""
    names = _tracked_names(source)
    top = {n for n in names if "/" not in n}
    text = ""
    for name in sorted(top):
        if name == "pyproject.toml" or (name.startswith("requirements") and name.endswith(".txt")):
            text += (source / name).read_text(encoding="utf-8", errors="replace")
    if "pyproject.toml" not in top and not any(n.startswith("requirements") for n in top):
        _fail("Python convention: provide pyproject.toml or requirements.txt")
    venv = "python3 -m venv .ci-venv && . .ci-venv/bin/activate && python -m pip install --quiet --upgrade pip"
    if "requirements-dev.txt" in top:
        deps = "python -m pip install --quiet -r requirements-dev.txt"
    elif "requirements.txt" in top:
        deps = "python -m pip install --quiet -r requirements.txt"
    else:
        deps = "(python -m pip install --quiet -e '.[dev]' || python -m pip install --quiet -e .)"
    if stage == "lint":
        if "[tool.ruff" not in text and "ruff.toml" not in top and ".ruff.toml" not in top:
            return "skip", "no lint convention: configure ruff ([tool.ruff] or ruff.toml) to enable it"
        return "ok", {"venv": venv, "deps": "", "tools": "python -m pip install --quiet ruff", "run": "ruff check ."}
    if stage == "build":
        if "pyproject.toml" not in top:
            return "skip", "no build convention: add pyproject.toml to build a distribution"
        return "ok", {"venv": venv, "deps": "", "tools": "python -m pip install --quiet build", "run": "python -m build"}
    if not any(n.startswith("tests/") for n in names):
        return "skip", "no test convention: add a tests/ directory"
    run = "python -m pytest -q" if "pytest" in text else "python -m unittest discover -s tests"
    return "ok", {"venv": venv, "deps": deps, "tools": "", "run": run}


def python(source: Path, stage: str, options: dict):
    """Python convention: an isolated venv, dependencies from the tracked manifest, standard tools."""
    kind, value = python_parts(source, stage)
    if kind == "skip":
        return _skip(value)
    body = " && ".join(part for part in (value["deps"], value["tools"], value["run"]) if part)
    return {"mode": "convention", "argv": ["bash", "-ec", f"{value['venv']} && {body}"]}


def node_parts(source: Path, stage: str):
    """Pieces of the Node convention: ('skip', reason) or ('ok', {prep, run})."""
    names = _tracked_names(source)
    if "package.json" not in names or "package-lock.json" not in names:
        _fail("Node convention: commit package.json and package-lock.json at the repository root")
    try:
        scripts = json.loads((source / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
    except (ValueError, AttributeError):
        _fail("Node convention: package.json is not valid JSON")
    if not isinstance(scripts, dict):
        _fail("Node convention: package.json scripts must be an object")
    wanted = {"lint": ("lint", "typecheck"), "test": ("test",), "build": ("build",)}[stage]
    script = next((name for name in wanted if name in scripts), None)
    if script is None:
        return "skip", f"no {stage} convention: add a package.json script named {' or '.join(wanted)}"
    return "ok", {"prep": "npm ci --no-audit --no-fund", "run": "npm test" if script == "test" else f"npm run {script}"}


def node(source: Path, stage: str, options: dict):
    """Node convention: npm ci from the lockfile, then the package.json script of the stage."""
    kind, value = node_parts(source, stage)
    if kind == "skip":
        return _skip(value)
    return {"mode": "convention", "argv": ["bash", "-ec", f"{value['prep']} && {value['run']}"]}


def resolve(ecosystem: str, stage: str, source: Path, options: dict, derived_data: str = "DerivedData"):
    if stage not in SCRIPT:
        _fail("unsupported stage")
    scripted = _script(source, stage)
    if scripted is not None:
        return scripted
    if ecosystem == "gradle":
        return gradle(source, stage, options)
    if ecosystem == "xcode":
        return xcode(source, stage, options, derived_data)
    if ecosystem == "node":
        return node(source, stage, options)
    if ecosystem == "python":
        return python(source, stage, options)
    _fail("unsupported ecosystem")


if __name__ == "__main__":
    print(json.dumps(resolve(sys.argv[1], sys.argv[2], Path(sys.argv[3]), json.loads(sys.argv[4]))))
