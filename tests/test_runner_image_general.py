from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/general/Dockerfile"
PREPARER = ROOT / "runner-images/general/prepare_inputs.py"
PRODUCT = ROOT / "runner-images/general/product.json"
SMOKE = ROOT / "runner-images/general/smoke.sh"
TOOLCHAIN = ROOT / "runner-images/general/toolchain.lock.json"
ZIP_TOOL = ROOT / "runner-images/general/zip.py"
UNZIP_TOOL = ROOT / "runner-images/general/unzip.py"
RETIRED_INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-general-linux-amd64.json"


def _load_preparer(path: Path = PREPARER):
    spec = importlib.util.spec_from_file_location("runner_general_prepare_inputs", path)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load runner-general input preparer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_general_runner_uses_pinned_debian_only_stages() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 3
    assert all("docker.io/library/" in line and "@sha256:" in line for line in from_lines)
    assert "buildpack-deps@sha256:" in from_lines[0]
    assert "node@sha256:" in from_lines[1]
    assert "python@sha256:" in from_lines[2]
    assert "buildpack-deps:trixie@" not in source
    assert "node:24.19.0-trixie-slim@" not in source
    assert "python:3.12.14-slim-trixie@" not in source
    assert "# syntax=" not in source.lower()
    assert "ghcr.io/actions/actions-runner" not in source
    assert "ubuntu" not in "\n".join(from_lines).lower()
    assert "git.faruqi.dev" not in source
    assert source.rstrip().endswith("CMD []")


def test_general_runner_build_is_networkless_and_engine_free() -> None:
    raw = DOCKERFILE.read_text(encoding="utf-8")
    source = raw.lower()
    for token in (
        "apt-get update",
        "apt-get install",
        "apt update",
        "apt install",
        "apk add",
        "dnf install",
        "curl http",
        "wget http",
        "git clone",
    ):
        assert token not in source
    assert ".ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz" in source
    assert ".ciw-build-inputs/node-26.7.0-linux-x64.tar.gz" in source
    assert "rm -f" in source and "/usr/bin/apt-get" in source
    assert "for forbidden in docker dockerd containerd ctr runc buildah podman skopeo sudo" in source
    assert "for path in /usr/bin/bash" in source
    assert "for path in /bin/bash" not in source
    assert "/usr/bin/unzip" not in source
    assert "copy --chmod=0755 unzip.py /usr/local/bin/unzip" in source
    assert "cp --parents -a /usr/lib/git-core /out" in source
    assert "cp --parents -a /usr/share/git-core /out" in source
    assert raw.count('library_dir="$(readlink -f "$(dirname "${library}")")"') == 2
    assert raw.count('cp -pl "${library}"') == 0
    assert raw.count('cp -pL "${library}"') == 2
    assert "ldconfig -p" in source
    assert 'libatomic.so.1" {print $nf; exit}' in source
    assert "/out/usr/lib/x86_64-linux-gnu/libatomic.so.1" in source
    assert "/node-root/usr/lib/x86_64-linux-gnu/libatomic.so.1" not in source
    assert "for library in /usr/lib/x86_64-linux-gnu/libatomic.so.1*" not in source
    assert "--mount=type=bind,from=build-tools,source=/node26-compat,target=/tmp/node26-compat,ro" in source
    assert "/tmp/node26-compat/bin/node --version | grep -fx 'v26.7.0'" in source
    assert "/tmp/node26-compat/lib/node_modules/npm/bin/npm-cli.js" in source


def test_general_runner_preparer_derives_exact_reviewed_inputs() -> None:
    assert not RETIRED_INPUT_LOCK.exists()
    module = _load_preparer()
    lock = module._load_toolchain()
    assets = module._inputs(lock)
    expected = {
        "actions-runner-linux-x64-2.336.0.tar.gz": (
            "https://github.com/actions/runner/releases/download/v2.336.0/"
            "actions-runner-linux-x64-2.336.0.tar.gz",
            "04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d",
            268_435_456,
        ),
        "node-26.7.0-linux-x64.tar.gz": (
            "https://github.com/actions/node-versions/releases/download/"
            "26.7.0-31064755789/node-26.7.0-linux-x64.tar.gz",
            "e1ab5849f548df59f394a978a04dbbfae915c3b35ac6597d829c53dada7ed701",
            83_886_080,
        ),
        "zstd-1.5.7.tar.gz": (
            "https://github.com/facebook/zstd/releases/download/v1.5.7/zstd-1.5.7.tar.gz",
            "eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3",
            4_194_304,
        ),
        "jq-1.8.2-linux-amd64": (
            "https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64",
            "b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f",
            4_194_304,
        ),
        "yq-4.53.3-linux-amd64": (
            "https://github.com/mikefarah/yq/releases/download/v4.53.3/yq_linux_amd64",
            "fa52a4e758c63d38299163fbdd1edfb4c4963247918bf9c1c5d31d84789eded4",
            20_971_520,
        ),
        "kubectl-1.36.2-linux-amd64": (
            "https://dl.k8s.io/release/v1.36.2/bin/linux/amd64/kubectl",
            "1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82",
            104_857_600,
        ),
        "helm-4.2.4-linux-amd64.tar.gz": (
            "https://get.helm.sh/helm-v4.2.4-linux-amd64.tar.gz",
            "c306b46f719b0a4da32d0f78ee21bf90ce8d602f15b22ab753f0674d1670a7f3",
            67_108_864,
        ),
        "kustomize-5.8.1-linux-amd64.tar.gz": (
            "https://github.com/kubernetes-sigs/kustomize/releases/download/"
            "kustomize%2Fv5.8.1/kustomize_v5.8.1_linux_amd64.tar.gz",
            "029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d",
            16_777_216,
        ),
    }
    assert {
        item.filename: (item.url, item.sha256, item.maximum_bytes)
        for item in assets
    } == expected

    source = PREPARER.read_text(encoding="utf-8")
    assert "urllib.request.Request" in source
    assert "hashlib.sha256" in source
    assert "Content-Length" in source
    assert "O_NOFOLLOW" in source
    assert "os.replace" in source
    assert "shutil.rmtree" in source
    assert "subprocess" not in source
    assert "os.environ" not in source
    assert "sys.argv" not in source


def test_general_runner_toolchain_is_release_readable() -> None:
    toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    assert toolchain["policy"]["final_distribution"] == "debian-trixie"
    assert toolchain["policy"]["release_authority"] == "ci-workflows-git-tag"
    assert toolchain["toolchain"] == {
        "actions_runner": "2.336.0",
        "python": "3.12.14",
        "node": "24.19.0",
        "jq": "1.8.2",
        "yq": "4.53.3",
        "zstd": "1.5.7",
        "kubectl": "1.36.2",
        "helm": "4.2.4",
        "kustomize": "5.8.1",
    }
    assert toolchain["compatibility_probes"] == {
        "setup_node_linux_x64": "26.7.0",
        "setup_node_linux_x64_release": "31064755789",
    }
    assert set(toolchain["oci_stages"]) == {
        "build_tools",
        "node_runtime",
        "final_python_debian",
    }
    assert toolchain["oci_stages"]["build_tools"]["reference"] == (
        "docker.io/library/buildpack-deps:trixie"
    )
    assert all(
        value.startswith("sha256:")
        for value in toolchain["external_assets"].values()
    )


def test_general_runner_smoke_proves_runtime_and_trust_boundary() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product == {
        "schema_version": 1,
        "product_id": "runner-general",
        "image_repository": "git.faruqi.dev/mimranfaruqi/github-actions-runner-general",
        "platform": "linux/amd64",
        "dockerfile": "runner-images/general/Dockerfile",
        "smoke": "runner-images/general/smoke.sh",
    }

    smoke = SMOKE.read_text(encoding="utf-8")
    for token in (
        "ID=debian",
        "VERSION_CODENAME=trixie",
        "bash -n /home/runner/run.sh",
        "/home/runner/bin/Runner.Listener --version",
        "libatomic.so.1",
        "/etc/ssl/certs/ca-certificates.crt",
        "BEGIN CERTIFICATE",
        "Python 3.12.14",
        "python3 -m venv",
        "v24.19.0",
        "git-remote-https",
        "git init -q",
        "jq-1.8.2",
        "v4.53.3",
        "v1.5.7",
        "v4.2.4",
        "v5.8.1",
        "v1.36.2",
        "/home/runner/.kube",
        "/var/run/secrets/kubernetes.io/serviceaccount/token",
    ):
        assert token in smoke
    for forbidden in (
        "docker",
        "dockerd",
        "containerd",
        "runc",
        "buildah",
        "podman",
        "skopeo",
        "sudo",
        "apt-get",
        "dpkg",
    ):
        assert forbidden in smoke


def test_general_runner_archive_compatibility_tools_are_bounded() -> None:
    zip_source = ZIP_TOOL.read_text(encoding="utf-8")
    assert "zipfile.ZipFile" in zip_source
    assert "source.rglob" in zip_source
    assert "subprocess" not in zip_source
    assert "urllib" not in zip_source

    unzip_source = UNZIP_TOOL.read_text(encoding="utf-8")
    assert "zipfile.ZipFile" in unzip_source
    assert "safe_destination" in unzip_source
    assert 'option == "o"' in unzip_source
    assert 'argument == "-d"' in unzip_source
    assert "subprocess" not in unzip_source
    assert "urllib" not in unzip_source


class _Response(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class GeneralRunnerInputPreparationTests(unittest.TestCase):
    ASSET_KEYS = (
        "actions_runner",
        "node_26_compat",
        "zstd_source",
        "jq",
        "yq",
        "kubectl",
        "helm",
        "kustomize",
    )

    def _fixture(self, root: Path):
        preparer = root / "prepare_inputs.py"
        preparer.write_text(PREPARER.read_text(encoding="utf-8"), encoding="utf-8")
        lock = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
        (root / "toolchain.lock.json").write_text(
            json.dumps(lock, indent=2) + "\n",
            encoding="utf-8",
        )
        module = _load_preparer(preparer)
        initial = module._inputs(module._load_toolchain())
        payloads = {
            asset.filename: f"payload:{asset.filename}\n".encode()
            for asset in initial
        }
        for key, asset in zip(self.ASSET_KEYS, initial, strict=True):
            lock["external_assets"][key] = (
                "sha256:" + hashlib.sha256(payloads[asset.filename]).hexdigest()
            )
        (root / "toolchain.lock.json").write_text(
            json.dumps(lock, indent=2) + "\n",
            encoding="utf-8",
        )
        assets = module._inputs(module._load_toolchain())
        return module, assets, payloads

    def test_verified_inputs_are_installed_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module, assets, payloads = self._fixture(root)
            by_url = {asset.url: payloads[asset.filename] for asset in assets}

            def open_url(request, *, timeout):
                self.assertEqual(timeout, module._TIMEOUT_SECONDS)
                self.assertEqual(request.get_header("User-agent"), module._USER_AGENT)
                return _Response(by_url[request.full_url])

            with mock.patch.object(module.urllib.request, "urlopen", side_effect=open_url):
                filenames = module.prepare()

            self.assertEqual(filenames, tuple(asset.filename for asset in assets))
            self.assertFalse(any(path.name.endswith(".partial") for path in module.DESTINATION.iterdir()))
            for asset in assets:
                target = module.DESTINATION / asset.filename
                self.assertEqual(target.read_bytes(), payloads[asset.filename])
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_checksum_failure_removes_all_owned_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module, assets, _payloads = self._fixture(root)
            by_url = {asset.url: b"wrong bytes" for asset in assets}

            with mock.patch.object(
                module.urllib.request,
                "urlopen",
                side_effect=lambda request, *, timeout: _Response(by_url[request.full_url]),
            ):
                with self.assertRaises(module.InputPreparationError):
                    module.prepare()

            self.assertFalse(module.DESTINATION.exists())
            self.assertFalse(module.DESTINATION.is_symlink())

    def test_preexisting_or_oversized_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module, assets, payloads = self._fixture(root)
            module.DESTINATION.mkdir()
            with self.assertRaises(module.InputPreparationError):
                module.prepare()
            module.DESTINATION.rmdir()

            first = assets[0]

            class Oversized(_Response):
                def __init__(self) -> None:
                    super().__init__(payloads[first.filename])
                    self.headers = {
                        "Content-Length": str(first.maximum_bytes + 1)
                    }

            with mock.patch.object(
                module.urllib.request,
                "urlopen",
                side_effect=lambda _request, *, timeout: Oversized(),
            ):
                with self.assertRaises(module.InputPreparationError):
                    module.prepare()
            self.assertFalse(module.DESTINATION.exists())


class GeneralRunnerStaticContractTests(unittest.TestCase):
    def test_pinned_debian_stages(self) -> None:
        test_general_runner_uses_pinned_debian_only_stages()

    def test_networkless_engine_free_build(self) -> None:
        test_general_runner_build_is_networkless_and_engine_free()

    def test_exact_prepared_inputs(self) -> None:
        test_general_runner_preparer_derives_exact_reviewed_inputs()

    def test_release_readable_toolchain(self) -> None:
        test_general_runner_toolchain_is_release_readable()

    def test_runtime_and_trust_boundary(self) -> None:
        test_general_runner_smoke_proves_runtime_and_trust_boundary()

    def test_archive_compatibility_tools(self) -> None:
        test_general_runner_archive_compatibility_tools_are_bounded()


if __name__ == "__main__":
    unittest.main()
