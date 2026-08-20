from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Mapping
from unittest import mock

from ci_workflows.ciw_packages import (
    PackagePublishError,
    _bootstrap_python_tools,
    _publication_environment,
    execute_package_publish,
    plan_outputs,
)
from ci_workflows.package_primitives import (
    PackageArtifact,
    PackageBuildResult,
    PackagePrimitiveError,
    PublicationResult,
)
from ci_workflows.runtime_primitives import ProcessResult


SHA = "a" * 40


def success_process() -> ProcessResult:
    return ProcessResult(0, "", "", False)


def no_python_tool_bootstrap(
    _python: Path,
    _root: Path,
    environment: Mapping[str, str],
) -> Mapping[str, str]:
    return dict(environment)


class PackageAdapterTests(unittest.TestCase):
    def _layout(self, directory: str) -> tuple[Path, Path]:
        root = Path(directory).resolve()
        source = root / "source"
        source.mkdir()
        (source / "README.md").write_text("fixture\n", encoding="utf-8")
        state = root / "state"
        (state / "generated").mkdir(parents=True)
        (state / "tmp").mkdir()
        return root, state

    def _environment(self, ecosystem: str, plan: dict[str, object]) -> dict[str, str]:
        return {
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_ECOSYSTEM": ecosystem,
            "INPUT_WORKING_DIRECTORY": ".",
            "INPUT_PACKAGE_NAME": "sdk" if ecosystem != "npm" else "@streamscape/sdk",
            "INPUT_PACKAGE_VERSION": "1.2.3",
            "INPUT_PACKAGE_GROUP": "tv.streamscape" if ecosystem == "jvm" else "",
            "INPUT_PUBLICATION_PLAN_JSON": json.dumps(plan),
            "CI_PACKAGE_TOKEN": "package-token",
            "CI_PACKAGE_USERNAME": "publisher",
            "HOME": "/runner-home",
            "TMPDIR": "/runner-tmp",
            "npm_config_cache": "/runner-npm-cache",
            "MAVEN_OPTS": "-Dambient=true",
        }

    def _assert_isolated_environment(self, environment: Mapping[str, str], state: Path) -> None:
        generated = (state / "generated").resolve()
        for name in (
            "HOME",
            "TMPDIR",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "npm_config_cache",
            "npm_config_prefix",
            "npm_config_userconfig",
            "npm_config_globalconfig",
            "MAVEN_USER_HOME",
        ):
            value = Path(environment[name]).resolve()
            self.assertTrue(value.is_relative_to(generated), f"{name} escaped run-owned state")
        self.assertNotEqual(environment["HOME"], "/runner-home")
        self.assertNotEqual(environment["TMPDIR"], "/runner-tmp")
        self.assertNotEqual(environment["npm_config_cache"], "/runner-npm-cache")
        self.assertNotEqual(environment["MAVEN_OPTS"], "-Dambient=true")
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")

    def test_plan_selects_small_python_and_mobile_npm_jvm_capacity(self) -> None:
        python = plan_outputs(self._environment("python", {"registry_profile": "pypi"}))
        npm = plan_outputs(self._environment("npm", {"registry_profile": "npmjs"}))
        jvm = plan_outputs(self._environment("jvm", {"maven_actions": ["deploy"]}))
        self.assertEqual(python["runner_profile"], "general-small")
        self.assertEqual(python["runs_on_json"], '["linux","amd64","general","small"]')
        self.assertEqual(npm["runner_profile"], "mobile")
        self.assertEqual(jvm["runs_on_json"], '["linux","amd64","mobile"]')

    def test_python_publication_credentials_support_token_or_username_password(self) -> None:
        token = _publication_environment(
            {"HOME": "/isolated"},
            {"CI_PACKAGE_TOKEN": "token-only"},
            "python",
        )
        self.assertEqual(token["CI_PACKAGE_TOKEN"], "token-only")
        self.assertNotIn("CI_PACKAGE_PASSWORD", token)

        pair = _publication_environment(
            {"HOME": "/isolated"},
            {"CI_PACKAGE_USERNAME": "publisher", "CI_PACKAGE_TOKEN": "password-value"},
            "python",
        )
        self.assertEqual(pair["CI_PACKAGE_USERNAME"], "publisher")
        self.assertEqual(pair["CI_PACKAGE_PASSWORD"], "password-value")
        self.assertNotIn("CI_PACKAGE_TOKEN", pair)

    def test_python_tool_bootstrap_is_fixed_cacheless_and_run_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            captured: dict[str, object] = {}

            def run_process(arguments: object, **kwargs: object) -> ProcessResult:
                argv = tuple(arguments)
                captured["argv"] = argv
                captured["cwd"] = kwargs["cwd"]
                captured["environment"] = kwargs["environment"]
                target = Path(argv[argv.index("--target") + 1])
                target.mkdir()
                return success_process()

            with mock.patch("ci_workflows.ciw_packages.run_process", side_effect=run_process):
                environment = _bootstrap_python_tools(
                    Path("/usr/local/bin/python3"),
                    root,
                    {"PYTHONPATH": "/central/src", "PATH": "/usr/local/bin"},
                )

            argv = captured["argv"]
            self.assertIn("--isolated", argv)
            self.assertIn("--no-cache-dir", argv)
            self.assertIn("build==1.3.0", argv)
            self.assertIn("twine==6.2.0", argv)
            self.assertEqual(captured["cwd"], root)
            self.assertEqual(captured["environment"]["PIP_NO_CACHE_DIR"], "1")
            self.assertEqual(
                environment["PYTHONPATH"],
                f"{root / 'python-tools'}:/central/src",
            )

    def test_python_builds_validated_artifacts_then_publishes_and_cleans_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, state = self._layout(directory)
            calls: list[str] = []

            def build(_python: Path, **kwargs: object) -> PackageBuildResult:
                environment = kwargs["environment"]
                self.assertNotIn("CI_PACKAGE_TOKEN", environment)
                self.assertNotIn("CI_PACKAGE_USERNAME", environment)
                self._assert_isolated_environment(environment, state)
                project = kwargs["project_directory"]
                output = project / kwargs["output_directory"]
                output.mkdir()
                (output / "sdk.whl").write_bytes(b"wheel")
                (output / "sdk.tar.gz").write_bytes(b"sdist")
                calls.append("build")
                return PackageBuildResult(
                    "python.build",
                    success_process(),
                    (
                        PackageArtifact("python", "wheel", "sdk.whl", 5, "sdk", "1.2.3"),
                        PackageArtifact("python", "sdist", "sdk.tar.gz", 5, "sdk", "1.2.3"),
                    ),
                )

            def publish(_python: Path, artifacts: object, **kwargs: object) -> PublicationResult:
                calls.append("publish")
                paths = tuple(artifacts)
                self.assertEqual(len(paths), 2)
                self.assertTrue(all(path.is_file() for path in paths))
                self.assertEqual(kwargs["registry_url"], "https://upload.pypi.org/legacy/")
                publication = kwargs["environment"]
                self.assertNotIn("CI_PACKAGE_TOKEN", publication)
                self.assertEqual(publication["CI_PACKAGE_USERNAME"], "publisher")
                self.assertEqual(publication["CI_PACKAGE_PASSWORD"], "package-token")
                self._assert_isolated_environment(publication, state)
                return PublicationResult(
                    "python",
                    str(kwargs["registry_url"]),
                    "sdk",
                    "1.2.3",
                    success_process(),
                    0,
                )

            result = execute_package_publish(
                workspace=workspace,
                state_root=state,
                environment=self._environment("python", {"registry_profile": "pypi"}),
                python_builder=build,
                python_publisher=publish,
                python_tool_bootstrap=no_python_tool_bootstrap,
            )
            self.assertEqual(calls, ["build", "publish"])
            self.assertEqual(result["package_name"], "sdk")
            self.assertEqual(result["package_version"], "1.2.3")
            self.assertEqual(list((state / "generated").iterdir()), [])
            self.assertEqual((workspace / "source" / "README.md").read_text(), "fixture\n")

    def test_python_can_use_checked_in_private_registry_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, state = self._layout(directory)
            (workspace / "source" / "registry.json").write_text(
                json.dumps({"registry_url": "https://packages.example.test/pypi/"}),
                encoding="utf-8",
            )

            def build(_python: Path, **kwargs: object) -> PackageBuildResult:
                self.assertNotIn("CI_PACKAGE_TOKEN", kwargs["environment"])
                project = kwargs["project_directory"]
                output = project / kwargs["output_directory"]
                output.mkdir()
                (output / "sdk.whl").write_bytes(b"wheel")
                (output / "sdk.tar.gz").write_bytes(b"sdist")
                return PackageBuildResult(
                    "python.build",
                    success_process(),
                    (
                        PackageArtifact("python", "wheel", "sdk.whl", 5, "sdk", "1.2.3"),
                        PackageArtifact("python", "sdist", "sdk.tar.gz", 5, "sdk", "1.2.3"),
                    ),
                )

            def publish(_python: Path, _artifacts: object, **kwargs: object) -> PublicationResult:
                self.assertEqual(
                    kwargs["registry_url"],
                    "https://packages.example.test/pypi/",
                )
                publication = kwargs["environment"]
                self.assertNotIn("CI_PACKAGE_TOKEN", publication)
                self.assertEqual(publication["CI_PACKAGE_USERNAME"], "publisher")
                self.assertEqual(publication["CI_PACKAGE_PASSWORD"], "package-token")
                return PublicationResult(
                    "python",
                    str(kwargs["registry_url"]),
                    "sdk",
                    "1.2.3",
                    success_process(),
                    0,
                )

            result = execute_package_publish(
                workspace=workspace,
                state_root=state,
                environment=self._environment(
                    "python",
                    {"registry_config_path": "registry.json"},
                ),
                python_builder=build,
                python_publisher=publish,
                python_tool_bootstrap=no_python_tool_bootstrap,
            )
            self.assertEqual(result["package_version"], "1.2.3")
            self.assertEqual(list((state / "generated").iterdir()), [])

    def test_npm_pack_publishes_tarball_and_cleans_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, state = self._layout(directory)
            env = self._environment("npm", {"registry_profile": "github-packages"})

            def pack(_npm: Path, **kwargs: object) -> PackageBuildResult:
                environment = kwargs["environment"]
                self.assertNotIn("CI_PACKAGE_TOKEN", environment)
                self.assertNotIn("CI_PACKAGE_USERNAME", environment)
                self._assert_isolated_environment(environment, state)
                self.assertTrue(Path(environment["npm_config_userconfig"]).is_file())
                self.assertTrue(Path(environment["npm_config_globalconfig"]).is_file())
                project = kwargs["project_directory"]
                output = project / kwargs["output_directory"]
                output.mkdir()
                artifact = output / "streamscape-sdk.tgz"
                artifact.write_bytes(b"package")
                return PackageBuildResult(
                    "npm.pack",
                    success_process(),
                    (
                        PackageArtifact(
                            "npm",
                            "tarball",
                            str(artifact.relative_to(project)),
                            7,
                            "@streamscape/sdk",
                            "1.2.3",
                        ),
                    ),
                )

            def publish(_npm: Path, artifact: Path, **kwargs: object) -> PublicationResult:
                self.assertTrue(artifact.is_file())
                self.assertEqual(kwargs["registry_url"], "https://npm.pkg.github.com")
                self.assertEqual(kwargs["temporary_root"], state / "tmp")
                self.assertEqual(kwargs["environment"]["CI_PACKAGE_TOKEN"], "package-token")
                self._assert_isolated_environment(kwargs["environment"], state)
                return PublicationResult(
                    "npm",
                    str(kwargs["registry_url"]),
                    "@streamscape/sdk",
                    "1.2.3",
                    success_process(),
                    1,
                )

            with mock.patch(
                "ci_workflows.ciw_packages._tool_from_path",
                return_value=Path("/usr/bin/npm"),
            ):
                result = execute_package_publish(
                    workspace=workspace,
                    state_root=state,
                    environment=env,
                    npm_packer=pack,
                    npm_publisher=publish,
                )
            self.assertEqual(result["ecosystem"], "npm")
            self.assertEqual(list((state / "generated").iterdir()), [])

    def test_jvm_runs_bounded_maven_publication_and_reports_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, state = self._layout(directory)
            wrapper = workspace / "source" / "mvnw"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o700)
            captured: dict[str, object] = {}

            def publish(
                tool: str,
                executable: Path,
                actions: object,
                **kwargs: object,
            ) -> PackageBuildResult:
                captured.update(
                    tool=tool,
                    executable=executable,
                    actions=tuple(actions),
                    options=kwargs["options"],
                )
                self.assertEqual(kwargs["environment"]["CI_PACKAGE_TOKEN"], "package-token")
                self._assert_isolated_environment(kwargs["environment"], state)
                output = kwargs["project_directory"] / kwargs["output_directory"]
                output.mkdir()
                return PackageBuildResult(
                    "jvm.maven.publish",
                    success_process(),
                    (
                        PackageArtifact(
                            "jvm",
                            "pom",
                            "repo/sdk-1.2.3.pom",
                            4,
                            "sdk",
                            "1.2.3",
                            "tv.streamscape",
                        ),
                    ),
                )

            result = execute_package_publish(
                workspace=workspace,
                state_root=state,
                environment=self._environment(
                    "jvm",
                    {
                        "maven_actions": ["deploy"],
                        "maven_options": ["-B", "-DskipTests=true"],
                        "maven_executable": "mvnw",
                    },
                ),
                jvm_publisher=publish,
            )
            self.assertEqual(captured["tool"], "maven")
            self.assertEqual(captured["actions"], ("deploy",))
            options = captured["options"]
            self.assertEqual(options[1:], ("-B", "-DskipTests=true"))
            self.assertTrue(options[0].startswith("-Dmaven.repo.local="))
            repository = Path(options[0].split("=", 1)[1]).resolve()
            self.assertTrue(repository.is_relative_to((state / "generated").resolve()))
            self.assertTrue(str(captured["executable"]).endswith("/project/mvnw"))
            self.assertEqual(result["package_name"], "sdk")
            self.assertEqual(list((state / "generated").iterdir()), [])

    def test_publication_failure_still_removes_run_owned_source_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, state = self._layout(directory)

            def build(_python: Path, **kwargs: object) -> PackageBuildResult:
                self.assertNotIn("CI_PACKAGE_TOKEN", kwargs["environment"])
                project = kwargs["project_directory"]
                output = project / kwargs["output_directory"]
                output.mkdir()
                artifact = output / "sdk.whl"
                artifact.write_bytes(b"wheel")
                return PackageBuildResult(
                    "python.build",
                    success_process(),
                    (PackageArtifact("python", "wheel", "sdk.whl", 5, "sdk", "1.2.3"),),
                )

            def fail_publish(*_args: object, **kwargs: object) -> PublicationResult:
                publication = kwargs["environment"]
                self.assertNotIn("CI_PACKAGE_TOKEN", publication)
                self.assertEqual(publication["CI_PACKAGE_USERNAME"], "publisher")
                self.assertEqual(publication["CI_PACKAGE_PASSWORD"], "package-token")
                raise PackagePrimitiveError("command_failed", "python.publish", returncode=9)

            with self.assertRaises(PackagePrimitiveError) as caught:
                execute_package_publish(
                    workspace=workspace,
                    state_root=state,
                    environment=self._environment("python", {"registry_profile": "pypi"}),
                    python_builder=build,
                    python_publisher=fail_publish,
                    python_tool_bootstrap=no_python_tool_bootstrap,
                )
            self.assertEqual(caught.exception.code, "command_failed")
            self.assertEqual(list((state / "generated").iterdir()), [])

    def test_source_symlink_escape_is_rejected_before_package_execution_and_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, state = self._layout(directory)
            outside = workspace / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            (workspace / "source" / "escape.txt").symlink_to(outside)
            build = mock.Mock()

            with self.assertRaises(PackagePublishError) as caught:
                execute_package_publish(
                    workspace=workspace,
                    state_root=state,
                    environment=self._environment("python", {"registry_profile": "pypi"}),
                    python_builder=build,
                    python_tool_bootstrap=no_python_tool_bootstrap,
                )
            self.assertEqual(caught.exception.code, "package_source_symlink_invalid")
            build.assert_not_called()
            self.assertEqual(list((state / "generated").iterdir()), [])

    def test_plan_rejects_raw_registry_url_ambiguous_destination_and_maven_registry_option(self) -> None:
        with self.assertRaises(PackagePublishError):
            plan_outputs(
                self._environment(
                    "python",
                    {
                        "registry_profile": "pypi",
                        "registry_url": "https://packages.example.test",
                    },
                )
            )
        with self.assertRaises(PackagePublishError):
            plan_outputs(
                self._environment(
                    "npm",
                    {
                        "registry_profile": "npmjs",
                        "registry_config_path": "registry.json",
                    },
                )
            )
        with self.assertRaises(PackagePublishError):
            plan_outputs(
                self._environment(
                    "jvm",
                    {
                        "maven_actions": ["deploy"],
                        "maven_options": ["-Drepo=https://packages.example.test"],
                    },
                )
            )

    def test_plan_rejects_non_jvm_group_unknown_ecosystem_and_invalid_jvm_group(self) -> None:
        env = self._environment("python", {"registry_profile": "pypi"})
        env["INPUT_PACKAGE_GROUP"] = "tv.streamscape"
        with self.assertRaises(PackagePublishError):
            plan_outputs(env)

        env = self._environment("python", {"registry_profile": "pypi"})
        env["INPUT_ECOSYSTEM"] = "gradle"
        with self.assertRaises(PackagePublishError):
            plan_outputs(env)

        env = self._environment("jvm", {"maven_actions": ["deploy"]})
        env["INPUT_PACKAGE_GROUP"] = "bad group"
        with self.assertRaises(PackagePublishError):
            plan_outputs(env)


if __name__ == "__main__":
    unittest.main()
