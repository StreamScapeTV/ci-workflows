from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts.ci import swiftpm_binary

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/swiftpm_binary.py"


class SwiftPMBinaryHelperTests(unittest.TestCase):
    def run_helper(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
        )

    def make_source(self, root: Path, *, version: str = "3.4.5") -> tuple[Path, str]:
        source = root / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
        (source / "VERSION").write_text(version + "\n", encoding="utf-8")
        (source / "Package.swift").write_text("// swift-tools-version: 5.10\n", encoding="utf-8")
        wrapper = source / "scripts/ci/run-swiftpm-binary.sh"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "--all"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "test"], check=True)
        sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        return source, sha

    def fake_swift_env(self, root: Path, *, version: str = "3.4.5", checksum: str = "a" * 64) -> dict[str, str]:
        binary_url = f"https://git.faruqi.dev/api/packages/mimranfaruqi/generic/example-apple/{version}/Example.xcframework.zip"
        dump = {
            "products": [{"name": "Example", "type": {"library": ["automatic"]}, "targets": ["Example"]}],
            "targets": [
                {"name": "Example", "type": "binary", "url": binary_url, "checksum": checksum},
            ],
        }
        bin_dir = root / "bin"
        bin_dir.mkdir()
        swift = bin_dir / "swift"
        swift.write_text(
            "#!/bin/sh\n"
            "test \"$1\" = package && test \"$2\" = dump-package || exit 9\n"
            f"cat <<'JSON'\n{json.dumps(dump)}\nJSON\n",
            encoding="utf-8",
        )
        swift.chmod(0o755)
        return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    def test_source_contract_uses_version_github_url_and_remote_binary_targets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, sha = self.make_source(root)
            output = root / "contract.json"
            result = self.run_helper(
                "source-contract",
                "--root", str(source),
                "--repository", "StreamScapeTV/example-swift",
                "--version", "3.4.5",
                "--source-sha", sha,
                "--output", str(output),
                env=self.fake_swift_env(root),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["packageURL"], "https://github.com/StreamScapeTV/example-swift.git")
            self.assertEqual(value["version"], "3.4.5")
            self.assertEqual(value["packageRevision"], sha)
            self.assertEqual(value["libraryProducts"], ["Example"])
            self.assertEqual(value["binaryTargets"][0]["target"], "Example")
            self.assertIn("/3.4.5/Example.xcframework.zip", value["binaryTargets"][0]["url"])

    def test_source_contract_rejects_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, sha = self.make_source(root, version="3.4.4")
            result = self.run_helper(
                "source-contract",
                "--root", str(source),
                "--repository", "StreamScapeTV/example-swift",
                "--version", "3.4.5",
                "--source-sha", sha,
                "--output", str(root / "contract.json"),
                env=self.fake_swift_env(root),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("VERSION must exactly match", result.stderr)

    def test_validate_plan_requires_exact_package_manifest_cohort_and_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence = root / "evidence"
            artifact_dir = evidence / "artifacts"
            artifact_dir.mkdir(parents=True)
            artifact = artifact_dir / "Example.xcframework.zip"
            artifact.write_bytes(b"exact-xcframework-archive")
            checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
            url = "https://git.faruqi.dev/api/packages/mimranfaruqi/generic/example-apple/3.4.5/Example.xcframework.zip"
            contract = {
                "schemaVersion": 1,
                "repository": "StreamScapeTV/example-swift",
                "packageURL": "https://github.com/StreamScapeTV/example-swift.git",
                "version": "3.4.5",
                "packageRevision": "b" * 40,
                "binaryTargets": [{"target": "Example", "url": url, "checksum": checksum, "filename": artifact.name}],
                "libraryProducts": ["Example"],
            }
            plan = {
                "schemaVersion": 1,
                "packageURL": contract["packageURL"],
                "version": "3.4.5",
                "artifacts": [
                    {"target": "Example", "file": "artifacts/Example.xcframework.zip", "url": url, "checksum": checksum}
                ],
            }
            contract_path = root / "contract.json"
            plan_path = root / "plan.json"
            output = root / "normalized.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = self.run_helper(
                "validate-plan",
                "--contract", str(contract_path),
                "--plan", str(plan_path),
                "--evidence-dir", str(evidence),
                "--output", str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            normalized = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(normalized["packageRevision"], "b" * 40)
            self.assertEqual(normalized["artifacts"][0]["size"], len(artifact.read_bytes()))

            plan["artifacts"][0]["checksum"] = "0" * 64
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            rejected = self.run_helper(
                "validate-plan",
                "--contract", str(contract_path),
                "--plan", str(plan_path),
                "--evidence-dir", str(evidence),
                "--output", str(output),
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("does not match Package.swift", rejected.stderr)

    def test_validate_plan_rejects_non_registry_or_wrong_version_urls(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence = root / "evidence"
            artifact_dir = evidence / "artifacts"
            artifact_dir.mkdir(parents=True)
            artifact = artifact_dir / "Example.xcframework.zip"
            artifact.write_bytes(b"artifact")
            checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
            bad_url = "https://example.invalid/api/packages/mimranfaruqi/generic/example-apple/3.4.5/Example.xcframework.zip"
            contract = {
                "schemaVersion": 1,
                "repository": "StreamScapeTV/example-swift",
                "packageURL": "https://github.com/StreamScapeTV/example-swift.git",
                "version": "3.4.5",
                "packageRevision": "b" * 40,
                "binaryTargets": [{"target": "Example", "url": bad_url, "checksum": checksum, "filename": artifact.name}],
                "libraryProducts": ["Example"],
            }
            plan = {
                "schemaVersion": 1,
                "packageURL": contract["packageURL"],
                "version": "3.4.5",
                "artifacts": [
                    {"target": "Example", "file": "artifacts/Example.xcframework.zip", "url": bad_url, "checksum": checksum}
                ],
            }
            contract_path = root / "contract.json"
            plan_path = root / "plan.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = self.run_helper(
                "validate-plan",
                "--contract", str(contract_path),
                "--plan", str(plan_path),
                "--evidence-dir", str(evidence),
                "--output", str(root / "normalized.json"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("approved private Generic Package host", result.stderr)


    def test_trusted_repository_policy_selects_credential_free_only_for_valid_grant_without_netrc(self) -> None:
        repository = "StreamScapeTV/example-swift"
        base = {
            "schemaVersion": 3,
            "repository": repository,
            "capabilities": [],
            "hostPolicy": {"operatingSystems": {}},
        }
        self.assertEqual(
            swiftpm_binary.repository_registry_read_policy({"repository_ci": base}, repository),
            "credential-free",
        )
        strict = {**base, "capabilities": ["registry_netrc"]}
        self.assertEqual(
            swiftpm_binary.repository_registry_read_policy({"repository_ci": strict}, repository),
            "authenticated",
        )
        for invalid in (
            {},
            {"repository_ci": {**base, "repository": "StreamScapeTV/other"}},
            {"repository_ci": {**base, "capabilities": ["not-a-capability"]}},
            {"repository_ci": {**base, "capabilities": ["registry_netrc", "registry_netrc"]}},
            {"repository_ci": {**base, "hostPolicy": "invalid"}},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(swiftpm_binary.ContractError):
                    swiftpm_binary.repository_registry_read_policy(invalid, repository)

    def test_resolve_read_policy_command_emits_only_bounded_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state.json"
            output = root / "policy.json"
            state.write_text(
                json.dumps(
                    {
                        "repository_ci": {
                            "schemaVersion": 1,
                            "repository": "StreamScapeTV/example-swift",
                            "capabilities": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_helper(
                "resolve-read-policy",
                "--project-state", str(state),
                "--repository", "StreamScapeTV/example-swift",
                "--output", str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"schemaVersion": 1, "registryReadPolicy": "credential-free"},
            )

    def test_remote_read_sends_auth_only_for_authenticated_policy(self) -> None:
        artifact = b"exact-private-artifact"

        class FakeResponse:
            status = 200

            def __init__(self) -> None:
                self.blocks = [artifact, b""]

            def read(self, _size: int = -1) -> bytes:
                return self.blocks.pop(0) if self.blocks else b""

        connections: list[object] = []

        class FakeConnection:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.headers: dict[str, str] = {}
                connections.append(self)

            def request(self, method: str, path: str, headers: dict[str, str]) -> None:
                self.method = method
                self.path = path
                self.headers = headers

            def getresponse(self) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                pass

        url = "https://git.faruqi.dev/api/packages/mimranfaruqi/generic/example/3.4.5/file.zip"
        with mock.patch.object(swiftpm_binary.http.client, "HTTPSConnection", FakeConnection):
            status, size, digest = swiftpm_binary.read_remote_identity(
                url, read_policy="credential-free"
            )
            self.assertEqual((status, size, digest), (200, len(artifact), hashlib.sha256(artifact).hexdigest()))
            self.assertEqual(connections[-1].headers, {})
            swiftpm_binary.read_remote_identity(
                url, read_policy="authenticated", username="reader", read_token="secret"
            )
            self.assertIn("Authorization", connections[-1].headers)

    def test_upload_remote_always_sends_separate_write_authorization(self) -> None:
        class FakeResponse:
            status = 201

            def read(self, _size: int = -1) -> bytes:
                return b""

        connections: list[object] = []

        class FakeConnection:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.headers: dict[str, str] = {}
                connections.append(self)

            def putrequest(self, method: str, path: str) -> None:
                self.method = method
                self.path = path

            def putheader(self, name: str, value: str) -> None:
                self.headers[name] = value

            def endheaders(self) -> None:
                pass

            def send(self, _block: bytes) -> None:
                pass

            def getresponse(self) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "artifact.zip"
            artifact.write_bytes(b"write-protected")
            url = "https://git.faruqi.dev/api/packages/mimranfaruqi/generic/example/3.4.5/file.zip"
            with mock.patch.object(swiftpm_binary.http.client, "HTTPSConnection", FakeConnection):
                self.assertEqual(
                    swiftpm_binary.upload_remote(url, artifact, "writer", "write-token"),
                    201,
                )
        self.assertEqual(connections[-1].method, "PUT")
        self.assertIn("Authorization", connections[-1].headers)
        self.assertEqual(
            connections[-1].headers["Authorization"],
            swiftpm_binary.basic_auth("writer", "write-token"),
        )

    def test_remote_exact_identity_rejects_wrong_bytes(self) -> None:
        item = {
            "target": "Example",
            "url": "https://git.faruqi.dev/api/packages/mimranfaruqi/generic/example/3.4.5/file.zip",
            "size": 4,
            "checksum": hashlib.sha256(b"good").hexdigest(),
        }
        with mock.patch.object(
            swiftpm_binary,
            "read_remote_identity",
            return_value=(200, 4, hashlib.sha256(b"evil").hexdigest()),
        ):
            with self.assertRaises(swiftpm_binary.ContractError):
                swiftpm_binary.require_remote_exact(item, read_policy="credential-free")

    def test_publish_credential_free_requires_writes_but_rejects_read_token_injection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence = root / "evidence"
            artifact_dir = evidence / "artifacts"
            artifact_dir.mkdir(parents=True)
            artifact = artifact_dir / "Example.xcframework.zip"
            artifact.write_bytes(b"artifact")
            checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
            plan = root / "plan.json"
            output = root / "receipt.json"
            plan.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "packageURL": "https://github.com/StreamScapeTV/example-swift.git",
                        "version": "3.4.5",
                        "packageRevision": "a" * 40,
                        "evidenceRoot": str(evidence),
                        "artifacts": [
                            {
                                "target": "Example",
                                "file": "artifacts/Example.xcframework.zip",
                                "url": "https://git.faruqi.dev/api/packages/mimranfaruqi/generic/example/3.4.5/Example.xcframework.zip",
                                "checksum": checksum,
                                "size": artifact.stat().st_size,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(plan=str(plan), output=str(output))
            env = {
                "CI_SWIFTPM_BINARY_PACKAGE_USERNAME": "writer",
                "CI_SWIFTPM_BINARY_PACKAGE_TOKEN": "write-token",
                "CI_SWIFTPM_BINARY_READ_POLICY": "credential-free",
            }
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                swiftpm_binary, "require_remote_exact", return_value=True
            ) as exact:
                swiftpm_binary.command_publish(args)
            self.assertTrue(output.exists())
            self.assertEqual(exact.call_args.kwargs["read_policy"], "credential-free")
            self.assertEqual(exact.call_args.kwargs["read_token"], "")

            with mock.patch.dict(
                os.environ,
                {**env, "CI_SWIFTPM_BINARY_PACKAGE_READ_TOKEN": "must-not-be-sent"},
                clear=True,
            ):
                with self.assertRaises(swiftpm_binary.ContractError):
                    swiftpm_binary.command_publish(args)

            with mock.patch.dict(
                os.environ,
                {
                    "CI_SWIFTPM_BINARY_PACKAGE_USERNAME": "writer",
                    "CI_SWIFTPM_BINARY_PACKAGE_TOKEN": "write-token",
                    "CI_SWIFTPM_BINARY_READ_POLICY": "authenticated",
                },
                clear=True,
            ):
                with self.assertRaises(swiftpm_binary.ContractError):
                    swiftpm_binary.command_publish(args)

    def test_consumer_results_must_match_github_tag_identity_twice(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            contract = {
                "schemaVersion": 1,
                "packageURL": "https://github.com/StreamScapeTV/example-swift.git",
                "version": "3.4.5",
                "packageRevision": "c" * 40,
            }
            contract_path = root / "contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            result_paths = []
            for index in (1, 2):
                path = root / f"result-{index}.json"
                path.write_text(json.dumps({**contract, "accepted": True}), encoding="utf-8")
                result_paths.append(path)
            accepted = self.run_helper(
                "validate-consumer",
                "--contract", str(contract_path),
                "--result-one", str(result_paths[0]),
                "--result-two", str(result_paths[1]),
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            bad = json.loads(result_paths[1].read_text(encoding="utf-8"))
            bad["packageRevision"] = "d" * 40
            result_paths[1].write_text(json.dumps(bad), encoding="utf-8")
            rejected = self.run_helper(
                "validate-consumer",
                "--contract", str(contract_path),
                "--result-one", str(result_paths[0]),
                "--result-two", str(result_paths[1]),
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("unexpected packageRevision", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
