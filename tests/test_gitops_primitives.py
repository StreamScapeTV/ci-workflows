from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.gitops_primitives import (
    GitOpsPrimitiveError,
    cleanup_gitops_state,
    inspect_gitops_sources,
    render_helm_chart,
    render_kustomize,
    run_read_only_source_validation,
    validate_config_directory,
    validate_json_file,
    validate_kubernetes_client_dry_run,
    validate_kubernetes_schema,
    validate_yaml_file,
)
from ci_workflows.runtime_primitives import ProcessResult


class FakeRunner:
    def __init__(self, results: list[ProcessResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str], str]] = []

    def run(self, arguments, *, cwd, environment, stdin="", timeout_seconds=None):
        self.calls.append((tuple(arguments), cwd, dict(environment), stdin))
        return self.results.pop(0)


def ok(stdout: str = "") -> ProcessResult:
    return ProcessResult(0, stdout, "", False)


MANIFEST = """apiVersion: v1
kind: ConfigMap
metadata:
  name: demo
  namespace: tests
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
"""


class GitOpsPrimitivesTests(unittest.TestCase):
    def test_yaml_json_and_directory_validation_returns_resource_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            yaml_path = root / "objects.yaml"
            json_path = root / "object.json"
            yaml_path.write_text(MANIFEST, encoding="utf-8")
            json_path.write_text(
                json.dumps(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "s"},
                    }
                ),
                encoding="utf-8",
            )
            yaml_result = validate_yaml_file(yaml_path)
            json_result = validate_json_file(json_path)
            all_result = validate_config_directory(root)
        self.assertEqual(yaml_result.document_count, 2)
        self.assertEqual([r.kind for r in yaml_result.resources], ["ConfigMap", "Deployment"])
        self.assertEqual(json_result.resources[0].name, "s")
        self.assertEqual(all_result.document_count, 3)
        self.assertEqual(len(all_result.files), 2)

    def test_invalid_yaml_and_symlink_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "bad.yaml"
            invalid.write_text("a: [\n", encoding="utf-8")
            with self.assertRaisesRegex(GitOpsPrimitiveError, "yaml_invalid"):
                validate_yaml_file(invalid)
            real = root / "real.json"
            real.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(GitOpsPrimitiveError, "path_invalid"):
                validate_json_file(link)

    def test_kustomize_build_is_local_and_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kustomization.yaml").write_text("resources: []\n", encoding="utf-8")
            runner = FakeRunner([ok(MANIFEST)])
            result = render_kustomize(
                root,
                environment={"PATH": "/usr/bin", "KUBECONFIG": "secret"},
                runner=runner,
            )
        argv, cwd, env, _ = runner.calls[0]
        self.assertEqual(argv[:2], ("kustomize", "build"))
        self.assertEqual(cwd, root.resolve())
        self.assertNotIn("KUBECONFIG", env)
        self.assertEqual([r.name for r in result.resources], ["demo", "app"])

    def test_helm_render_delegates_to_packaging_primitive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory)
            with patch(
                "ci_workflows.gitops_primitives.helm_template",
                return_value=MANIFEST,
            ) as render:
                result = render_helm_chart(
                    chart,
                    environment={"PATH": "/usr/bin", "KUBECONFIG": "secret"},
                )
        self.assertEqual(len(result.resources), 2)
        self.assertNotIn("KUBECONFIG", render.call_args.kwargs["environment"])
        self.assertEqual(render.call_args.kwargs["tool"], "helm")

    def test_kubectl_client_dry_run_forces_no_kubeconfig_and_no_validation_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner([ok("validated\n")])
            result = validate_kubernetes_client_dry_run(
                MANIFEST,
                cwd=root,
                environment={
                    "PATH": "/usr/bin",
                    "KUBECONFIG": "/secret",
                    "KUBERNETES_SERVICE_HOST": "10.0.0.1",
                },
                runner=runner,
            )
        argv, _, env, stdin = runner.calls[0]
        self.assertEqual(
            argv,
            (
                "kubectl",
                "--kubeconfig=/dev/null",
                "apply",
                "--dry-run=client",
                "--validate=false",
                "-f",
                "-",
            ),
        )
        self.assertNotIn("KUBECONFIG", env)
        self.assertNotIn("KUBERNETES_SERVICE_HOST", env)
        self.assertEqual(stdin, MANIFEST)
        self.assertEqual(result.stdout, "validated\n")

    def test_kubeconform_schema_validation_builds_bounded_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner([ok("Summary: 2 resources\n")])
            result = validate_kubernetes_schema(
                MANIFEST,
                cwd=root,
                environment={"PATH": "/usr/bin"},
                schema_locations=("default", "schemas/{{.ResourceKind}}.json"),
                ignore_missing_schemas=True,
                runner=runner,
            )
        argv = runner.calls[0][0]
        self.assertEqual(
            argv[0:4],
            ("kubeconform", "-strict", "-summary", "-ignore-missing-schemas"),
        )
        self.assertEqual(argv[-1], "-")
        self.assertEqual(len(result.resources), 2)

    def test_read_only_validation_allows_bounded_commands_and_rejects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            runner = FakeRunner([ok(MANIFEST)])
            result = run_read_only_source_validation(
                ("kustomize", "build", "source"),
                cwd=root,
                environment={"PATH": "/usr/bin"},
                runner=runner,
            )
            self.assertEqual(len(result.resources), 2)
            self.assertEqual(runner.calls[0][0], ("kustomize", "build", str(source.resolve())))
            with self.assertRaisesRegex(GitOpsPrimitiveError, "command_not_read_only"):
                run_read_only_source_validation(
                    ("helm", "upgrade", "demo", "source"),
                    cwd=root,
                    runner=FakeRunner([]),
                )
            with self.assertRaisesRegex(GitOpsPrimitiveError, "command_not_read_only"):
                run_read_only_source_validation(
                    ("kustomize", "build", "source", "--enable-alpha-plugins"),
                    cwd=root,
                    runner=FakeRunner([]),
                )
            with self.assertRaisesRegex(GitOpsPrimitiveError, "tool_unsupported"):
                run_read_only_source_validation(
                    ("bash", "-lc", "true"),
                    cwd=root,
                    runner=FakeRunner([]),
                )

    def test_inspect_gitops_sources_returns_only_flux_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sources.yaml"
            path.write_text(
                """apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: app
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: ignored
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: deploy
""",
                encoding="utf-8",
            )
            result = inspect_gitops_sources((path,))
        self.assertEqual([r.kind for r in result.resources], ["GitRepository", "Kustomization"])

    def test_cleanup_is_bounded_idempotent_and_does_not_delete_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temp = root / "render"
            temp.mkdir()
            (temp / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
            self.assertEqual(cleanup_gitops_state((temp,), root=root), 1)
            self.assertEqual(cleanup_gitops_state((temp,), root=root), 0)
            self.assertTrue(root.is_dir())

    def test_process_failures_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner([ProcessResult(1, "", "token=super-secret", False)])
            with self.assertRaisesRegex(
                GitOpsPrimitiveError,
                "process_failed: source validation exited with status 1",
            ) as raised:
                run_read_only_source_validation(
                    ("kubeconform",),
                    cwd=root,
                    environment={"PATH": "/usr/bin"},
                    stdin=MANIFEST,
                    runner=runner,
                )
        self.assertNotIn("super-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
