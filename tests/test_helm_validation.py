from __future__ import annotations

import gzip
import hashlib
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
    _image_reference_assertions,
    cleanup_helm_state,
    normalize_chart_archive,
    validate_and_package,
    verify_no_helm_residue,
)
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests/fixtures/helm-validation"
SHA = "a" * 40

PRODUCTS = {
    "iptv-backend-chart": (
        "StreamScapeTV/iptv-backend",
        FIXTURE_ROOT / "backend",
        "1.2.3",
    ),
    "agent-state-chart": (
        "StreamScapeTV/agent-state",
        FIXTURE_ROOT / "agent-state",
        "1.2.3",
    ),
    "flux-github-actions-runner-chart": (
        "StreamScapeTV/flux",
        FIXTURE_ROOT / "flux",
        "1.3.0",
    ),
}


def request_environment(
    product_id: str = "iptv-backend-chart",
    **overrides: str,
) -> dict[str, str]:
    repository, _, release_version = PRODUCTS[product_id]
    values = {
        "GITHUB_REPOSITORY": repository,
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_PRODUCT_ID": product_id,
        "INPUT_RELEASE_VERSION": release_version,
        "INPUT_VALUES_PROFILE": "default",
        "INPUT_POLICY_PATH": "",
        "INPUT_ARTIFACT_EXCEPTION_ID": "",
        "INPUT_SOURCE_TRUST": "trusted-exact",
    }
    values.update(overrides)
    return values


def chart_archive(
    destination: Path,
    chart_root: Path,
    chart_name: str,
    *,
    secret_template: bool = False,
) -> None:
    with destination.open("wb") as raw:
        with gzip.GzipFile(
            fileobj=raw,
            mode="wb",
            mtime=177,
            filename="unstable",
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in reversed(sorted(chart_root.rglob("*"))):
                    if path.is_dir():
                        continue
                    archive.add(
                        path,
                        arcname=(
                            f"{chart_name}/"
                            f"{path.relative_to(chart_root).as_posix()}"
                        ),
                    )
                if secret_template:
                    payload = b"token: ghp_abcdefghijklmnopqrstuv\n"
                    info = tarfile.TarInfo(
                        f"{chart_name}/templates/secret.yaml"
                    )
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))


def source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class HelmValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_helm_contract(ROOT)

    def copied_fixture(
        self,
        product_id: str = "iptv-backend-chart",
    ) -> tempfile.TemporaryDirectory[str]:
        directory = tempfile.TemporaryDirectory()
        _, fixture, _ = PRODUCTS[product_id]
        shutil.copytree(fixture, Path(directory.name) / "source")
        return directory

    def test_contract_tracks_real_current_chart_producer_identities(self) -> None:
        self.assertEqual(
            set(self.contract["products"]),
            {
                "iptv-backend-chart",
                "agent-state-chart",
                "flux-github-actions-runner-chart",
            },
        )
        self.assertEqual(
            self.contract["products"]["flux-github-actions-runner-chart"][
                "chart_name"
            ],
            "github-actions-runner",
        )
        self.assertEqual(self.contract["runner_profile"], "portable")
        self.assertEqual(self.contract["artifact_policy"], "zero-default")

    def test_all_three_current_producer_shapes_resolve_same_contract(self) -> None:
        for product_id, (_, _, _) in PRODUCTS.items():
            with self.subTest(product_id=product_id):
                with self.copied_fixture(product_id) as directory:
                    source = Path(directory) / "source"
                    request = request_from_environment(
                        request_environment(product_id)
                    )
                    plan = resolve_validation_plan(
                        source,
                        self.contract,
                        request,
                    )
                    chart_root, values = validate_chart_layout(source, plan)
                    self.assertTrue((chart_root / "Chart.yaml").is_file())
                    self.assertEqual(values.name, "values.yaml")

    def test_backend_https_dependency_and_exact_lock_are_admitted(self) -> None:
        with self.copied_fixture() as directory:
            source = Path(directory) / "source"
            request = request_from_environment(request_environment())
            plan = resolve_validation_plan(source, self.contract, request)
            chart_root, _ = validate_chart_layout(source, plan)
            self.assertEqual(
                plan.product.locked_dependencies,
                (
                    (
                        "valkey",
                        "0.11.0",
                        "https://valkey.io/valkey-helm/",
                    ),
                ),
            )
            lock = chart_root / "Chart.lock"
            lock.write_text(
                lock.read_text(encoding="utf-8").replace(
                    "version: 0.11.0",
                    "version: 0.12.0",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                HelmValidationError,
                "dependency_lock_invalid",
            ):
                validate_chart_layout(source, plan)

    def test_dependency_repository_rejects_embedded_credentials(self) -> None:
        with self.copied_fixture() as directory:
            source = Path(directory) / "source"
            manifest = source / ".streamscape/helm-product.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["locked_dependencies"][0]["repository"] = (
                "https://user:password@valkey.io/valkey-helm/"
            )
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(
                HelmValidationError,
                "invalid_product_manifest",
            ):
                resolve_validation_plan(
                    source,
                    self.contract,
                    request_from_environment(request_environment()),
                )

    def test_source_trust_is_explicit_and_validation_accepts_forks(self) -> None:
        missing = request_environment()
        missing["INPUT_SOURCE_TRUST"] = ""
        with self.assertRaisesRegex(HelmValidationError, "invalid_input"):
            request_from_environment(missing)
        fork = request_from_environment(
            request_environment(INPUT_SOURCE_TRUST="untrusted-fork")
        )
        self.assertEqual(fork.source_trust, "untrusted-fork")

    def test_traversal_mismatched_repository_and_artifact_exception_fail_closed(
        self,
    ) -> None:
        with self.copied_fixture() as directory:
            source = Path(directory) / "source"
            manifest = source / ".streamscape/helm-product.json"
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["chart_root"] = "../outside"
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                HelmValidationError,
                "invalid_product_manifest",
            ):
                resolve_validation_plan(
                    source,
                    self.contract,
                    request_from_environment(request_environment()),
                )
        with self.copied_fixture() as directory:
            with self.assertRaisesRegex(
                HelmValidationError,
                "repository_rejected",
            ):
                resolve_validation_plan(
                    Path(directory) / "source",
                    self.contract,
                    request_from_environment(
                        request_environment(
                            GITHUB_REPOSITORY="StreamScapeTV/other"
                        )
                    ),
                )
        with self.assertRaisesRegex(
            HelmValidationError,
            "artifact_policy_failed",
        ):
            request_from_environment(
                request_environment(
                    INPUT_ARTIFACT_EXCEPTION_ID="debug-package"
                )
            )

    def test_every_rendered_image_is_immutable_even_without_expected_list(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            HelmValidationError,
            "image_reference_mismatch",
        ):
            _image_reference_assertions(
                "containers:\n  - image: registry.example/app:latest\n",
                (),
            )
        _image_reference_assertions(
            "containers:\n"
            f"  - image: registry.example/app@sha256:{'f' * 64}\n",
            (),
        )

    def test_normalized_archive_is_stable_and_scans_templates_for_secrets(
        self,
    ) -> None:
        with self.copied_fixture() as directory:
            path = Path(directory)
            chart_root = path / "source/charts/iptv-backend"
            first = path / "first.tgz"
            second = path / "second.tgz"
            chart_archive(first, chart_root, "iptv-backend")
            chart_archive(second, chart_root, "iptv-backend")
            one = normalize_chart_archive(
                first,
                path / "one.tgz",
                "iptv-backend",
            )
            two = normalize_chart_archive(
                second,
                path / "two.tgz",
                "iptv-backend",
            )
            self.assertEqual(one, two)
            with tarfile.open(path / "one.tgz", "r:gz") as archive:
                self.assertTrue(
                    all(member.mtime == 0 for member in archive.getmembers())
                )
                self.assertTrue(
                    all(
                        member.uid == 0 and member.gid == 0
                        for member in archive.getmembers()
                    )
                )
            secret = path / "secret.tgz"
            chart_archive(
                secret,
                chart_root,
                "iptv-backend",
                secret_template=True,
            )
            with self.assertRaisesRegex(
                HelmValidationError,
                "archive_secret_detected",
            ):
                normalize_chart_archive(
                    secret,
                    path / "rejected.tgz",
                    "iptv-backend",
                )

    def test_release_override_builds_only_from_isolated_chart_copy(self) -> None:
        product_id = "agent-state-chart"
        with self.copied_fixture(product_id) as directory:
            path = Path(directory)
            source = path / "source"
            state = path / "state"
            state.mkdir()
            before = source_fingerprint(source)
            plan = resolve_validation_plan(
                source,
                self.contract,
                request_from_environment(
                    request_environment(product_id)
                ),
            )
            package_commands: list[list[str]] = []

            def fake_run(
                argv,
                *,
                cwd,
                environment,
                timeout,
                code,
                stdin=None,
                check=True,
            ):
                if argv[:2] == ["helm", "template"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        (
                            "image: "
                            "registry.example/agent-state@sha256:"
                            + "b" * 64
                            + "\n"
                        ),
                        "",
                    )
                if argv[:2] == ["helm", "package"]:
                    package_commands.append(list(argv))
                    chart_root = Path(argv[2])
                    self.assertFalse(
                        chart_root.resolve().is_relative_to(source.resolve())
                    )
                    output = Path(argv[argv.index("--destination") + 1])
                    output.mkdir(parents=True, exist_ok=True)
                    version = argv[argv.index("--version") + 1]
                    chart_archive(
                        output / f"agent-state-{version}.tgz",
                        chart_root,
                        "agent-state",
                    )
                return subprocess.CompletedProcess(argv, 0, "", "")

            with (
                patch("ci_workflows.helm_execution.verify_exact_source"),
                patch("ci_workflows.helm_execution.verify_helm_toolchain"),
                patch(
                    "ci_workflows.helm_execution._run",
                    side_effect=fake_run,
                ),
            ):
                result = validate_and_package(
                    source,
                    state,
                    plan,
                    SHA,
                    {"PATH": "/usr/bin", "HOME": str(path)},
                )
            self.assertTrue(result.archive_path.is_file())
            self.assertEqual(source_fingerprint(source), before)
            self.assertEqual(len(package_commands), 1)
            self.assertIn("--version", package_commands[0])
            self.assertIn("--app-version", package_commands[0])
            self.assertEqual(
                package_commands[0][
                    package_commands[0].index("--version") + 1
                ],
                "1.2.3",
            )
            self.assertEqual(
                package_commands[0][
                    package_commands[0].index("--app-version") + 1
                ],
                "1.2.3",
            )
            cleanup_helm_state(state)
            verify_no_helm_residue(state)


if __name__ == "__main__":
    unittest.main()
