from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.packaging_primitives import (
    REGISTRY_TOKEN_ENV,
    REGISTRY_USERNAME_ENV,
    HelmPackage,
    ImageInspection,
    PackagingError,
    build_image,
    cleanup_packaging_state,
    helm_dependency_build,
    helm_lint,
    helm_package,
    helm_push,
    helm_template,
    inspect_image,
    push_image,
    registry_authenticate,
    registry_logout,
    tag_image,
)


def _completed(
    argv: list[str],
    stdout: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")


class PackagingPrimitivesTests(unittest.TestCase):
    def test_registry_authentication_uses_fixed_environment_secrets_via_stdin(
        self,
    ) -> None:
        environment = {
            "PATH": "/usr/bin",
            REGISTRY_USERNAME_ENV: "alice",
            REGISTRY_TOKEN_ENV: "super-secret",
            "CALLER_SELECTED_SECRET": "must-not-be-used",
        }
        with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
            run.return_value = _completed(["docker"])
            registry_authenticate(
                "registry.example.test",
                environment=environment,
                tool="docker",
            )
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            [
                "docker",
                "login",
                "registry.example.test",
                "--username",
                "alice",
                "--password-stdin",
            ],
        )
        self.assertNotIn("super-secret", argv)
        self.assertNotIn("must-not-be-used", argv)
        self.assertEqual(run.call_args.kwargs["input"], "super-secret\n")

    def test_registry_login_and_logout_support_requested_tools(self) -> None:
        environment = {
            REGISTRY_USERNAME_ENV: "u",
            REGISTRY_TOKEN_ENV: "p",
        }
        cases = {
            "buildah": (
                [
                    "buildah",
                    "login",
                    "--username",
                    "u",
                    "--password-stdin",
                    "r.example",
                ],
                ["buildah", "logout", "r.example"],
            ),
            "docker": (
                [
                    "docker",
                    "login",
                    "r.example",
                    "--username",
                    "u",
                    "--password-stdin",
                ],
                ["docker", "logout", "r.example"],
            ),
            "podman": (
                [
                    "podman",
                    "login",
                    "r.example",
                    "--username",
                    "u",
                    "--password-stdin",
                ],
                ["podman", "logout", "r.example"],
            ),
            "helm": (
                [
                    "helm",
                    "registry",
                    "login",
                    "r.example",
                    "--username",
                    "u",
                    "--password-stdin",
                ],
                ["helm", "registry", "logout", "r.example"],
            ),
        }
        for tool, (login, logout) in cases.items():
            with self.subTest(tool=tool), patch(
                "ci_workflows.packaging_primitives.subprocess.run"
            ) as run:
                run.return_value = _completed([tool])
                registry_authenticate(
                    "r.example",
                    environment=environment,
                    tool=tool,
                )
                registry_logout("r.example", environment=environment, tool=tool)
                self.assertEqual(run.call_args_list[0].args[0], login)
                self.assertEqual(run.call_args_list[1].args[0], logout)

    def test_registry_authentication_fails_closed_on_missing_fixed_secret(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            PackagingError,
            f"missing environment secret: {REGISTRY_TOKEN_ENV}",
        ):
            registry_authenticate(
                "registry.example.test",
                environment={REGISTRY_USERNAME_ENV: "alice"},
            )

    def test_image_build_tag_push_and_inspect_are_tool_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = root / "context"
            context.mkdir()
            dockerfile = context / "Containerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            inspect_payload = json.dumps(
                [
                    {
                        "Id": "sha256:local",
                        "RepoDigests": ["r.example/app@sha256:abc"],
                    }
                ]
            )
            with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
                run.side_effect = [
                    _completed(["buildah"]),
                    _completed(["buildah"]),
                    _completed(["buildah"]),
                    _completed(["buildah"], inspect_payload),
                ]
                built = build_image(
                    context,
                    dockerfile,
                    "local/app:dev",
                    build_args={"VERSION": "1.2.3", "FEATURE": "on"},
                    tool="buildah",
                )
                tagged = tag_image(
                    "local/app:dev",
                    "r.example/app:1.2.3",
                    tool="buildah",
                )
                pushed = push_image("r.example/app:1.2.3", tool="buildah")
                inspected = inspect_image("r.example/app:1.2.3", tool="buildah")
        self.assertEqual(built.reference, "local/app:dev")
        self.assertEqual(tagged.reference, "r.example/app:1.2.3")
        self.assertEqual(pushed.reference, "r.example/app:1.2.3")
        self.assertIsInstance(inspected, ImageInspection)
        self.assertEqual(inspected.image_id, "sha256:local")
        self.assertEqual(inspected.repo_digests, ("r.example/app@sha256:abc",))
        build_argv = run.call_args_list[0].args[0]
        self.assertEqual(
            build_argv[:6],
            [
                "buildah",
                "bud",
                "--file",
                str(dockerfile),
                "--tag",
                "local/app:dev",
            ],
        )
        self.assertEqual(
            build_argv[6:10],
            ["--build-arg", "FEATURE=on", "--build-arg", "VERSION=1.2.3"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["buildah", "tag", "local/app:dev", "r.example/app:1.2.3"],
        )
        self.assertEqual(
            run.call_args_list[2].args[0],
            ["buildah", "push", "r.example/app:1.2.3"],
        )
        self.assertEqual(
            run.call_args_list[3].args[0],
            ["buildah", "inspect", "r.example/app:1.2.3"],
        )

    def test_docker_and_podman_build_use_build_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            dockerfile = context / "Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            for tool in ("docker", "podman"):
                with self.subTest(tool=tool), patch(
                    "ci_workflows.packaging_primitives.subprocess.run"
                ) as run:
                    run.return_value = _completed([tool])
                    build_image(context, dockerfile, "example:test", tool=tool)
                    self.assertEqual(run.call_args.args[0][:2], [tool, "build"])

    def test_helm_validation_primitives_use_caller_chart_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart = root / "chart"
            chart.mkdir()
            (chart / "Chart.yaml").write_text(
                "apiVersion: v2\nname: demo\nversion: 1.0.0\n",
                encoding="utf-8",
            )
            values = root / "ci-values.yaml"
            values.write_text("replicas: 1\n", encoding="utf-8")
            with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
                run.side_effect = [
                    _completed(["helm"]),
                    _completed(["helm"]),
                    _completed(["helm"], "kind: ConfigMap\n"),
                ]
                helm_dependency_build(chart)
                helm_lint(chart, values=(values,))
                rendered = helm_template(
                    chart,
                    values=(values,),
                    release_name="demo",
                    namespace="tests",
                )
        self.assertEqual(rendered, "kind: ConfigMap\n")
        self.assertEqual(
            run.call_args_list[0].args[0],
            ["helm", "dependency", "build", str(chart)],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["helm", "lint", "--strict", str(chart), "--values", str(values)],
        )
        self.assertEqual(
            run.call_args_list[2].args[0],
            [
                "helm",
                "template",
                "demo",
                str(chart),
                "--values",
                str(values),
                "--namespace",
                "tests",
            ],
        )

    def test_helm_primitives_reject_arbitrary_executable_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory)
            (chart / "Chart.yaml").write_text(
                "apiVersion: v2\nname: demo\nversion: 1.0.0\n",
                encoding="utf-8",
            )
            with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
                with self.assertRaisesRegex(PackagingError, "unsupported tool: sh"):
                    helm_lint(chart, tool="sh")
                run.assert_not_called()

    def test_helm_package_and_oci_push_return_structured_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart = root / "chart"
            chart.mkdir()
            (chart / "Chart.yaml").write_text(
                "apiVersion: v2\nname: demo\nversion: 1.0.0\n",
                encoding="utf-8",
            )
            destination = root / "packages"
            archive = destination / "demo-2.0.0.tgz"

            def fake_run(
                argv: list[str],
                **kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                if argv[1] == "package":
                    destination.mkdir(parents=True, exist_ok=True)
                    archive.write_bytes(b"chart")
                    return _completed(
                        argv,
                        "Successfully packaged chart and saved it to: "
                        f"{archive}\n",
                    )
                return _completed(argv)

            with patch(
                "ci_workflows.packaging_primitives.subprocess.run",
                side_effect=fake_run,
            ) as run:
                package = helm_package(
                    chart,
                    destination,
                    version="2.0.0",
                    app_version="2.0.0",
                )
                published = helm_push(
                    package,
                    "oci://registry.example.test/team/charts",
                )
        self.assertIsInstance(package, HelmPackage)
        self.assertEqual(package.archive, archive.resolve())
        self.assertEqual(
            published.repository,
            "oci://registry.example.test/team/charts",
        )
        self.assertEqual(published.archive, archive.resolve())
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "helm",
                "package",
                str(chart),
                "--destination",
                str(destination),
                "--version",
                "2.0.0",
                "--app-version",
                "2.0.0",
            ],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "helm",
                "push",
                str(archive.resolve()),
                "oci://registry.example.test/team/charts",
            ],
        )

    def test_cleanup_removes_state_without_following_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            keep = outside / "keep"
            keep.write_text("keep", encoding="utf-8")
            state = root / "state"
            layout = state / "oci-layout"
            package = state / "packages"
            auth = state / "auth"
            for path in (layout, package, auth):
                path.mkdir(parents=True)
                (path / "file").write_text("state", encoding="utf-8")
            (auth / "outside-link").symlink_to(outside, target_is_directory=True)
            cleanup_packaging_state((layout, package, auth))
            self.assertTrue(keep.is_file())
            self.assertFalse(layout.exists())
            self.assertFalse(package.exists())
            self.assertFalse(auth.exists())

    def test_tool_failure_does_not_echo_stderr_or_secret(self) -> None:
        environment = {
            REGISTRY_USERNAME_ENV: "u",
            REGISTRY_TOKEN_ENV: "secret-value",
        }
        with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                ["docker"],
                1,
                stdout="",
                stderr="token=secret-value",
            )
            with self.assertRaisesRegex(
                PackagingError,
                "^tool execution failed: docker$",
            ):
                registry_authenticate(
                    "r.example",
                    environment=environment,
                )


if __name__ == "__main__":
    unittest.main()
