from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.packaging_primitives import (
    OCI_EMULATED_PLATFORMS_ENV,
    OCI_NATIVE_PLATFORM_ENV,
    OCIIndexInspection,
    PackagingError,
    PlatformBuildResult,
    assemble_multi_platform_index,
    build_platform_images,
    cleanup_multi_platform_state,
    inspect_multi_platform_index,
    normalize_platforms,
    plan_platform_executions,
)


def _completed(argv: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")


def _index_payload() -> str:
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + "a" * 64,
                    "size": 1200,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + "b" * 64,
                    "size": 1300,
                    "platform": {
                        "os": "linux",
                        "architecture": "arm64",
                        "variant": "v8",
                    },
                },
            ],
        }
    )


class MultiArchitecturePackagingPrimitiveTests(unittest.TestCase):
    def test_platform_normalization_is_canonical_deterministic_and_unique(self) -> None:
        self.assertEqual(
            normalize_platforms(("linux/aarch64", "linux/x86_64")),
            ("linux/amd64", "linux/arm64/v8"),
        )
        with self.assertRaisesRegex(PackagingError, "duplicate platform"):
            normalize_platforms(("linux/arm64", "linux/arm64/v8"))
        with self.assertRaisesRegex(PackagingError, "unsupported platform"):
            normalize_platforms(("windows/amd64",))

    def test_execution_plan_distinguishes_native_and_explicit_emulation(self) -> None:
        environment = {
            OCI_NATIVE_PLATFORM_ENV: "linux/x86_64",
            OCI_EMULATED_PLATFORMS_ENV: "linux/arm64/v8",
        }
        plans = plan_platform_executions(
            ("linux/arm64", "linux/amd64"),
            environment=environment,
        )
        self.assertEqual(
            [(plan.platform, plan.strategy, plan.native_platform) for plan in plans],
            [
                ("linux/amd64", "native", "linux/amd64"),
                ("linux/arm64/v8", "emulated", "linux/amd64"),
            ],
        )

    def test_foreign_architecture_without_runner_capability_fails_before_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            dockerfile = context / "Dockerfile"
            dockerfile.write_text(
                "FROM debian:trixie-slim\nRUN printf 'ordinary-run-step\\n'\n",
                encoding="utf-8",
            )
            with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
                with self.assertRaisesRegex(
                    PackagingError,
                    "unsupported foreign platform execution: linux/arm64/v8",
                ):
                    build_platform_images(
                        context,
                        dockerfile,
                        ("linux/amd64", "linux/arm64/v8"),
                        {
                            "linux/amd64": "local/demo:amd64",
                            "linux/arm64/v8": "local/demo:arm64-v8",
                        },
                        environment={OCI_NATIVE_PLATFORM_ENV: "linux/amd64"},
                        tool="buildah",
                    )
                run.assert_not_called()

    def test_compatible_runner_builds_run_steps_for_both_platforms(self) -> None:
        environment = {
            OCI_NATIVE_PLATFORM_ENV: "linux/amd64",
            OCI_EMULATED_PLATFORMS_ENV: "linux/arm64",
        }
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            dockerfile = context / "Containerfile"
            dockerfile.write_text(
                "FROM debian:trixie-slim\nRUN test -x /bin/sh\n",
                encoding="utf-8",
            )
            with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
                run.side_effect = [_completed(["buildah"]), _completed(["buildah"])]
                builds = build_platform_images(
                    context,
                    dockerfile,
                    ("linux/amd64", "linux/arm64/v8"),
                    {
                        "linux/amd64": "local/demo:amd64",
                        "linux/arm64": "local/demo:arm64-v8",
                    },
                    build_args={"VERSION": "1.2.3"},
                    environment=environment,
                    tool="buildah",
                )
        self.assertEqual(
            [(item.platform, item.strategy, item.reference) for item in builds],
            [
                ("linux/amd64", "native", "local/demo:amd64"),
                ("linux/arm64/v8", "emulated", "local/demo:arm64-v8"),
            ],
        )
        amd64_argv = run.call_args_list[0].args[0]
        arm64_argv = run.call_args_list[1].args[0]
        self.assertEqual(amd64_argv[:4], ["buildah", "bud", "--platform", "linux/amd64"])
        self.assertEqual(
            arm64_argv[:4],
            ["buildah", "bud", "--platform", "linux/arm64/v8"],
        )
        self.assertIn("VERSION=1.2.3", amd64_argv)
        self.assertIn("VERSION=1.2.3", arm64_argv)
        self.assertEqual(amd64_argv[-1], str(context))
        self.assertEqual(arm64_argv[-1], str(context))

    def test_buildah_manifest_assembly_and_inspection_are_structured(self) -> None:
        builds = (
            PlatformBuildResult("linux/amd64", "native", "local/demo:amd64"),
            PlatformBuildResult("linux/arm64/v8", "emulated", "local/demo:arm64-v8"),
        )
        with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
            run.side_effect = [
                _completed(["buildah"]),
                _completed(["buildah"]),
                _completed(["buildah"]),
                _completed(["buildah"], _index_payload()),
            ]
            result = assemble_multi_platform_index(
                "local/demo:multi",
                builds,
                tool="buildah",
            )
        self.assertIsInstance(result, OCIIndexInspection)
        self.assertEqual(result.reference, "local/demo:multi")
        self.assertEqual(result.platforms, ("linux/amd64", "linux/arm64/v8"))
        self.assertEqual(
            [descriptor.digest for descriptor in result.descriptors],
            ["sha256:" + "a" * 64, "sha256:" + "b" * 64],
        )
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["buildah", "manifest", "create", "local/demo:multi"],
                [
                    "buildah",
                    "manifest",
                    "add",
                    "local/demo:multi",
                    "local/demo:amd64",
                ],
                [
                    "buildah",
                    "manifest",
                    "add",
                    "local/demo:multi",
                    "local/demo:arm64-v8",
                ],
                ["buildah", "manifest", "inspect", "local/demo:multi"],
            ],
        )

    def test_docker_manifest_create_uses_all_successful_platform_references(self) -> None:
        builds = (
            PlatformBuildResult("linux/amd64", "native", "registry/demo:amd64"),
            PlatformBuildResult("linux/arm64/v8", "emulated", "registry/demo:arm64-v8"),
        )
        with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
            run.side_effect = [
                _completed(["docker"]),
                _completed(["docker"], _index_payload()),
            ]
            assemble_multi_platform_index(
                "registry/demo:multi",
                builds,
                tool="docker",
            )
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "docker",
                "manifest",
                "create",
                "registry/demo:multi",
                "registry/demo:amd64",
                "registry/demo:arm64-v8",
            ],
        )

    def test_manifest_inspection_rejects_wrong_platform_or_digest(self) -> None:
        payload = json.loads(_index_payload())
        payload["manifests"][1]["platform"]["architecture"] = "ppc64le"
        with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
            run.return_value = _completed(["buildah"], json.dumps(payload))
            with self.assertRaisesRegex(PackagingError, "unsupported platform"):
                inspect_multi_platform_index(
                    "local/demo:multi",
                    ("linux/amd64", "linux/arm64/v8"),
                )
        payload = json.loads(_index_payload())
        payload["manifests"][0]["digest"] = "sha256:not-a-digest"
        with patch("ci_workflows.packaging_primitives.subprocess.run") as run:
            run.return_value = _completed(["buildah"], json.dumps(payload))
            with self.assertRaisesRegex(PackagingError, "invalid manifest digest"):
                inspect_multi_platform_index(
                    "local/demo:multi",
                    ("linux/amd64", "linux/arm64/v8"),
                )

    def test_multiarch_cleanup_removes_layouts_without_following_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            state = root / "state"
            amd64 = state / "linux-amd64"
            arm64 = state / "linux-arm64-v8"
            for path in (amd64, arm64):
                path.mkdir(parents=True)
                (path / "oci-layout").write_text("{}", encoding="utf-8")
            (arm64 / "outside").symlink_to(outside, target_is_directory=True)
            cleanup_multi_platform_state((amd64, arm64))
            self.assertTrue(sentinel.is_file())
            self.assertFalse(amd64.exists())
            self.assertFalse(arm64.exists())

    def test_runner_capacity_contract_contains_no_runner_labels_or_product_policy(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src/ci_workflows/packaging_primitives.py"
        ).read_text(encoding="utf-8").casefold()
        for forbidden in ("self-hosted", "runner label", "iptv", "flux reconcile"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
