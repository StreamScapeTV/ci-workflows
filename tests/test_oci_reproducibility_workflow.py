from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import oci_reproducibility as repro


WORKFLOW = ROOT / ".github" / "workflows" / "reusable-oci-reproducibility.yml"
SMOKE = ROOT / ".github" / "workflows" / "oci-reproducibility-validation.yml"
SCRIPT = ROOT / "scripts" / "ci" / "oci_reproducibility.py"
MODULE = ROOT / "src" / "ci_workflows" / "oci_reproducibility.py"
FIXTURE = ROOT / "tests" / "fixtures" / "oci-reproducibility"


class OciReproducibilityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.smoke = SMOKE.read_text(encoding="utf-8")
        cls.script = SCRIPT.read_text(encoding="utf-8")
        cls.module = MODULE.read_text(encoding="utf-8")

    def test_public_surface_is_bounded_product_neutral_and_non_publishing(self) -> None:
        text = self.workflow
        self.assertIn("workflow_call:", text)
        self.assertIn("runs-on: [linux, amd64, buildah, small]", text)
        self.assertIn("permissions:\n  contents: read", text)
        public = text.split("permissions:", 1)[0]
        self.assertEqual(1, public.count("admitted_sha:"))
        self.assertEqual(1, public.count("dockerfile_path:"))
        self.assertEqual(1, public.count("build_context:"))
        for forbidden in (
            "runner:",
            "runs_on:",
            "runner_labels:",
            "platform:",
            "platforms:",
            "engine:",
            "command:",
            "registry:",
            "registry_username:",
            "registry_token:",
            "secrets:",
            "product_id",
            "iptv-backend",
        ):
            self.assertNotIn(forbidden, public)
        for forbidden in (
            "upload-artifact",
            "download-artifact",
            "packages: write",
            "id-token: write",
            "docker login",
            "buildah login",
            "skopeo copy docker://",
            "kubectl",
            "flux reconcile",
            ":latest",
        ):
            self.assertNotIn(forbidden, text)

    def test_real_smoke_is_path_scoped_for_push_and_pull_request(self) -> None:
        required_paths = (
            ".github/workflows/reusable-oci-reproducibility.yml",
            ".github/workflows/oci-reproducibility-validation.yml",
            "contracts/public-workflows.json",
            "contracts/public-workflows/products.json",
            "contracts/runner-profiles.json",
            "src/ci_workflows/oci_reproducibility.py",
            "src/ci_workflows/public_api_contract.py",
            "tests/test_oci_reproducibility_workflow.py",
        )
        self.assertIn("pull_request:\n    paths:\n", self.smoke)
        self.assertIn("push:\n", self.smoke)
        self.assertIn("push:\n    # Keep the real two-clean-build proof", self.smoke)
        for path in required_paths:
            self.assertEqual(2, self.smoke.count(f"      - {path}\n"), path)
        self.assertIn("github.event.pull_request.user.login == 'mimranfaruqi'", self.smoke)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.smoke)

    def test_function_first_module_and_thin_cli_adapter_are_explicit(self) -> None:
        self.assertIn("from ci_workflows.oci_reproducibility import main", self.script)
        self.assertIn("raise SystemExit(main())", self.script)
        for implementation_token in (
            "class ReproducibilityError",
            "class ProofInputs",
            "def build_command",
            "def raw_config_identity",
            "def compare_builds",
            "def run_proof",
        ):
            self.assertNotIn(implementation_token, self.script)
            self.assertIn(implementation_token, self.module)
        self.assertIn("PYTHONPATH: .ciw/src", self.workflow)
        self.assertIn('PYTHONDONTWRITEBYTECODE: "1"', self.workflow)

    def test_exact_source_and_fixed_platform_contract_are_explicit(self) -> None:
        text = self.workflow
        self.assertIn("Check out exact admitted caller source", text)
        self.assertIn("uses: ./.ciw/actions/exact-checkout", text)
        self.assertIn("Reverify exact caller source after both builds", text)
        self.assertEqual(
            (("linux/amd64", "amd64"), ("linux/arm64/v8", "arm64")),
            repro.REQUIRED_PLATFORMS,
        )
        self.assertIn("command -v qemu-aarch64-static", text)
        self.assertNotIn("matrix:", text)

    def test_build_command_fixes_source_timestamp_metadata_and_disables_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            dockerfile = source / "Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            inputs = repro.ProofInputs(
                source_root=source,
                admitted_sha="a" * 40,
                dockerfile=dockerfile,
                context=source,
                source_date_epoch=1_700_000_000,
                source_created="2023-11-14T22:13:20Z",
                state_root=root / "state",
            )
            command = repro.build_command(
                inputs,
                root / "state" / "build-a",
                "linux/arm64/v8",
                "localhost/proof:candidate",
            )
        rendered = " ".join(command)
        self.assertIn("--storage-driver vfs", rendered)
        self.assertIn("--layers=false", rendered)
        self.assertIn("--no-cache", rendered)
        self.assertIn("--http-proxy=false", rendered)
        self.assertIn("--identity-label=false", rendered)
        self.assertIn("--timestamp 1700000000", rendered)
        self.assertIn("--platform linux/arm64/v8", rendered)
        self.assertIn("org.opencontainers.image.revision=" + "a" * 40, rendered)
        self.assertIn("org.opencontainers.image.created=2023-11-14T22:13:20Z", rendered)
        self.assertIn("SOURCE_DATE_EPOCH=1700000000", rendered)

    def test_raw_config_identity_hashes_referenced_blob_not_manifest_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            raw_config = json.dumps(
                {"architecture": "amd64", "os": "linux", "rootfs": {"type": "layers", "diff_ids": []}},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            digest = "sha256:" + hashlib.sha256(raw_config).hexdigest()
            blob = layout / "blobs" / "sha256" / digest.split(":", 1)[1]
            blob.parent.mkdir(parents=True)
            blob.write_bytes(raw_config)
            manifest = json.dumps(
                {"schemaVersion": 2, "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": digest, "size": len(raw_config)}, "layers": []},
                separators=(",", ":"),
            )
            completed = mock.Mock(returncode=0, stdout=manifest, stderr="")
            with mock.patch.object(repro, "_run", return_value=completed):
                actual_digest, actual_raw = repro.raw_config_identity(layout, "amd64")
        self.assertEqual(digest, actual_digest)
        self.assertEqual(raw_config, actual_raw)
        self.assertNotEqual(hashlib.sha256(manifest.encode("utf-8")).hexdigest(), digest.split(":", 1)[1])

    def test_changed_raw_config_fails_closed(self) -> None:
        required = {platform for platform, _ in repro.REQUIRED_PLATFORMS}
        first = {platform: ("sha256:" + "1" * 64, b"same") for platform in required}
        second = dict(first)
        second["linux/arm64/v8"] = ("sha256:" + "2" * 64, b"changed")
        with self.assertRaisesRegex(repro.ReproducibilityError, "raw OCI config drift"):
            repro.compare_builds(first, second)

    def test_missing_extra_or_wrong_platform_set_fails_closed(self) -> None:
        item = ("sha256:" + "1" * 64, b"same")
        valid = {platform: item for platform, _ in repro.REQUIRED_PLATFORMS}
        for bad in (
            {"linux/amd64": item},
            {**valid, "linux/ppc64le": item},
            {"linux/amd64": item, "linux/arm64": item},
        ):
            with self.subTest(platforms=sorted(bad)):
                with self.assertRaises(repro.ReproducibilityError):
                    repro.compare_builds(bad, valid)

    def test_build_a_is_deleted_before_build_b_and_cleanup_is_terminal(self) -> None:
        self.assertIn('first = _one_clean_build(inputs, "build-a")', self.module)
        self.assertIn('not (inputs.state_root / "build-a").exists()', self.module)
        self.assertIn('second = _one_clean_build(inputs, "build-b")', self.module)
        self.assertLess(
            self.module.index('first = _one_clean_build(inputs, "build-a")'),
            self.module.index('second = _one_clean_build(inputs, "build-b")'),
        )
        self.assertIn("finally:\n        cleanup_state(inputs.state_root)", self.module)
        self.assertIn("buildah", self.module)
        for token in ("unmount", "rm", "rmi", "graphroot", "runroot", "cache", "runtime", "layouts"):
            self.assertIn(token, self.module)
        self.assertIn("Remove all run-owned OCI reproducibility state", self.workflow)
        self.assertIn("Verify zero routine Actions artifact behavior", self.workflow)

    def test_bounded_paths_reject_absolute_parent_and_control_characters(self) -> None:
        for value in ("/Dockerfile", "../Dockerfile", "a/../../Dockerfile", "Dockerfile\nother"):
            with self.subTest(value=value):
                with self.assertRaises(repro.ReproducibilityError):
                    repro._safe_relative(value, "path")
        self.assertEqual("Dockerfile", str(repro._safe_relative("Dockerfile", "path")))
        self.assertEqual(".", str(repro._safe_relative(".", "path")))

    def test_isolated_environment_drops_credential_like_inherited_variables(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.dict(
                repro.os.environ,
                {
                    "GITHUB_TOKEN": "sensitive",
                    "REGISTRY_PASSWORD": "sensitive",
                    "SAFE_TEST_VALUE": "keep",
                },
                clear=True,
            ):
                isolated = repro._isolated_environment(root)
        self.assertNotIn("GITHUB_TOKEN", isolated)
        self.assertNotIn("REGISTRY_PASSWORD", isolated)
        self.assertEqual("keep", isolated["SAFE_TEST_VALUE"])
        self.assertEqual(str(root / "home"), isolated["HOME"])
        self.assertEqual(str(root / "auth" / "auth.json"), isolated["REGISTRY_AUTH_FILE"])

    def test_deterministic_fixture_is_scratch_only_and_has_no_network_or_clock_input(self) -> None:
        dockerfile = (FIXTURE / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM scratch", dockerfile)
        self.assertIn("ARG SOURCE_DATE_EPOCH", dockerfile)
        self.assertIn("COPY payload.txt /payload.txt", dockerfile)
        for forbidden in ("RUN ", "ADD http", "curl", "wget", "date ", "latest"):
            self.assertNotIn(forbidden, dockerfile)
        self.assertEqual(
            "central-oci-reproducibility-fixture\n",
            (FIXTURE / "payload.txt").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
