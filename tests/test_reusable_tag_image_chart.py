from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-tag-image-chart.yml"
SELF_CHECK = ROOT / ".github" / "workflows" / "self-check.yml"
README = ROOT / "README.md"


class ReusableTagImageChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.self_check = SELF_CHECK.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_public_workflow_is_workflow_call_only(self) -> None:
        header = self.text.split("\npermissions:", 1)[0]
        self.assertRegex(header, re.compile(r"(?m)^on:\n  workflow_call:\n"))
        for forbidden in (
            "\n  push:",
            "\n  pull_request:",
            "\n  workflow_dispatch:",
            "\n  schedule:",
            "\n  issue_comment:",
            "\n  workflow_run:",
        ):
            self.assertNotIn(forbidden, header)

    def test_public_api_is_bounded_and_explicit(self) -> None:
        for input_name in (
            "image_name",
            "chart_name",
            "chart_path",
            "dockerfile_path",
            "build_context",
        ):
            self.assertRegex(
                self.text,
                re.compile(rf"(?m)^      {re.escape(input_name)}:\n"),
            )
        for secret_name in ("registry_username", "registry_token"):
            self.assertRegex(
                self.text,
                re.compile(rf"(?m)^      {secret_name}:\n"),
            )
        for forbidden_input in (
            "registry_host",
            "runner_label",
            "container_engine",
            "cluster",
            "kubeconfig",
            "command",
            "script",
            "secret_name",
        ):
            self.assertNotRegex(
                self.text,
                re.compile(rf"(?m)^      {forbidden_input}:\n"),
            )
        self.assertNotIn("secrets: inherit", self.text)

    def test_exact_tag_and_source_are_required(self) -> None:
        self.assertIn(
            "github.event_name == 'push' && github.ref_type == 'tag'",
            self.text,
        )
        self.assertIn("startsWith(github.ref, 'refs/tags/')", self.text)
        self.assertIn("ref: ${{ github.sha }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "${GITHUB_REF}" = "refs/tags/${GITHUB_REF_NAME}"', self.text)
        self.assertIn('test "${GITHUB_SHA}" = "$(git rev-parse HEAD)"', self.text)
        self.assertIn('VERSION="${GITHUB_REF_NAME}"', self.text)

    def test_version_and_input_validator_accepts_only_bounded_values(self) -> None:
        marker = "          python3 - <<'PY'\n"
        start = self.text.index(
            marker,
            self.text.index("Validate tag and bounded product inputs"),
        )
        script = textwrap.dedent(
            self.text[start + len(marker):].split("\n          PY", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "chart").mkdir()
            (root / "chart" / "Chart.yaml").write_text(
                "apiVersion: v2\nname: backend\nversion: 0.0.0\n",
                encoding="utf-8",
            )
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            good_env = {
                **os.environ,
                "VERSION": "1.2.3-rc.1",
                "IMAGE_NAME": "iptv-backend",
                "CHART_NAME": "iptv-backend",
                "CHART_PATH": "chart",
                "DOCKERFILE_PATH": "Dockerfile",
                "BUILD_CONTEXT": ".",
            }
            result = subprocess.run(
                [sys.executable, "-S", "-c", script],
                cwd=root,
                env=good_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            for name, value in (
                ("VERSION", "v1.2.3"),
                ("VERSION", "1.2.3+build"),
                ("VERSION", "latest"),
                ("IMAGE_NAME", "../escape"),
                ("CHART_NAME", "UPPER"),
                ("CHART_PATH", "../chart"),
                ("DOCKERFILE_PATH", "/Dockerfile"),
                ("BUILD_CONTEXT", ".."),
            ):
                with self.subTest(name=name, value=value):
                    env = dict(good_env)
                    env[name] = value
                    result = subprocess.run(
                        [sys.executable, "-S", "-c", script],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)

    def test_registry_runner_and_engine_are_central_fixed_contracts(self) -> None:
        self.assertIn("REGISTRY: git.faruqi.dev", self.text)
        self.assertIn("REGISTRY_NAMESPACE: mimranfaruqi", self.text)
        self.assertIn("CHART_NAMESPACE: mimranfaruqi/helm-charts", self.text)
        self.assertIn("    runs-on: buildah-high", self.text)
        self.assertNotIn("self-hosted", self.text)
        for marker in (
            "! command -v docker",
            "! command -v dockerd",
            "test ! -e /var/run/docker.sock",
            "buildah --version",
            "skopeo --version",
            "podman --version",
            "--storage-driver vfs",
        ):
            self.assertIn(marker, self.text)

    def test_image_is_exact_tag_multi_platform_and_independently_read_back(self) -> None:
        self.assertIn("for architecture in amd64 arm64; do", self.text)
        self.assertIn('--platform "linux/${architecture}"', self.text)
        self.assertIn('--timestamp "${source_epoch}"', self.text)
        self.assertIn("org.opencontainers.image.source", self.text)
        self.assertIn("org.opencontainers.image.revision", self.text)
        self.assertIn("org.opencontainers.image.version", self.text)
        self.assertIn('"oci:${OCI_LAYOUT}:${VERSION}"', self.text)
        self.assertIn('"docker://${IMAGE_REFERENCE}"', self.text)
        self.assertIn("skopeo copy --all", self.text)
        self.assertIn("remote image platforms", self.text)
        self.assertIn("--override-arch", self.text)
        self.assertIn('test "${remote_digest}" = "${local_digest}"', self.text)

    def test_chart_builds_locked_dependencies_before_lint_and_package(self) -> None:
        prepare = self.text.index("Prepare locked Helm chart dependencies")
        dependency_build = self.text.index(
            '"${HELM_BIN}" dependency build "${CHART_SOURCE}"',
            prepare,
        )
        chart_step = self.text.index(
            "Package publish and verify exact-tag Helm chart",
            dependency_build,
        )
        lint = self.text.index('"${HELM_BIN}" lint "${CHART_SOURCE}"', chart_step)
        package = self.text.index('"${HELM_BIN}" package "${CHART_SOURCE}"', chart_step)
        self.assertLess(prepare, dependency_build)
        self.assertLess(dependency_build, lint)
        self.assertLess(lint, package)

        for marker in (
            "HELM_REPOSITORY_CONFIG",
            "HELM_REPOSITORY_CACHE",
            "Chart.lock is required when Chart.yaml declares dependencies",
            "Unsupported Helm dependency repository scheme",
            "dependency build",
            '--skip-refresh',
            'test "${lock_sha_after}" = "${lock_sha_before}"',
            "CHART_DEPENDENCY_COUNT",
            "local_dependency_entries",
            "remote_dependency_entries",
        ):
            self.assertIn(marker, self.text)

    def test_dependency_validator_accepts_locked_https_and_oci_only(self) -> None:
        marker = "          python3 - <<'PY'\n"
        start = self.text.index(
            marker,
            self.text.index("Prepare locked Helm chart dependencies"),
        )
        script = textwrap.dedent(
            self.text[start + len(marker):].split("\n          PY", 1)[0]
        )

        def run_case(
            directory: Path,
            dependency_output: str,
            *,
            with_lock: bool,
        ) -> subprocess.CompletedProcess[str]:
            chart = directory / "chart"
            chart.mkdir(parents=True, exist_ok=True)
            if with_lock:
                (chart / "Chart.lock").write_text(
                    "dependencies: []\ndigest: sha256:test\ngenerated: now\n",
                    encoding="utf-8",
                )
            dependency_file = directory / "dependencies.txt"
            repository_file = directory / "repositories.txt"
            count_file = directory / "count.txt"
            dependency_file.write_text(dependency_output, encoding="utf-8")
            env = {
                **os.environ,
                "CHART_SOURCE": str(chart),
                "DEPENDENCY_LIST": str(dependency_file),
                "DEPENDENCY_REPOSITORIES": str(repository_file),
                "DEPENDENCY_COUNT_FILE": str(count_file),
            }
            return subprocess.run(
                [sys.executable, "-S", "-c", script],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        header = "NAME VERSION REPOSITORY STATUS\n"
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            result = run_case(
                directory / "https",
                header + "valkey 0.11.0 https://valkey.io/valkey-helm/ missing\n",
                with_lock=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / "https" / "repositories.txt").read_text(),
                "https://valkey.io/valkey-helm/\n",
            )
            self.assertEqual(
                (directory / "https" / "count.txt").read_text(),
                "1\n",
            )

            result = run_case(
                directory / "oci",
                header + "shared 1.2.3 oci://registry.example/charts missing\n",
                with_lock=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / "oci" / "repositories.txt").read_text(),
                "",
            )

            result = run_case(
                directory / "no-lock",
                header + "valkey 0.11.0 https://valkey.io/valkey-helm/ missing\n",
                with_lock=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Chart.lock is required", result.stderr)

            for repository in (
                "http://valkey.example/charts",
                "file://../escape",
                "git+https://example.invalid/charts",
                "https://user:password@example.invalid/charts",
                "https://example.invalid/charts?token=secret",
            ):
                with self.subTest(repository=repository):
                    result = run_case(
                        directory / ("bad-" + str(abs(hash(repository)))),
                        header + f"bad 1.2.3 {repository} missing\n",
                        with_lock=True,
                    )
                    self.assertNotEqual(result.returncode, 0)

            result = run_case(
                directory / "none",
                header,
                with_lock=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / "none" / "count.txt").read_text(),
                "0\n",
            )

    def test_chart_uses_tag_version_and_remote_package_readback(self) -> None:
        self.assertIn('--version "${VERSION}"', self.text)
        self.assertIn('--app-version "${VERSION}"', self.text)
        self.assertIn('--set-string image.tag="${VERSION}"', self.text)
        self.assertIn('"${HELM_BIN}" push "${package}"', self.text)
        self.assertIn('"${HELM_BIN}" pull "${CHART_REFERENCE}"', self.text)
        self.assertIn('test "$(sha256sum "${remote_package}"', self.text)
        self.assertIn('grep -Fqx "version: ${VERSION}"', self.text)
        self.assertIn("Rendered chart contains forbidden latest identity", self.text)

    def test_replay_is_idempotent_and_conflicts_fail_closed(self) -> None:
        self.assertIn("remote_exists=false", self.text)
        self.assertIn('test "${remote_digest}" = "${local_digest}"', self.text)
        self.assertIn('test "$(sha256sum "${remote_package}"', self.text)
        self.assertIn("replayed=%s", self.text)

    def test_no_latest_manual_release_github_release_or_deployment(self) -> None:
        lower = self.text.lower()
        for forbidden in (
            "workflow_dispatch:",
            "refs/heads/main",
            ":latest",
            "github release",
            "/releases",
            "repository_dispatch",
            "flux",
            "kubectl",
            "kubeconfig",
            "sops",
            "helm upgrade",
            "helm install",
            "rollout restart",
            "actions/upload-artifact",
            "buildx",
            "dind",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lower)
        self.assertIn("Deployment/rollout: not performed", self.text)

    def test_zero_artifacts_auth_isolated_and_cleanup_fail_closed(self) -> None:
        self.assertIn("actions: read", self.text)
        self.assertIn("contents: read", self.text)
        self.assertIn("chmod 0600", self.text)
        self.assertIn("zero artifacts", self.text)
        self.assertIn("if: always()", self.text)
        self.assertIn("cleanup_failed=0", self.text)
        self.assertIn('exit "${cleanup_failed}"', self.text)
        self.assertNotIn("exit 0", self.text)
        self.assertIn("containers -q", self.text)
        self.assertIn("images -q", self.text)
        self.assertIn("registry logout", self.text)
        self.assertIn('"${STATE_ROOT:-${RUNNER_TEMP}/central-tag-release}"', self.text)

    def test_self_check_and_documented_caller_are_thin(self) -> None:
        self.assertIn(
            '"${VERIFIED_PYTHON}" -m unittest discover -s tests -p \'test_*.py\' -v',
            self.self_check,
        )
        self.assertNotIn(
            '"${VERIFIED_PYTHON}" -m unittest -v tests/test_reusable_tag_image_chart.py',
            self.self_check,
        )
        self.assertIn("Confirm zero Actions artifacts", self.self_check)
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/"
            "reusable-tag-image-chart.yml@",
            self.readme,
        )
        self.assertIn("tags:", self.readme)
        self.assertIn("registry_username:", self.readme)
        self.assertIn("registry_token:", self.readme)
        self.assertIn("git tag 1.2.3 <commit>", self.readme)
        self.assertNotIn("secrets: inherit\n", self.readme)


if __name__ == "__main__":
    unittest.main()
