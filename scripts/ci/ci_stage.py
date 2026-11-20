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


def _script(source: Path, stage: str):
    relative = SCRIPT[stage]
    mode = _tracked(source, relative)
    if mode is None:
        return None
    path = source / relative
    if mode != "100755" or path.is_symlink() or not path.is_file():
        _fail(f"{relative} must be a tracked regular executable file (mode 100755)")
    return {"mode": "script", "argv": ["./" + relative]}


def gradle(source: Path, stage: str, options: dict):
    if _tracked(source, "gradlew") != "100755" or (source / "gradlew").is_symlink():
        _fail("Gradle convention: commit an executable gradlew (mode 100755) and gradle/wrapper/gradle-wrapper.properties")
    tasks = {"lint": ["check", "-x", "test"], "build": ["assemble"], "test": ["test"]}[stage]
    return {"mode": "convention", "argv": ["./gradlew", "--no-daemon", *tasks]}


def xcode(source: Path, stage: str, options: dict, derived_data: str = "DerivedData"):
    names = _tracked_names(source)
    if stage == "lint":
        if ".swiftlint.yml" in names:
            return {"mode": "convention", "argv": ["swiftlint", "lint", "--strict"]}
        _fail("Xcode convention: lint needs a tracked .swiftlint.yml (or a transition .ci/lint.sh)")
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
        _fail("Xcode convention: simulator tests are not standardised yet; run macos tests or keep .ci/test.sh (transition)")
    flag = "-workspace" if workspaces else "-project"
    argv = ["xcodebuild", flag, container[0], "-scheme", scheme, "-configuration", configuration,
            "-destination", destinations[platform], "-derivedDataPath", derived_data,
            "build" if stage == "build" else "test",
            "CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO"]
    return {"mode": "convention", "argv": argv}


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
    _fail("unsupported ecosystem")


if __name__ == "__main__":
    print(json.dumps(resolve(sys.argv[1], sys.argv[2], Path(sys.argv[3]), json.loads(sys.argv[4]))))
