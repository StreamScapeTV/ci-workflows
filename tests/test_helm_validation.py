from __future__ import annotations

import gzip
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_contract import (
    load_helm_contract,
    request_from_environment,
    resolve_validation_plan,
    validate_chart_layout,
)
from ci_workflows.helm_execution import (
    cleanup_helm_state,
    normalize_chart_archive,
    validate_and_package,
    verify_no_helm_residue,
)
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/helm-validation/backend"
SHA = "a" * 40


def request_environment(**overrides: str) -> dict[str, str]:
    values = {
        "GITHUB_REPOSITORY": "StreamScapeTV/iptv-backend",
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_PRODUCT_ID": "iptv-backend-chart",
        "INPUT_RELEASE_VERSION": "1.2.3",
        "INPUT_VALUES_PROFILE": "default",
        "INPUT_POLICY_PATH": "",
        "INPUT_ARTIFACT_EXCEPTION_ID": "",
        "INPUT_SOURCE_TRUST": "trusted-exact",
    }
    values.update(overrides)
    return values


def chart_archive(destination: Path, chart_root: Path, *, secret: bool = False) -> None:
    with destination.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=177, filename="unstable") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in reversed(sorted(chart_root.rglob("*"))):
                    if path.is_dir():
                        continue
                    archive.add(path, arcname=f"iptv-backend/{path.relative_to(chart_root).as_posix()}")
                if secret:
                    payload = b"token=ghp_abcdefghijklmnopqrstuv\n"
                    info = tarfile.TarInfo("iptv-backend/config.txt")
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))


class HelmValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_helm_contract(ROOT)

    def copied_fixture(self) -> tempfile.TemporaryDirectory[str]:
        directory = tempfile.TemporaryDirectory()
        shutil.copytree(FIXTURE, Path(directory.name) / "source")
        return directory

    def test_contract_has_exact_known_products_and_no_runner_or_registry_input(self) -> None:
        self.assertEqual(
            set(self.contract["products"]),
            {"iptv-backend-chart", "agent-state-chart", "flux-runner-chart-assets"},
        )
        self.assertEqual(self.contract["runner_profile"], "portable")
        self.assertEqual(self.contract["artifact_policy"], "zero-default")
        self.assertEqual(request_from_environment(request_environment()).product_id, "iptv-backend-chart")

    def test_product_manifest_is_the_fixed_chart_root_authority(self) -> None:
        with self.copied_fixture() as directory:
            source = Path(directory) / "source"
            plan = resolve_validation_plan(source, self.contract, request_from_environment(request_environment()))
            self.assertEqual(plan.product.chart_root, "charts/iptv-backend")
            self.assertEqual(plan.values_path, "values.yaml")
            chart_root, values = validate_chart_layout(source, plan)
            self.assertTrue((chart_root / "Chart.yaml").is_file())
            self.assertEqual(values.name, "values.yaml")

    def test_traversal_mismatched_repository_and_artifact_exception_fail_closed(self) -> None:
        with self.copied_fixture() as directory:
            source = Path(directory) / "source"
            manifest = source / ".streamscape/helm-product.json"
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["chart_root"] = "../outside"
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(HelmValidationError, "invalid_product_manifest"):
                resolve_validation_plan(source, self.contract, request_from_environment(request_environment()))
        with self.copied_fixture() as directory:
            with self.assertRaisesRegex(HelmValidationError, "repository_rejected"):
                resolve_validation_plan(
                    Path(directory) / "source",
                    self.contract,
                    request_from_environment(request_environment(GITHUB_REPOSITORY="StreamScapeTV/other")),
                )
        with self.assertRaisesRegex(HelmValidationError, "artifact_policy_failed"):
            request_from_environment(request_environment(INPUT_ARTIFACT_EXCEPTION_ID="debug-package"))

    def test_dependency_lock_must_match_chart_metadata(self) -> None:
        with self.copied_fixture() as directory:
            source = Path(directory) / "source"
            chart = source / "charts/iptv-backend/Chart.yaml"
            chart.write_text(
                chart.read_text(encoding="utf-8")
                + "dependencies:\n  - name: common\n    version: 1.0.0\n    repository: oci://git.faruqi.dev/charts\n",
                encoding="utf-8",
            )
            plan = resolve_validation_plan(source, self.contract, request_from_environment(request_environment()))
            with self.assertRaisesRegex(HelmValidationError, "dependency_lock_invalid"):
                validate_chart_layout(source, plan)

    def test_normalized_archive_is_stable_and_rejects_token_content(self) -> None:
        with self.copied_fixture() as directory:
            path = Path(directory)
            chart_root = path / "source/charts/iptv-backend"
            first = path / "first.tgz"
            second = path / "second.tgz"
            chart_archive(first, chart_root)
            chart_archive(second, chart_root)
            one = normalize_chart_archive(first, path / "one.tgz", "iptv-backend")
            two = normalize_chart_archive(second, path / "two.tgz", "iptv-backend")
            self.assertEqual(one, two)
            with tarfile.open(path / "one.tgz", "r:gz") as archive:
                self.assertTrue(all(member.mtime == 0 for member in archive.getmembers()))
                self.assertTrue(all(member.uid == 0 and member.gid == 0 for member in archive.getmembers()))
            secret = path / "secret.tgz"
            chart_archive(secret, chart_root, secret=True)
            with self.assertRaisesRegex(HelmValidationError, "archive_secret_detected"):
                normalize_chart_archive(secret, path / "rejected.tgz", "iptv-backend")
            traversal = path / "traversal.tgz"
            with tarfile.open(traversal, "w:gz") as archive:
                payload = b"apiVersion: v2\nname: iptv-backend\nversion: 1.2.3\n"
                info = tarfile.TarInfo("../iptv-backend/Chart.yaml")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            with self.assertRaisesRegex(HelmValidationError, "archive_invalid"):
                normalize_chart_archive(traversal, path / "traversal-rejected.tgz", "iptv-backend")

    def test_synthetic_lint_template_package_and_terminal_cleanup(self) -> None:
        with self.copied_fixture() as directory:
            path = Path(directory)
            source = path / "source"
            state = path / "state"
            state.mkdir()
            plan = resolve_validation_plan(source, self.contract, request_from_environment(request_environment()))

            def fake_run(argv, *, cwd, environment, timeout, code, stdin=None, check=True):
                if argv[:2] == ["helm", "template"]:
                    return subprocess.CompletedProcess(argv, 0, "image: registry.example/iptv-backend@sha256:" + "a" * 64 + "\n", "")
                if argv[:2] == ["helm", "package"]:
                    output = Path(argv[argv.index("--destination") + 1])
                    output.mkdir(parents=True, exist_ok=True)
                    chart_archive(output / "iptv-backend-1.2.3.tgz", source / "charts/iptv-backend")
                return subprocess.CompletedProcess(argv, 0, "", "")

            with patch("ci_workflows.helm_execution.verify_exact_source"), patch("ci_workflows.helm_execution.verify_helm_toolchain"), patch("ci_workflows.helm_execution._run", side_effect=fake_run):
                result = validate_and_package(source, state, plan, SHA, {"PATH": "/usr/bin", "HOME": str(path)})
            self.assertTrue(result.archive_path.is_file())
            self.assertEqual(result.chart_digest, f"sha256:{result.package_sha256}")
            cleanup_helm_state(state)
            verify_no_helm_residue(state)


if __name__ == "__main__":
    unittest.main()
