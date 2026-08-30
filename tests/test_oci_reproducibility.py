from pathlib import Path
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_oci_helper():
    path = ROOT / "scripts/ci/oci_reproducibility.py"
    spec = importlib.util.spec_from_file_location("ciw_oci_reproducibility", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load OCI reproducibility helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OCI_HELPER = _load_oci_helper()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _put_blob(layout: Path, data: bytes) -> tuple[str, int]:
    digest = hashlib.sha256(data).hexdigest()
    path = layout / "blobs" / "sha256" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"sha256:{digest}", len(data)


def _write_test_oci_layout(
    layout: Path,
    *,
    missing_arm64: bool = False,
    raw_config_variant: bool = False,
    wrong_config_architecture: bool = False,
) -> None:
    layout.mkdir(parents=True, exist_ok=True)
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    manifest_descriptors = []
    platforms = [("amd64", None)]
    if not missing_arm64:
        platforms.append(("arm64", "v8"))
    for architecture, variant in platforms:
        config = {
            "architecture": (
                "arm64"
                if wrong_config_architecture and architecture == "amd64"
                else architecture
            ),
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [f"sha256:{'1' * 64}"]},
        }
        config_bytes = _json_bytes(config)
        if raw_config_variant and architecture == "amd64":
            config_bytes = (
                b'{"os":"linux","rootfs":{"diff_ids":["sha256:'
                + (b"1" * 64)
                + b'"],"type":"layers"},"architecture":"amd64"}'
            )
        config_digest, config_size = _put_blob(layout, config_bytes)
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": config_size,
            },
            "layers": [],
        }
        manifest_bytes = _json_bytes(manifest)
        manifest_digest, manifest_size = _put_blob(layout, manifest_bytes)
        platform = {"os": "linux", "architecture": architecture}
        if variant:
            platform["variant"] = variant
        manifest_descriptors.append(
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": manifest_digest,
                "size": manifest_size,
                "platform": platform,
            }
        )
    nested_index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": manifest_descriptors,
    }
    nested_bytes = _json_bytes(nested_index)
    nested_digest, nested_size = _put_blob(layout, nested_bytes)
    top_index = {
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "digest": nested_digest,
                "size": nested_size,
                "annotations": {"org.opencontainers.image.ref.name": "proof"},
            }
        ],
    }
    (layout / "index.json").write_bytes(_json_bytes(top_index))


class OciReproducibilityTests(unittest.TestCase):
    def test_oci_reproducibility_workflow_is_fixed_isolated_and_nonpublishing(self) -> None:
        path = ROOT / ".github/workflows/oci-reproducibility.yml"
        workflow = yaml.safe_load(path.read_text())
        text = path.read_text()
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {"repository", "ref", "dockerfile_path", "build_context", "ci_run_id"},
        )
        for forbidden in ("command", "platform", "runner", "registry", "credential", "secret_name"):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(workflow["jobs"]["prove"]["runs-on"], "ubuntu-24.04")
        steps = workflow["jobs"]["prove"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        names = [step.get("name") for step in steps]
        build = by_name["Build two isolated dual-platform OCI layouts"]["run"]
        cleanup = by_name["Clean all run-owned OCI state"]["run"]
        finish = by_name["Finish Agent State run"]["with"]["status"]
        self.assertIn("for build_id in a b", build)
        self.assertIn("linux/amd64 linux/arm64/v8", build)
        self.assertIn('--root "${state_root}/graphroot"', build)
        self.assertIn('--runroot "${state_root}/runroot"', build)
        self.assertIn("--storage-driver vfs", build)
        self.assertIn('export TMPDIR="${platform_root}/tmp"', build)
        self.assertIn('export XDG_CACHE_HOME="${platform_root}/xdg-cache"', build)
        self.assertIn("--layers=false", build)
        self.assertIn("--pull=always", build)
        self.assertIn("--timestamp", build)
        self.assertIn("org.opencontainers.image.revision=${SOURCE_SHA}", build)
        self.assertIn('"oci:${layout_root}:proof"', build)
        self.assertIn("raw dual-platform OCI config identity", by_name["Compare raw dual-platform OCI config identity"]["run"])
        self.assertIn('unmount --all', cleanup)
        self.assertIn('rm -rf "${proof_root}"', cleanup)
        self.assertIn('docker image rm --force "${QEMU_IMAGE_ID}"', cleanup)
        self.assertIn('test ! -e "${proof_root}"', cleanup)
        self.assertIn("phase: observe-source", text)
        self.assertIn("job.workflow_repository", text)
        self.assertIn("job.workflow_sha", text)
        self.assertIn("docker/setup-qemu-action@v4", text)
        self.assertIn("cache-image: false", text)
        self.assertIn("actions/google-drive@main", text)
        self.assertIn("mime_type: text/plain", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("FORGEJO_", text)
        self.assertNotIn("actions/private-git", text)
        self.assertNotIn("docker://", text)
        self.assertNotIn("ghcr.io", text)
        self.assertNotIn("git.faruqi.dev", text)
        self.assertLess(names.index("Compare raw dual-platform OCI config identity"), names.index("Reverify exact requested source after both builds"))
        self.assertLess(names.index("Reverify exact requested source after both builds"), names.index("Clean all run-owned OCI state"))
        self.assertLess(names.index("Clean all run-owned OCI state"), names.index("Upload private reproducibility log to Google Drive"))
        self.assertLess(names.index("Upload private reproducibility log to Google Drive"), names.index("Finish Agent State run"))
        source_reverify = by_name["Reverify exact requested source after both builds"]["run"]
        self.assertIn('test "$(git -C source rev-parse HEAD)" = "${SOURCE_SHA}"', source_reverify)
        self.assertIn('git -C source status --porcelain=v1 --untracked-files=all', source_reverify)
        for required in (
            "steps.build.outcome == 'success'",
            "steps.compare.outcome == 'success'",
            "steps.source_reverify.outcome == 'success'",
            "steps.cleanup.outcome == 'success'",
            "steps.drive.outcome == 'success'",
        ):
            self.assertIn(required, finish)

    def test_oci_reproducibility_helper_accepts_identical_dual_platform_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            _write_test_oci_layout(left)
            _write_test_oci_layout(right)
            result = OCI_HELPER.compare_layouts(left, right)
            self.assertEqual(result["status"], "reproducible")
            self.assertEqual(result["platforms_expected"], ["linux/amd64", "linux/arm64/v8"])
            self.assertEqual(set(result["platforms"]), {"linux/amd64", "linux/arm64/v8"})
            for platform in result["platforms"].values():
                self.assertTrue(platform["config_bytes_identical"])
                self.assertTrue(platform["config_digest"].startswith("sha256:"))
                self.assertEqual(platform["config_digest"], platform["config_raw_sha256"])

    def test_oci_reproducibility_helper_rejects_raw_config_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            _write_test_oci_layout(left)
            _write_test_oci_layout(right, raw_config_variant=True)
            with self.assertRaisesRegex(OCI_HELPER.ReproducibilityError, "raw config bytes mismatch for linux/amd64"):
                OCI_HELPER.compare_layouts(left, right)

    def test_oci_reproducibility_helper_rejects_missing_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary) / "layout"
            _write_test_oci_layout(layout, missing_arm64=True)
            with self.assertRaisesRegex(OCI_HELPER.ReproducibilityError, "platform set mismatch"):
                OCI_HELPER.inspect_layout(layout)

    def test_oci_reproducibility_helper_rejects_descriptor_config_architecture_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary) / "layout"
            _write_test_oci_layout(layout, wrong_config_architecture=True)
            with self.assertRaisesRegex(
                OCI_HELPER.ReproducibilityError,
                "config linux/amd64 has wrong architecture",
            ):
                OCI_HELPER.inspect_layout(layout)


class ContainerServiceWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = ROOT / ".github/workflows/container-service.yml"
        self.workflow = yaml.safe_load(self.path.read_text())
        self.text = self.path.read_text()
        self.job = self.workflow["jobs"]["conformance"]
        self.steps = self.job["steps"]
        self.by_name = {step.get("name"): step for step in self.steps if step.get("name")}

    def test_container_service_workflow_is_small_fixed_and_nonpublishing(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {"repository", "ref", "service_port", "dockerfile_path", "build_context", "ci_run_id"},
        )
        self.assertEqual(inputs["service_port"]["type"], "number")
        self.assertTrue(inputs["service_port"]["required"])
        self.assertEqual(inputs["dockerfile_path"]["default"], "Dockerfile")
        self.assertEqual(inputs["build_context"]["default"], ".")
        self.assertEqual(self.workflow["env"]["PRODUCT_WRAPPER"], "scripts/ci/run-container-service-conformance.sh")
        self.assertEqual(self.job["runs-on"], "ubuntu-24.04")

        for forbidden_input in (
            "command",
            "script",
            "wrapper",
            "environment",
            "service_definition",
            "runner",
            "registry",
            "secret_name",
            "network",
        ):
            self.assertNotIn(forbidden_input, inputs)
        for forbidden_text in (
            "FORGEJO_",
            "git.faruqi.dev",
            "ghcr.io",
            "kubectl",
            "helm push",
            "docker push",
            "upload-artifact",
            "actions/private-git",
        ):
            self.assertNotIn(forbidden_text, self.text)
        self.assertIn('"docker-daemon:${service_tag}"', self.by_name["Build exact local service image"]["run"])
        self.assertNotIn("docker://", self.by_name["Build exact local service image"]["run"])

    def test_exact_source_image_postgres_wrapper_and_cleanup_wiring(self) -> None:
        names = [step.get("name") for step in self.steps]
        source = self.by_name["Resolve exact source and fixed conformance wrapper"]
        build = self.by_name["Build exact local service image"]
        postgres = self.by_name["Start isolated PostgreSQL"]
        prepare = self.by_name["Run fixed product prepare phase"]
        service = self.by_name["Start exact built service image"]
        readiness = self.by_name["Wait for fixed product readiness contract"]
        conformance = self.by_name["Run fixed external product conformance"]
        reverify = self.by_name["Reverify exact source and image identity"]
        cleanup = self.by_name["Clean all ephemeral service state"]
        scrub = self.by_name["Scrub configured CI secrets from private log"]
        drive = self.by_name["Upload private conformance log to Google Drive"]
        finish = self.by_name["Finish Agent State run"]

        self.assertIn('source_sha="$(git -C source rev-parse HEAD)"', source["run"])
        self.assertIn('git -C source ls-files --error-unmatch -- "${PRODUCT_WRAPPER}"', source["run"])
        self.assertIn('test -x "source/${PRODUCT_WRAPPER}"', source["run"])
        self.assertIn("wrapper.is_symlink()", source["run"])
        self.assertIn("org.opencontainers.image.revision=${SOURCE_SHA}", build["run"])
        self.assertIn('--root "${state_root}/graphroot"', build["run"])
        self.assertIn('--runroot "${state_root}/runroot"', build["run"])
        self.assertIn("--storage-driver vfs", build["run"])
        self.assertIn("--layers=false", build["run"])
        self.assertIn("--pull=always", build["run"])
        self.assertIn("docker image inspect", build["run"])
        self.assertIn("image_id=%s", build["run"])

        self.assertIn('docker network create "${SERVICE_NETWORK}"', postgres["run"])
        self.assertIn('docker volume create "${POSTGRES_VOLUME}"', postgres["run"])
        self.assertIn("postgres:17-alpine", self.workflow["env"]["POSTGRES_IMAGE"])
        self.assertIn("pg_isready", postgres["run"])
        self.assertIn("CIW_POSTGRES_URL=postgresql://ciw@127.0.0.1:", postgres["run"])
        self.assertIn("CIW_POSTGRES_NETWORK_URL=postgresql://ciw@postgres:5432/ciw", postgres["run"])

        expected_image = "${{ steps.image.outputs.image_id }}"
        self.assertEqual(prepare["env"]["CIW_CONTAINER_CONFORMANCE_PHASE"], "prepare")
        self.assertEqual(readiness["env"]["CIW_CONTAINER_CONFORMANCE_PHASE"], "ready")
        self.assertEqual(conformance["env"]["CIW_CONTAINER_CONFORMANCE_PHASE"], "conformance")
        for step in (prepare, readiness, conformance):
            self.assertEqual(step["env"]["CIW_SERVICE_IMAGE"], expected_image)
            self.assertIn('"./${PRODUCT_WRAPPER}"', step["run"])
        self.assertEqual(service["env"]["SERVICE_IMAGE_ID"], expected_image)
        self.assertIn('"${SERVICE_IMAGE_ID}" >/dev/null', service["run"])
        self.assertIn("CIW_SERVICE_URL=http://127.0.0.1:", service["run"])
        self.assertIn("docker inspect --format '{{.Image}}'", reverify["run"])
        self.assertIn("org.opencontainers.image.revision", reverify["run"])

        self.assertEqual(cleanup["if"], "always()")
        for value in (
            'docker rm -f "${SERVICE_CONTAINER',
            'docker rm -f "${POSTGRES_CONTAINER',
            "docker volume rm -f",
            "docker network rm",
            "docker image rm --force",
            'rm -rf "${runtime_root}"',
            "Verified zero run-owned container, network, volume, image, build and private-file residue",
        ):
            self.assertIn(value, cleanup["run"])
        self.assertLess(names.index("Run fixed external product conformance"), names.index("Reverify exact source and image identity"))
        self.assertLess(names.index("Reverify exact source and image identity"), names.index("Clean all ephemeral service state"))
        self.assertLess(names.index("Clean all ephemeral service state"), names.index("Scrub configured CI secrets from private log"))
        self.assertLess(names.index("Scrub configured CI secrets from private log"), names.index("Upload private conformance log to Google Drive"))
        self.assertLess(names.index("Upload private conformance log to Google Drive"), names.index("Finish Agent State run"))
        self.assertEqual(drive["with"]["mime_type"], "text/plain")
        self.assertEqual(drive["with"]["file_name"], "${{ github.run_id }}-${{ github.run_attempt }}.txt")
        self.assertEqual(scrub["if"], "always()")
        self.assertEqual(finish["if"], "always()")
        status = finish["with"]["status"]
        for step_id in (
            "source_identity",
            "observe_source",
            "toolchain",
            "image",
            "postgres",
            "prepare",
            "service",
            "readiness",
            "conformance",
            "reverify",
            "cleanup",
            "scrub",
            "drive",
        ):
            self.assertIn(f"steps.{step_id}.outcome == 'success'", status)

    def test_fixed_wrapper_validation_executes_fail_closed(self) -> None:
        script = self.by_name["Resolve exact source and fixed conformance wrapper"]["run"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            (source / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            wrapper = source / "scripts/ci/run-container-service-conformance.sh"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            subprocess.run(["git", "add", "Dockerfile", "scripts/ci/run-container-service-conformance.sh"], cwd=source, check=True)
            subprocess.run(["git", "-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "fixture"], cwd=source, check=True)
            output = root / "output"
            github_env = root / "github-env"
            log = root / "log"
            base_env = {
                **os.environ,
                "DOCKERFILE_PATH": "Dockerfile",
                "BUILD_CONTEXT": ".",
                "SERVICE_PORT": "8080",
                "PRODUCT_WRAPPER": "scripts/ci/run-container-service-conformance.sh",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_ENV": str(github_env),
                "CI_LOG": str(log),
            }
            valid = subprocess.run(["bash", "-c", script], cwd=root, env=base_env, capture_output=True, text=True)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn("source_sha=", output.read_text())

            wrapper.chmod(0o644)
            subprocess.run(["git", "add", "scripts/ci/run-container-service-conformance.sh"], cwd=source, check=True)
            subprocess.run(["git", "-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "non-executable"], cwd=source, check=True)
            non_executable = subprocess.run(["bash", "-c", script], cwd=root, env=base_env, capture_output=True, text=True)
            self.assertNotEqual(non_executable.returncode, 0)

            wrapper.chmod(0o755)
            subprocess.run(["git", "add", "scripts/ci/run-container-service-conformance.sh"], cwd=source, check=True)
            subprocess.run(["git", "-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "executable"], cwd=source, check=True)
            subprocess.run(["git", "rm", "--cached", "scripts/ci/run-container-service-conformance.sh"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "untrack-wrapper"], cwd=source, check=True)
            untracked = subprocess.run(["bash", "-c", script], cwd=root, env=base_env, capture_output=True, text=True)
            self.assertNotEqual(untracked.returncode, 0)

    def test_dispatch_admits_only_bounded_container_service_profile(self) -> None:
        dispatch_path = ROOT / ".github/workflows/central-ci-dispatch.yml"
        dispatch = yaml.safe_load(dispatch_path.read_text())
        validation = next(
            step for step in dispatch["jobs"]["request"]["steps"]
            if step.get("name") == "Validate container service request"
        )
        job = dispatch["jobs"]["container_service"]
        self.assertEqual(validation["if"], "${{ steps.claim.outputs.workflow_key == 'validation.container-service' }}")
        self.assertEqual(job["if"], "${{ needs.request.outputs.workflow_key == 'validation.container-service' && needs.request.outputs.test_profile == 'conformance' }}")
        self.assertEqual(job["uses"], "./.github/workflows/container-service.yml")
        self.assertEqual(
            set(job["with"]),
            {"repository", "ref", "service_port", "dockerfile_path", "build_context", "ci_run_id"},
        )
        self.assertTrue(job["concurrency"]["cancel-in-progress"])
        self.assertEqual(job["concurrency"]["group"], "central-ci-${{ inputs.active_key }}")

        script = validation["run"]
        def run(profile: str, inputs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "TEST_PROFILE": profile,
                    "INPUTS_JSON": json.dumps(inputs),
                },
                capture_output=True,
                text=True,
            )

        self.assertEqual(run("conformance", {"service_port": 8080}).returncode, 0)
        self.assertEqual(
            run(
                "conformance",
                {"service_port": 80, "dockerfile_path": "docker/Dockerfile", "build_context": "."},
            ).returncode,
            0,
        )
        for profile, inputs in (
            ("build", {"service_port": 8080}),
            ("conformance", {}),
            ("conformance", {"service_port": True}),
            ("conformance", {"service_port": 0}),
            ("conformance", {"service_port": 65536}),
            ("conformance", {"service_port": 8080, "command": "pytest"}),
            ("conformance", {"service_port": 8080, "dockerfile_path": ""}),
        ):
            self.assertNotEqual(run(profile, inputs).returncode, 0)


if __name__ == "__main__":
    unittest.main()
