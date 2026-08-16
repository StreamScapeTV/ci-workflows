from __future__ import annotations

import base64
import inspect
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ci_workflows import distribution_primitives as p
from ci_workflows.distribution_primitives import (
    ANDROID_KEY_PASSWORD_ENV,
    ANDROID_KEYSTORE_B64_ENV,
    ANDROID_STORE_PASSWORD_ENV,
    APPLE_CERTIFICATE_B64_ENV,
    APPLE_CERTIFICATE_PASSWORD_ENV,
    APPLE_KEYCHAIN_PASSWORD_ENV,
    APPLE_PROFILE_B64_ENV,
    APP_STORE_ISSUER_ID_ENV,
    APP_STORE_KEY_ID_ENV,
    APP_STORE_PRIVATE_KEY_B64_ENV,
    GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_ENV,
    AppleKeychain,
    CommandOutcome,
    DistributionPrimitiveError,
    android_sign,
    android_verify,
    app_store_upload_request,
    apple_archive,
    apple_export,
    apple_sign,
    apple_verify,
    cleanup_distribution_state,
    create_distribution_state,
    execute_upload,
    google_play_upload_request,
    materialize_android_keystore,
    materialize_apple_credentials,
    materialize_store_auth,
    prepare_apple_keychain,
)


@dataclass(frozen=True)
class Call:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


class Runner:
    def __init__(self, outcome: CommandOutcome | None = None) -> None:
        self.outcome = outcome or CommandOutcome(0)
        self.calls: list[Call] = []

    def run(self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> CommandOutcome:
        self.calls.append(Call(tuple(argv), cwd, dict(env)))
        return self.outcome


class DistributionPrimitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"; self.project.mkdir()
        self.state_parent = self.root / "state"; self.state_parent.mkdir()
        self.bin = self.root / "bin"; self.bin.mkdir()

    def tool(self, name: str) -> Path:
        path = self.bin / name; path.write_text("#!/bin/sh\n", encoding="utf-8"); path.chmod(0o755); return path.resolve()

    @staticmethod
    def b64(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    def apple_env(self) -> dict[str, str]:
        return {
            APPLE_CERTIFICATE_B64_ENV: self.b64(b"p12"),
            APPLE_PROFILE_B64_ENV: self.b64(b"profile"),
            APPLE_CERTIFICATE_PASSWORD_ENV: "certificate-secret",
            APPLE_KEYCHAIN_PASSWORD_ENV: "keychain-secret",
            APP_STORE_KEY_ID_ENV: "ABC123DEF4",
            APP_STORE_ISSUER_ID_ENV: "issuer_123",
            APP_STORE_PRIVATE_KEY_B64_ENV: self.b64(b"p8-key"),
        }

    def android_env(self) -> dict[str, str]:
        return {
            ANDROID_KEYSTORE_B64_ENV: self.b64(b"keystore"),
            ANDROID_STORE_PASSWORD_ENV: "store-secret",
            ANDROID_KEY_PASSWORD_ENV: "key-secret",
            GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_ENV: json.dumps({"type": "service_account", "client_email": "ci@example.invalid"}),
        }

    def test_state_and_secret_materialization_are_restricted(self) -> None:
        state = create_distribution_state(self.state_parent)
        apple = materialize_apple_credentials(state, environment=self.apple_env())
        android = materialize_android_keystore(state, environment=self.android_env())
        self.assertEqual(0o700, state.root.stat().st_mode & 0o777)
        self.assertEqual(b"p12", apple.certificate.path.read_bytes())
        self.assertEqual(b"profile", apple.profile.path.read_bytes())
        self.assertEqual(b"keystore", android.path.read_bytes())
        for item in (apple.certificate.path, apple.profile.path, android.path): self.assertEqual(0o600, item.stat().st_mode & 0o777)

    def test_secret_failures_are_redacted_and_symlinks_rejected(self) -> None:
        state = create_distribution_state(self.state_parent)
        secret = "not-base64-secret"
        with self.assertRaises(DistributionPrimitiveError) as context:
            materialize_android_keystore(state, environment={ANDROID_KEYSTORE_B64_ENV: secret})
        self.assertNotIn(secret, str(context.exception))
        outside = self.root / "outside"; outside.write_bytes(b"x")
        (state.root / "android-signing.keystore").symlink_to(outside)
        with self.assertRaises(DistributionPrimitiveError): materialize_android_keystore(state, environment=self.android_env())

    def test_store_auth_materialization_is_structured_without_secret_values(self) -> None:
        state = create_distribution_state(self.state_parent)
        apple = materialize_store_auth(state, "app-store-connect", environment=self.apple_env())
        google = materialize_store_auth(state, "google-play", environment=self.android_env())
        self.assertEqual("ABC123DEF4", apple.key_id)
        self.assertEqual(b"p8-key", apple.credential.path.read_bytes())
        self.assertEqual("service_account", json.loads(google.credential.path.read_text())["type"])
        self.assertNotIn("client_email", repr(google))

    def test_prepare_apple_keychain_uses_fixed_environment_secrets(self) -> None:
        state = create_distribution_state(self.state_parent)
        credentials = materialize_apple_credentials(state, environment=self.apple_env())
        runner = Runner(); keychain = prepare_apple_keychain(self.tool("security"), credentials, state, environment=self.apple_env(), runner=runner)
        self.assertEqual(state.root / "ciw-signing.keychain-db", keychain.path)
        self.assertEqual([c.argv[1] for c in runner.calls], ["create-keychain", "unlock-keychain", "import"])

    def test_apple_archive_export_sign_verify_use_caller_configuration(self) -> None:
        xcodebuild, codesign = self.tool("xcodebuild"), self.tool("codesign")
        container = self.project / "Example.xcodeproj"; container.mkdir()
        archive = self.project / "build" / "Example.xcarchive"; archive.parent.mkdir()
        options = self.project / "ExportOptions.plist"; options.write_text("<plist/>")
        app = self.project / "Example.app"; app.mkdir()
        runner = Runner()
        result = apple_archive(xcodebuild, project_directory=self.project, container=container, container_kind="project", scheme="ReleaseScheme", configuration="Release", archive_path=archive, destination="generic/platform=iOS", runner=runner)
        self.assertEqual("apple.archive", result.operation); self.assertIn("ReleaseScheme", runner.calls[0].argv)
        archive.mkdir()
        self.assertEqual("apple.export", apple_export(xcodebuild, project_directory=self.project, archive=archive, export_options=options, export_path=Path("export"), runner=runner).operation)
        self.assertTrue(apple_sign(codesign, app, project_directory=self.project, identity="Apple Distribution: Example", runner=runner).signed)
        self.assertTrue(apple_verify(codesign, app, project_directory=self.project, runner=runner).verified)

    def test_apple_archive_rejects_wrong_container_kind(self) -> None:
        container = self.project / "Example.xcodeproj"; container.mkdir()
        with self.assertRaises(DistributionPrimitiveError):
            apple_archive(self.tool("xcodebuild"), project_directory=self.project, container=container, container_kind="workspace", scheme="Release", configuration="Release", archive_path=Path("Example.xcarchive"), runner=Runner())

    def test_android_apk_sign_verify_uses_environment_indirection(self) -> None:
        apk = self.project / "app.apk"; apk.write_bytes(b"apk")
        state = create_distribution_state(self.state_parent); keystore = materialize_android_keystore(state, environment=self.android_env()); runner = Runner()
        signed = android_sign("apk", self.tool("apksigner"), apk, keystore, project_directory=self.project, key_alias="release", environment=self.android_env(), output_path=Path("signed.apk"), runner=runner)
        joined = " ".join(runner.calls[0].argv)
        self.assertIn(f"env:{ANDROID_STORE_PASSWORD_ENV}", joined); self.assertNotIn("store-secret", joined)
        signed.package.write_bytes(b"signed")
        self.assertTrue(android_verify("apk", self.tool("apksigner"), signed.package, project_directory=self.project, runner=runner).verified)

    def test_android_aab_sign_verify_uses_environment_indirection(self) -> None:
        aab = self.project / "app.aab"; aab.write_bytes(b"aab")
        state = create_distribution_state(self.state_parent); keystore = materialize_android_keystore(state, environment=self.android_env()); runner = Runner()
        self.assertTrue(android_sign("aab", self.tool("jarsigner"), aab, keystore, project_directory=self.project, key_alias="release", environment=self.android_env(), runner=runner).signed)
        self.assertIn("-storepass:env", runner.calls[0].argv); self.assertNotIn("store-secret", " ".join(runner.calls[0].argv))
        self.assertTrue(android_verify("aab", self.tool("jarsigner"), aab, project_directory=self.project, runner=runner).verified)

    def test_app_store_request_keeps_private_key_out_of_command(self) -> None:
        package = self.project / "app.ipa"; package.write_bytes(b"ipa")
        state = create_distribution_state(self.state_parent); auth = materialize_store_auth(state, "app-store-connect", environment=self.apple_env())
        request = app_store_upload_request(self.tool("xcrun"), package, auth, project_directory=self.project, platform="ios")
        self.assertEqual(str(auth.credential.path.parent), request.environment_overrides["API_PRIVATE_KEYS_DIR"])
        self.assertNotIn("p8-key", " ".join(request.argv)); self.assertIn(auth.key_id, request.argv)

    def test_google_play_request_keeps_track_and_package_caller_owned(self) -> None:
        package = self.project / "app.aab"; package.write_bytes(b"aab")
        state = create_distribution_state(self.state_parent); auth = materialize_store_auth(state, "google-play", environment=self.android_env())
        request = google_play_upload_request(self.tool("fastlane"), package, auth, project_directory=self.project, package_name="com.example.player", track="internal", kind="aab", release_status="draft")
        joined = " ".join(request.argv)
        self.assertIn("com.example.player", joined); self.assertIn("internal", joined); self.assertIn("draft", joined); self.assertNotIn("client_email", joined); self.assertNotIn("rollout", joined)

    def test_upload_execution_is_optional_structured_and_redacted(self) -> None:
        package = self.project / "app.aab"; package.write_bytes(b"aab")
        state = create_distribution_state(self.state_parent); auth = materialize_store_auth(state, "google-play", environment=self.android_env())
        request = google_play_upload_request(self.tool("fastlane"), package, auth, project_directory=self.project, package_name="com.example.player", track="internal", kind="aab")
        self.assertTrue(execute_upload(request, runner=Runner()).uploaded)
        secret = "provider-secret"
        with self.assertRaises(DistributionPrimitiveError) as context: execute_upload(request, runner=Runner(CommandOutcome(7, secret, secret)))
        self.assertEqual("command_failed", context.exception.code); self.assertNotIn(secret, str(context.exception))

    def test_cleanup_is_idempotent_and_removes_state_on_keychain_failure(self) -> None:
        state = create_distribution_state(self.state_parent); credentials = materialize_apple_credentials(state, environment=self.apple_env())
        keychain = AppleKeychain(state.root / "ciw-signing.keychain-db"); keychain.path.touch()
        with self.assertRaises(DistributionPrimitiveError): cleanup_distribution_state(state, security=self.tool("security"), keychain=keychain, environment=self.apple_env(), runner=Runner(CommandOutcome(9)))
        self.assertFalse(state.root.exists())
        cleanup_distribution_state(state, security=self.tool("security"), keychain=keychain, environment=self.apple_env(), runner=Runner())

    def test_public_api_has_no_secret_value_or_product_specific_parameters(self) -> None:
        functions = (materialize_apple_credentials, materialize_android_keystore, materialize_store_auth, prepare_apple_keychain, apple_archive, apple_export, apple_sign, apple_verify, android_sign, android_verify, app_store_upload_request, google_play_upload_request, execute_upload, cleanup_distribution_state)
        forbidden = {"password", "token", "certificate_data", "keystore_data", "private_key", "service_account_json", "product", "repository", "team"}
        for function in functions: self.assertTrue(set(inspect.signature(function).parameters).isdisjoint(forbidden), function.__name__)
        source = Path(p.__file__).read_text().casefold()
        for text in ("streamscape", "iptv-", "actions/cache", "com.stream"): self.assertNotIn(text, source)


if __name__ == "__main__": unittest.main()
