from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReleaseObservabilityTests(unittest.TestCase):
    def test_exact_tag_source_sha_and_readable_private_log_are_terminal_gates(self) -> None:
        path = ROOT / ".github/workflows/public-native-image-chart.yml"
        workflow = yaml.safe_load(path.read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps if step.get("name")}

        authority = by_name["Verify exact source ref authority"]
        publication = by_name["Resolve product-owned publication version"]
        chart_prepare = by_name["Prepare isolated locked Helm dependencies"]
        authenticate = by_name["Authenticate and require unused immutable version"]
        observe = by_name["Record exact product source SHA"]
        revalidate = by_name["Revalidate exact source ref before publication"]
        publish = by_name["Publish immutable image and chart"]
        readback = by_name["Authenticated private registry read-back"]
        cleanup = by_name["Clean publication state"]
        scrub = by_name["Scrub private release log"]
        drive = by_name["Upload private release log to Google Drive"]
        finish = by_name["Finish Agent State run"]

        self.assertIn('source_sha="$(git -C source rev-parse HEAD)"', authority["run"])
        self.assertIn('ref_sha="$(git -C source rev-parse "${SOURCE_REF}^{commit}")"', authority["run"])
        self.assertIn('test "${source_sha}" = "${ref_sha}"', authority["run"])
        self.assertEqual(authority["env"]["SOURCE_REF"], "${{ steps.requested_ref.outputs.full_ref }}")
        self.assertNotIn("MAJOR.MINOR.PATCH", authority["run"])
        self.assertNotIn("[0-9]*)\\.", authority["run"])
        self.assertNotIn("version=%s", authority["run"])

        self.assertEqual(publication["env"]["CHART_PATH"], "${{ inputs.chart_path }}")
        self.assertIn('version_path="source/VERSION"', publication["run"])
        self.assertIn("git -C source ls-files --error-unmatch -- VERSION", publication["run"])
        self.assertIn("Repository-root VERSION must be release SemVer without build metadata", publication["run"])
        self.assertIn('helm show chart "source/${CHART_PATH}"', publication["run"])
        self.assertIn('/^version:[[:space:]]+/', publication["run"])
        self.assertNotIn('$1 == "version:"', publication["run"])
        self.assertIn("found != 1", publication["run"])
        self.assertIn("publication_version=%s", publication["run"])
        self.assertIn("publication_source=%s", publication["run"])
        self.assertNotIn("RELEASE_TAG", publication["run"])
        self.assertNotIn("inputs.ref", publication["run"])
        self.assertNotIn("version_file", path.read_text())
        dispatch_text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertNotIn("version_file", dispatch_text)
        self.assertEqual(observe["uses"], "StreamScapeTV/ci-workflows/actions/agent-state@main")
        self.assertEqual(observe["with"]["phase"], "observe-source")
        self.assertEqual(observe["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        self.assertEqual(observe["with"]["observed_source_sha"], "${{ steps.authority.outputs.source_sha }}")
        self.assertNotIn("github.sha", observe["with"]["observed_source_sha"])

        self.assertEqual(chart_prepare["env"]["CHART_PATH"], "${{ inputs.chart_path }}")
        self.assertEqual(chart_prepare["env"]["VERSION"], "${{ steps.publication.outputs.publication_version }}")
        self.assertIn('prepared_chart="${RUNNER_TEMP}/private-native-image-chart/chart"', chart_prepare["run"])
        self.assertIn(
            "lines[version_rows[0]] = f'version: \"{version}\"'",
            chart_prepare["run"],
        )
        self.assertIn(
            "f'appVersion: \"{version}\"'",
            chart_prepare["run"],
        )
        self.assertIn("helm dependency list", chart_prepare["run"])
        self.assertIn("helm repo add", chart_prepare["run"])
        self.assertIn("helm_dependency_build", chart_prepare["run"])
        self.assertIn("HELM_REPOSITORY_CONFIG", path.read_text())
        self.assertIn("HELM_REPOSITORY_CACHE", path.read_text())

        self.assertEqual(drive["with"]["file_name"], "${{ github.run_id }}-${{ github.run_attempt }}.txt")
        self.assertEqual(drive["with"]["gzip"], "false")
        self.assertEqual(drive["with"]["mime_type"], "text/plain")
        self.assertEqual(drive["if"], "${{ always() && steps.scrub.outcome == 'success' }}")

        self.assertLess(names.index("Check out exact product source ref"), names.index("Verify exact source ref authority"))
        self.assertLess(names.index("Verify exact source ref authority"), names.index("Record exact product source SHA"))
        self.assertLess(names.index("Record exact product source SHA"), names.index("Resolve product-owned publication version"))
        self.assertLess(names.index("Resolve product-owned publication version"), names.index("Prepare isolated locked Helm dependencies"))
        self.assertLess(names.index("Prepare isolated locked Helm dependencies"), names.index("Build image and package chart"))
        self.assertLess(names.index("Build image and package chart"), names.index("Revalidate exact source ref before publication"))
        self.assertLess(names.index("Revalidate exact source ref before publication"), names.index("Publish immutable image and chart"))
        self.assertLess(names.index("Publish immutable image and chart"), names.index("Authenticated private registry read-back"))
        self.assertLess(names.index("Authenticated private registry read-back"), names.index("Clean publication state"))
        self.assertLess(names.index("Clean publication state"), names.index("Scrub private release log"))
        self.assertLess(names.index("Scrub private release log"), names.index("Upload private release log to Google Drive"))
        self.assertLess(names.index("Upload private release log to Google Drive"), names.index("Finish Agent State run"))

        self.assertIn('test "$(git -C source rev-parse HEAD)" = "${SOURCE_SHA}"', revalidate["run"])
        self.assertIn('FETCH_HEAD^{commit}', revalidate["run"])
        self.assertEqual(revalidate["env"]["SOURCE_REF"], "${{ steps.requested_ref.outputs.full_ref }}")
        version_expr = "${{ steps.publication.outputs.publication_version }}"
        prepare = by_name["Build image and package chart"]
        self.assertEqual(prepare["env"]["VERSION"], version_expr)
        self.assertEqual(prepare["env"]["CHART_PATH"], "${{ steps.chart_prepare.outputs.chart_path }}")
        self.assertEqual(authenticate["env"]["VERSION"], version_expr)
        self.assertEqual(publish["env"]["VERSION"], version_expr)
        self.assertEqual(readback["env"]["VERSION"], version_expr)
        self.assertIn(version_expr, publish["env"]["REMOTE_IMAGE_REFERENCE"])
        self.assertIn(version_expr, readback["env"]["REMOTE_IMAGE_REFERENCE"])
        self.assertNotIn("steps.authority.outputs.version", path.read_text())
        self.assertIn("Immutable publication already exists", authenticate["run"])
        self.assertIn('require_unused "${image_reference}"', authenticate["run"])
        self.assertIn('require_unused "${chart_reference}"', authenticate["run"])
        self.assertIn("buildah push", publish["run"])
        self.assertIn("helm push", publish["run"])
        self.assertIn("skopeo inspect", readback["run"])
        self.assertIn("helm pull", readback["run"])
        self.assertIn('helm show chart "${chart_archive}"', readback["run"])
        self.assertIn("Published chart version/appVersion do not match publication VERSION", readback["run"])
        self.assertEqual(cleanup["if"], "always()")
        self.assertEqual(scrub["if"], "always()")
        status = finish["with"]["status"]
        for required in (
            "steps.publication.outcome == 'success'",
            "steps.chart_prepare.outcome == 'success'",
            "steps.revalidate.outcome == 'success'",
            "steps.publish.outcome == 'success'",
            "steps.readback.outcome == 'success'",
            "steps.cleanup.outcome == 'success'",
            "steps.scrub.outcome == 'success'",
            "steps.drive.outcome == 'success'",
        ):
            self.assertIn(required, status)

    def test_locked_dependencies_are_prepared_in_run_owned_chart_copy(self) -> None:
        path = ROOT / ".github/workflows/public-native-image-chart.yml"
        workflow = yaml.safe_load(path.read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        chart_prepare = next(
            step for step in steps if step.get("name") == "Prepare isolated locked Helm dependencies"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_chart = root / "source" / "charts" / "fixture"
            source_chart.mkdir(parents=True)
            (source_chart / "Chart.yaml").write_text(
                "apiVersion: v2\n"
                "name: fixture\n"
                "version: 1.0.0\n"
                "dependencies:\n"
                "  - name: valkey\n"
                "    version: 0.11.0\n"
                "    repository: https://valkey.example.test/charts/\n",
                encoding="utf-8",
            )
            (source_chart / "Chart.lock").write_text(
                "dependencies:\n"
                "  - name: valkey\n"
                "    repository: https://valkey.example.test/charts/\n"
                "    version: 0.11.0\n"
                "digest: sha256:fixture\n",
                encoding="utf-8",
            )

            runner_temp = root / "runner-temp"
            state_root = runner_temp / "private-native-image-chart"
            state_root.mkdir(parents=True)
            (state_root / "helm-repository-cache").mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            calls = root / "helm-calls"
            helm = bin_dir / "helm"
            helm.write_text(
                "#!/usr/bin/env bash\n"
                "set -Eeuo pipefail\n"
                "printf '%s\\n' \"$*\" >> \"${HELM_CALLS}\"\n"
                "if test \"$1\" = dependency && test \"$2\" = list; then\n"
                "  printf '%s\\n' 'NAME VERSION REPOSITORY STATUS' "
                "'valkey 0.11.0 https://valkey.example.test/charts/ missing'\n"
                "elif test \"$1\" = repo && test \"$2\" = add; then\n"
                "  exit 0\n"
                "elif test \"$1\" = dependency && test \"$2\" = build; then\n"
                "  mkdir -p \"$3/charts\"\n"
                "  printf 'fixture' > \"$3/charts/valkey-0.11.0.tgz\"\n"
                "else\n"
                "  exit 9\n"
                "fi\n",
                encoding="utf-8",
            )
            helm.chmod(0o755)

            fake_src = root / "fake-src"
            package_dir = fake_src / "ci_workflows"
            package_dir.mkdir(parents=True)
            (package_dir / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "packaging_primitives.py").write_text(
                "import subprocess\n"
                "def helm_dependency_build(chart, *, environment=None, tool='helm'):\n"
                "    subprocess.run([tool, 'dependency', 'build', str(chart)], "
                "check=True, env=dict(environment or {}), text=True)\n",
                encoding="utf-8",
            )

            output = root / "github-output"
            ci_log = root / "ci.log"
            repository_config = state_root / "helm-repositories.yaml"
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "PYTHONPATH": str(fake_src),
                "CHART_PATH": "charts/fixture",
                "VERSION": "2.4.0-rc.1",
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_OUTPUT": str(output),
                "CI_LOG": str(ci_log),
                "HELM_REPOSITORY_CONFIG": str(repository_config),
                "HELM_REPOSITORY_CACHE": str(state_root / "helm-repository-cache"),
                "HELM_CALLS": str(calls),
            }
            completed = subprocess.run(
                ["bash", "-c", chart_prepare["run"]],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            prepared = state_root / "chart"
            self.assertTrue((prepared / "charts" / "valkey-0.11.0.tgz").is_file())
            self.assertFalse((source_chart / "charts").exists())
            prepared_metadata = yaml.safe_load((prepared / "Chart.yaml").read_text())
            self.assertEqual(prepared_metadata["version"], "2.4.0-rc.1")
            self.assertEqual(prepared_metadata["appVersion"], "2.4.0-rc.1")
            source_metadata = yaml.safe_load((source_chart / "Chart.yaml").read_text())
            self.assertEqual(source_metadata["version"], "1.0.0")
            self.assertNotIn("appVersion", source_metadata)
            self.assertEqual(output.read_text(), f"chart_path={prepared}\n")
            call_text = calls.read_text()
            self.assertIn(
                "repo add ciw-dependency-1 https://valkey.example.test/charts/ --force-update",
                call_text,
            )
            self.assertIn(f"dependency build {prepared}", call_text)

    def _publication_fixture(self, root: Path, *, version_contents: str | None) -> tuple[dict[str, str], Path, Path]:
        source = root / "source"
        chart = source / "charts" / "fixture"
        chart.mkdir(parents=True)
        (chart / "Chart.yaml").write_text(
            "apiVersion: v2\nname: fixture\nversion: 9.9.9\n",
            encoding="utf-8",
        )
        if version_contents is not None:
            (source / "VERSION").write_text(version_contents, encoding="ascii")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)

        bin_dir = root / "bin"
        bin_dir.mkdir()
        helm_calls = root / "helm-calls"
        helm = bin_dir / "helm"
        helm.write_text(
            "#!/usr/bin/env bash\n"
            "set -Eeuo pipefail\n"
            "printf '%s\n' \"$*\" >> \"${HELM_CALLS}\"\n"
            "test \"$1\" = show && test \"$2\" = chart\n"
            "printf '%s\n' 'apiVersion: v2' 'name: fixture' 'version: 9.9.9'\n",
            encoding="utf-8",
        )
        helm.chmod(0o755)
        output = root / "github-output"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CHART_PATH": "charts/fixture",
            "GITHUB_OUTPUT": str(output),
            "HELM_CALLS": str(helm_calls),
            "RELEASE_TAG": "source-release-candidate",
        }
        return env, output, helm_calls

    def test_root_version_is_preferred_and_no_caller_path_selector_exists(self) -> None:
        path = ROOT / ".github/workflows/public-native-image-chart.yml"
        workflow = yaml.safe_load(path.read_text())
        publication = next(
            step for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Resolve product-owned publication version"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env, output, helm_calls = self._publication_fixture(
                root, version_contents="2.4.0-rc.1\n"
            )
            completed = subprocess.run(
                ["bash", "-c", publication["run"]],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                output.read_text(),
                "publication_version=2.4.0-rc.1\npublication_source=root-VERSION\n",
            )
            self.assertFalse(helm_calls.exists(), "Chart.yaml fallback must not run when VERSION exists")

    def test_missing_root_version_preserves_chart_version_fallback(self) -> None:
        path = ROOT / ".github/workflows/public-native-image-chart.yml"
        workflow = yaml.safe_load(path.read_text())
        publication = next(
            step for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Resolve product-owned publication version"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env, output, helm_calls = self._publication_fixture(root, version_contents=None)
            completed = subprocess.run(
                ["bash", "-c", publication["run"]],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                output.read_text(),
                "publication_version=9.9.9\npublication_source=chart-version\n",
            )
            self.assertIn("show chart source/charts/fixture", helm_calls.read_text())

    def test_malformed_root_version_fails_closed_without_chart_fallback(self) -> None:
        path = ROOT / ".github/workflows/public-native-image-chart.yml"
        workflow = yaml.safe_load(path.read_text())
        publication = next(
            step for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Resolve product-owned publication version"
        )

        invalid_values = (
            "",
            "1.2\n",
            "01.2.3\n",
            "1.2.3\n2.0.0\n",
            " 1.2.3\n",
            "1.2.3-01\n",
            "1.2.3+build.5\n",
        )
        for value in invalid_values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                env, output, helm_calls = self._publication_fixture(root, version_contents=value)
                completed = subprocess.run(
                    ["bash", "-c", publication["run"]],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(output.exists() and output.read_text())
                self.assertFalse(helm_calls.exists(), "Malformed VERSION must never fall back to Chart.yaml")

    def test_symlink_root_version_fails_closed(self) -> None:
        path = ROOT / ".github/workflows/public-native-image-chart.yml"
        workflow = yaml.safe_load(path.read_text())
        publication = next(
            step for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Resolve product-owned publication version"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env, output, helm_calls = self._publication_fixture(root, version_contents=None)
            source = root / "source"
            (source / "version-target").write_text("1.2.3\n", encoding="ascii")
            (source / "VERSION").symlink_to("version-target")
            subprocess.run(["git", "-C", str(source), "add", "VERSION", "version-target"], check=True)
            completed = subprocess.run(
                ["bash", "-c", publication["run"]],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output.exists() and output.read_text())
            self.assertFalse(helm_calls.exists())


if __name__ == "__main__":
    unittest.main()
