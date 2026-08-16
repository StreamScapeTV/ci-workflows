from __future__ import annotations

import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ci_workflows.package_primitives import (
    PackagePrimitiveError,
    build_python_packages,
    inspect_jvm_outputs,
    inspect_npm_package,
    inspect_python_packages,
    npm_pack,
    npm_publish,
    publish_python_packages,
    run_jvm_publication_tasks,
    validate_package_identity,
)
from ci_workflows.runtime_primitives import ProcessResult


class RecordingRunner:
    def __init__(self, callback: Callable[..., ProcessResult] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.callback = callback

    def __call__(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        stdin: str = "",
        timeout_seconds: float | None = None,
    ) -> ProcessResult:
        call = {
            "arguments": tuple(arguments),
            "cwd": cwd,
            "environment": dict(environment),
            "stdin": stdin,
            "timeout_seconds": timeout_seconds,
        }
        self.calls.append(call)
        if self.callback is not None:
            return self.callback(**call)
        return ProcessResult(0, "", "", False)


def executable(root: Path, name: str) -> Path:
    path = root / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path.resolve()


def write_wheel(path: Path, name: str, version: str) -> None:
    metadata = f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA", metadata)
        archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\n")


def write_sdist(path: Path, name: str, version: str) -> None:
    payload = f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n\n".encode()
    info = tarfile.TarInfo(f"{name}-{version}/PKG-INFO")
    info.size = len(payload)
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))


def write_npm(path: Path, name: str, version: str) -> None:
    payload = json.dumps({"name": name, "version": version}).encode()
    info = tarfile.TarInfo("package/package.json")
    info.size = len(payload)
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))


def write_pom(path: Path, group: str, name: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            '<project xmlns="http://maven.apache.org/POM/4.0.0">'
            "<modelVersion>4.0.0</modelVersion>"
            f"<groupId>{group}</groupId><artifactId>{name}</artifactId><version>{version}</version>"
            "</project>"
        ),
        encoding="utf-8",
    )


class IdentityTests(unittest.TestCase):
    def test_validates_representative_package_names_and_versions(self) -> None:
        self.assertEqual(validate_package_identity("python", "streamscape-sdk", "1.2.3").name, "streamscape-sdk")
        self.assertEqual(validate_package_identity("npm", "@streamscape/sdk", "1.2.3-beta.1").name, "@streamscape/sdk")
        self.assertEqual(validate_package_identity("jvm", "streamscape-sdk", "1.2.3-SNAPSHOT").version, "1.2.3-SNAPSHOT")

    def test_rejects_multiline_and_unsafe_names(self) -> None:
        for ecosystem, name in (("python", "bad/name"), ("npm", "UPPER"), ("jvm", "bad name")):
            with self.subTest(ecosystem=ecosystem):
                with self.assertRaises(PackagePrimitiveError):
                    validate_package_identity(ecosystem, name, "1.0.0")
        with self.assertRaises(PackagePrimitiveError):
            validate_package_identity("python", "good", "1.0\nsecret")


class PythonPackageTests(unittest.TestCase):
    def test_builds_wheel_and_sdist_then_inspects_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            python = executable(root, "python")
            output = root / "dist"

            def callback(**call: object) -> ProcessResult:
                args = call["arguments"]
                self.assertEqual(args[:5], (str(python), "-m", "build", "--wheel", "--sdist"))
                write_wheel(output / "streamscape_sdk-1.2.3-py3-none-any.whl", "streamscape-sdk", "1.2.3")
                write_sdist(output / "streamscape-sdk-1.2.3.tar.gz", "streamscape-sdk", "1.2.3")
                return ProcessResult(0, "built\n", "", False)

            runner = RecordingRunner(callback)
            result = build_python_packages(
                python,
                project_directory=root,
                output_directory=Path("dist"),
                expected_name="streamscape-sdk",
                expected_version="1.2.3",
                environment={},
                runner=runner,
            )
            self.assertEqual({artifact.kind for artifact in result.artifacts}, {"wheel", "sdist"})
            self.assertTrue(result.process.ok)
            self.assertEqual(len(runner.calls), 1)

    def test_inspection_rejects_mismatched_embedded_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "dist"
            output.mkdir()
            write_wheel(output / "x.whl", "other", "1.0.0")
            write_sdist(output / "x.tar.gz", "other", "1.0.0")
            with self.assertRaises(PackagePrimitiveError) as caught:
                inspect_python_packages(root, output, expected_name="wanted", expected_version="1.0.0")
            self.assertEqual(caught.exception.code, "package_identity_mismatch")

    def test_python_publish_uses_fixed_environment_credentials_and_redacts_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            python = executable(root, "python")
            artifact = root / "sdk.whl"
            write_wheel(artifact, "sdk", "1.0.0")
            token = "super-secret-token"

            def callback(**call: object) -> ProcessResult:
                args = call["arguments"]
                env = call["environment"]
                self.assertNotIn(token, " ".join(args))
                self.assertNotIn("CI_PACKAGE_TOKEN", env)
                self.assertEqual(env["TWINE_USERNAME"], "__token__")
                self.assertEqual(env["TWINE_PASSWORD"], token)
                return ProcessResult(0, f"uploaded {token}", token, False)

            result = publish_python_packages(
                python,
                [artifact],
                project_directory=root,
                registry_url="https://packages.example.test/pypi",
                package_name="sdk",
                package_version="1.0.0",
                environment={"CI_PACKAGE_TOKEN": token},
                runner=RecordingRunner(callback),
            )
            self.assertNotIn(token, result.process.stdout)
            self.assertNotIn(token, result.process.stderr)
            self.assertEqual(result.registry, "https://packages.example.test/pypi")

    def test_python_publish_rejects_selected_artifact_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            python = executable(root, "python")
            artifact = root / "other.whl"
            write_wheel(artifact, "other", "1.0.0")
            with self.assertRaises(PackagePrimitiveError) as caught:
                publish_python_packages(
                    python,
                    [artifact],
                    project_directory=root,
                    registry_url="https://packages.example.test",
                    package_name="sdk",
                    package_version="1.0.0",
                    environment={"CI_PACKAGE_TOKEN": "secret"},
                    runner=RecordingRunner(),
                )
            self.assertEqual(caught.exception.code, "package_identity_mismatch")

    def test_python_publish_rejects_registry_userinfo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            python = executable(root, "python")
            artifact = root / "sdk.whl"
            write_wheel(artifact, "sdk", "1.0.0")
            with self.assertRaises(PackagePrimitiveError) as caught:
                publish_python_packages(
                    python,
                    [artifact],
                    project_directory=root,
                    registry_url="https://user:pass@example.test",
                    package_name="sdk",
                    package_version="1.0.0",
                    environment={"CI_PACKAGE_TOKEN": "secret"},
                    runner=RecordingRunner(),
                )
            self.assertEqual(caught.exception.code, "registry_url_invalid")


class NpmPackageTests(unittest.TestCase):
    def test_npm_pack_uses_json_result_and_inspects_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            npm = executable(root, "npm")
            output = root / "packages"

            def callback(**call: object) -> ProcessResult:
                args = call["arguments"]
                self.assertEqual(args[1:3], ("pack", "--json"))
                filename = "streamscape-sdk-1.2.3.tgz"
                write_npm(output / filename, "@streamscape/sdk", "1.2.3")
                return ProcessResult(
                    0,
                    json.dumps([{"filename": filename, "name": "@streamscape/sdk", "version": "1.2.3"}]),
                    "",
                    False,
                )

            result = npm_pack(
                npm,
                project_directory=root,
                output_directory=Path("packages"),
                expected_name="@streamscape/sdk",
                expected_version="1.2.3",
                environment={},
                runner=RecordingRunner(callback),
            )
            self.assertEqual(result.artifacts[0].name, "@streamscape/sdk")
            self.assertEqual(result.artifacts[0].kind, "tarball")

    def test_npm_inspection_rejects_symlink_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory).resolve()
            outside_file = Path(outside).resolve() / "package.tgz"
            write_npm(outside_file, "sdk", "1.0.0")
            link = root / "package.tgz"
            link.symlink_to(outside_file)
            with self.assertRaises(PackagePrimitiveError):
                inspect_npm_package(root, link)

    def test_npm_publish_uses_mode_0600_temp_userconfig_and_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            npm = executable(root, "npm")
            artifact = root / "sdk.tgz"
            write_npm(artifact, "sdk", "1.0.0")
            temp = root / "temp"
            temp.mkdir()
            token = "npm-secret"
            captured: dict[str, Path] = {}

            def callback(**call: object) -> ProcessResult:
                args = call["arguments"]
                config = Path(args[args.index("--userconfig") + 1])
                captured["config"] = config
                self.assertTrue(config.is_file())
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
                self.assertIn(token, config.read_text(encoding="utf-8"))
                self.assertNotIn(token, " ".join(args))
                self.assertNotIn("CI_PACKAGE_TOKEN", call["environment"])
                return ProcessResult(0, token, token, False)

            result = npm_publish(
                npm,
                artifact,
                project_directory=root,
                temporary_root=temp,
                registry_url="https://registry.example.test/npm",
                package_name="sdk",
                package_version="1.0.0",
                environment={"CI_PACKAGE_TOKEN": token},
                runner=RecordingRunner(callback),
            )
            self.assertEqual(result.auth_paths_removed, 1)
            self.assertFalse(captured["config"].exists())
            self.assertNotIn(token, result.process.stdout)

    def test_npm_publish_rejects_tarball_identity_mismatch_before_auth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            npm = executable(root, "npm")
            artifact = root / "other.tgz"
            write_npm(artifact, "other", "1.0.0")
            temp = root / "temp"
            temp.mkdir()
            with self.assertRaises(PackagePrimitiveError) as caught:
                npm_publish(
                    npm,
                    artifact,
                    project_directory=root,
                    temporary_root=temp,
                    registry_url="https://registry.example.test",
                    package_name="sdk",
                    package_version="1.0.0",
                    environment={"CI_PACKAGE_TOKEN": "secret"},
                    runner=RecordingRunner(),
                )
            self.assertEqual(caught.exception.code, "package_identity_mismatch")
            self.assertEqual(list(temp.iterdir()), [])

    def test_npm_publish_cleans_auth_state_on_process_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            npm = executable(root, "npm")
            artifact = root / "sdk.tgz"
            write_npm(artifact, "sdk", "1.0.0")
            temp = root / "temp"
            temp.mkdir()
            captured: dict[str, Path] = {}

            def callback(**call: object) -> ProcessResult:
                args = call["arguments"]
                captured["config"] = Path(args[args.index("--userconfig") + 1])
                return ProcessResult(9, "failed", "failed", False)

            with self.assertRaises(PackagePrimitiveError) as caught:
                npm_publish(
                    npm,
                    artifact,
                    project_directory=root,
                    temporary_root=temp,
                    registry_url="https://registry.example.test",
                    package_name="sdk",
                    package_version="1.0.0",
                    environment={"CI_PACKAGE_TOKEN": "secret"},
                    runner=RecordingRunner(callback),
                )
            self.assertEqual(caught.exception.code, "command_failed")
            self.assertFalse(captured["config"].exists())


class JvmPackageTests(unittest.TestCase):
    def _runner_creating_outputs(self, output: Path, secret: str = "") -> RecordingRunner:
        def callback(**call: object) -> ProcessResult:
            output.mkdir(parents=True, exist_ok=True)
            write_pom(output / "sdk-1.0.0.pom", "tv.streamscape", "sdk", "1.0.0")
            (output / "sdk-1.0.0.aar").write_bytes(b"aar")
            (output / "sdk-1.0.0.jar").write_bytes(b"jar")
            return ProcessResult(0, f"done {secret}", secret, False)
        return RecordingRunner(callback)

    def test_gradle_publication_runs_caller_tasks_and_inspects_pom_aar_jar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            gradle = executable(root, "gradlew")
            output = root / "repo"
            secret = "gradle-secret"
            runner = self._runner_creating_outputs(output, secret)
            result = run_jvm_publication_tasks(
                "gradle",
                gradle,
                ("publish", ":sdk:assembleRelease"),
                project_directory=root,
                output_directory=Path("repo"),
                package_name="sdk",
                package_version="1.0.0",
                package_group="tv.streamscape",
                options=("--no-daemon",),
                environment={"CI_PACKAGE_PASSWORD": secret},
                runner=runner,
            )
            self.assertEqual(runner.calls[0]["arguments"][1:3], ("publish", ":sdk:assembleRelease"))
            self.assertEqual({item.kind for item in result.artifacts}, {"pom", "aar", "jar"})
            self.assertNotIn(secret, result.process.stdout)
            self.assertNotIn(secret, result.process.stderr)

    def test_maven_publication_places_nonsecret_options_before_goals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            mvn = executable(root, "mvn")
            output = root / "repo"
            runner = self._runner_creating_outputs(output)
            run_jvm_publication_tasks(
                "maven",
                mvn,
                ("deploy",),
                project_directory=root,
                output_directory=Path("repo"),
                package_name="sdk",
                package_version="1.0.0",
                package_group="tv.streamscape",
                options=("-B", "-DskipTests=true"),
                environment={},
                runner=runner,
            )
            self.assertEqual(runner.calls[0]["arguments"][1:], ("-B", "-DskipTests=true", "deploy"))

    def test_jvm_publication_rejects_secret_bearing_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            gradle = executable(root, "gradlew")
            with self.assertRaises(PackagePrimitiveError) as caught:
                run_jvm_publication_tasks(
                    "gradle",
                    gradle,
                    ("publish",),
                    project_directory=root,
                    output_directory=Path("repo"),
                    package_name="sdk",
                    package_version="1.0.0",
                    options=("-Ppassword=do-not-pass-here",),
                    environment={},
                    runner=RecordingRunner(),
                )
            self.assertEqual(caught.exception.code, "secret_option_forbidden")

    def test_jvm_inspection_requires_matching_pom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "repo"
            output.mkdir()
            write_pom(output / "other.pom", "tv.streamscape", "other", "1.0.0")
            (output / "sdk.aar").write_bytes(b"aar")
            with self.assertRaises(PackagePrimitiveError) as caught:
                inspect_jvm_outputs(
                    root,
                    output,
                    expected_name="sdk",
                    expected_version="1.0.0",
                    expected_group="tv.streamscape",
                )
            self.assertEqual(caught.exception.code, "package_identity_mismatch")


class FailureTests(unittest.TestCase):
    def test_command_failure_projects_stable_error_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            npm = executable(root, "npm")
            runner = RecordingRunner(lambda **_: ProcessResult(7, "sensitive stdout", "sensitive stderr", False))
            with self.assertRaises(PackagePrimitiveError) as caught:
                npm_pack(
                    npm,
                    project_directory=root,
                    output_directory=Path("dist"),
                    environment={},
                    runner=runner,
                )
            self.assertEqual(caught.exception.code, "command_failed")
            self.assertNotIn("sensitive", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
