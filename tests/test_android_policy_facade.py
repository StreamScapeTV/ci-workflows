"""Public-facade coverage for Android repository-policy projection."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import android, ciw_android
from ci_workflows.android_types import (
    AndroidValidationError,
    AndroidValidationRequest,
)
from ci_workflows.ciw_types import CIWContext
from ci_workflows.foundation_types import FoundationError

ROOT = Path(__file__).resolve().parents[1]
SAFE_FIXTURE = (
    "Endpoint: GET /portal.php?type=itv&action=get_genres&"
    "JsHttpRequest=1-xml\n"
    "          Headers: Authorization"
    + ": Bearer <token>; Cookie: mac=<mac>; stb_lang=en; timezone=<tz>\n"
    "Product:  Stalker / Ministra Middleware STB-emulation API "
    "(portal.php / load.php),\n"
    "          stable since Stalker 4.x; current as Ministra 5.x (2026)\n"
    "Fetched:  2026-06-11\n"
    "Doc URL:  https://www.quassi.nl/2020/03/23/"
    "the-wonderful-world-of-stalker-iptv-2/\n"
    "          + server source: iptvhakr/stalker_portal "
    "server/lib/itv.class.php (getGenres)\n"
    "\n"
    "Notes:\n"
    "  - Live-channel genre list. The `js` payload is a flat array of genre\n"
    "    objects. Field names (`id`, `title`, `alias`, `censored`, "
    "`active_sub`)\n"
    "    are emitted verbatim by the server (itv.class.php).\n"
    "  - The provider maps each genre to a `categories` row "
    "(live content type)\n"
    "    and uses `id` for the `get_ordered_list?genre=<id>` channel fetch.\n"
    "  - `id`/`title` are the only fields the mapper consumes; the rest are\n"
    "    retained to mirror the real shape so a handler that crashes on extra\n"
    "    keys is caught.\n"
)
SAFE_PATH = "docs/fixtures/stalker/get_genres.README.md"


class AndroidPolicyFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", str(self.repository)],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "config",
                "user.email",
                "android-policy@example.invalid",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "config",
                "user.name",
                "Android Policy",
            ],
            check=True,
        )
        self.environment = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commit(self, relative: str, content: str = "safe\n") -> Path:
        target = self.repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repository), "add", relative],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "commit",
                "--quiet",
                "-m",
                relative,
            ],
            check=True,
        )
        return target

    def request(
        self,
        *,
        artifact_exception_id: str | None = None,
    ) -> AndroidValidationRequest:
        head = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        return AndroidValidationRequest(
            repository="StreamScapeTV/iptv-android",
            admitted_sha=head,
            validation_profile="toolchain-smoke",
            task_profile="app-toolchain",
            working_directory=".",
            gradle_wrapper_path="gradlew",
            targeted_test_selector=None,
            consumer_script_profile=None,
            private_dependency_contract_id=None,
            private_dependency_sha=None,
            artifact_exception_id=artifact_exception_id,
            device_family=None,
            device_request_id=None,
            source_trust="trusted-pr",
        )

    def invoke(
        self,
        request: AndroidValidationRequest | None = None,
    ) -> object:
        candidate = request or self.request()
        expected = object()
        with (
            mock.patch.object(
                android,
                "load_android_contract",
                return_value={},
            ),
            mock.patch.object(
                android,
                "resolve_validation_plan",
                return_value=object(),
            ),
            mock.patch.object(
                android,
                "execute_android_plan",
                return_value=expected,
            ),
        ):
            actual = android.validate(
                contract_root=ROOT,
                source_root=self.repository,
                state_root=self.state,
                request=candidate,
                phase="execute",
                environment=self.environment,
            )
        self.assertIs(actual, expected)
        return actual

    def assert_policy_failure(
        self,
        code: str,
        rule: str,
    ) -> AndroidValidationError:
        with self.assertRaises(AndroidValidationError) as caught:
            self.invoke()
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(error.rule_id, rule)
        self.assertIsNotNone(error.subject)
        assert error.subject is not None
        self.assertLessEqual(len(error.subject), 255)
        self.assertNotIn(str(self.root), error.subject)
        self.assertNotIn("secret", error.subject.casefold())
        return error

    def test_clean_reviewed_synthetic_authorization_marker_passes(self) -> None:
        self.commit(SAFE_PATH, SAFE_FIXTURE)
        self.invoke()

    def test_real_token_like_value_fails_with_stable_secret_code(self) -> None:
        real_shape = "ghp_" + "B" * 40
        self.commit(
            SAFE_PATH,
            SAFE_FIXTURE.replace("<token>", real_shape),
        )
        error = self.assert_policy_failure(
            "tracked_secret_detected",
            "tracked_secret_detected",
        )
        self.assertEqual(error.subject, SAFE_PATH)
        self.assertNotIn(real_shape, repr(error.diagnostic_values()))

    def test_forbidden_file_fails_distinctly(self) -> None:
        self.commit(".env", "EXAMPLE=value\n")
        error = self.assert_policy_failure(
            "forbidden_tracked_file",
            "forbidden_tracked_file",
        )
        self.assertEqual(error.subject, ".env")

    def test_symlink_escape_fails_distinctly(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        link = self.repository / "outside-link"
        link.symlink_to(outside)
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "outside-link"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "commit",
                "--quiet",
                "-m",
                "outside-link",
            ],
            check=True,
        )
        error = self.assert_policy_failure(
            "symlink_path_escape",
            "tracked_symlink_escape",
        )
        self.assertEqual(error.subject, "outside-link")

    def test_generated_output_drift_fails_distinctly(self) -> None:
        target = self.commit(
            "docs/workflows/public-api-reference.md",
            "generated baseline\n",
        )
        target.write_text("generated drift\n", encoding="utf-8")
        error = self.assert_policy_failure(
            "generated_output_drift",
            "generated_output_dirty",
        )
        self.assertEqual(
            error.subject,
            "docs/workflows/public-api-reference.md",
        )

    def test_actual_tracked_and_untracked_dirt_remain_dirty_tree(self) -> None:
        target = self.commit("value.txt")
        target.write_text("mutated\n", encoding="utf-8")
        tracked = self.assert_policy_failure(
            "dirty_tree",
            "repository_tree_dirty",
        )
        assert tracked.subject is not None
        self.assertRegex(tracked.subject, r"^sha256:[0-9a-f]{64}$")

        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "checkout",
                "--",
                "value.txt",
            ],
            check=True,
        )
        (self.repository / "untracked.txt").write_text(
            "untracked\n",
            encoding="utf-8",
        )
        untracked = self.assert_policy_failure(
            "dirty_tree",
            "repository_tree_dirty",
        )
        assert untracked.subject is not None
        self.assertRegex(untracked.subject, r"^sha256:[0-9a-f]{64}$")

    def test_artifact_and_contract_failures_are_distinct(self) -> None:
        self.commit("value.txt")
        with self.assertRaises(AndroidValidationError) as artifact:
            self.invoke(
                self.request(
                    artifact_exception_id="android-redacted-diagnostics-v1",
                )
            )
        self.assertEqual(
            artifact.exception.code,
            "artifact_policy_failed",
        )
        self.assertEqual(
            artifact.exception.rule_id,
            "unused_artifact_exception",
        )
        self.assertEqual(
            artifact.exception.subject,
            "contracts/artifact-exceptions.json",
        )

        with mock.patch.object(
            android,
            "load_android_source_policy",
            side_effect=FoundationError("android_source_policy_invalid"),
        ):
            with self.assertRaises(AndroidValidationError) as contract:
                self.invoke()
        self.assertEqual(
            contract.exception.code,
            "policy_contract_failed",
        )
        self.assertEqual(
            contract.exception.rule_id,
            "android_source_policy_invalid",
        )
        self.assertEqual(
            contract.exception.subject,
            "contracts/android-source-policy.json",
        )

    def test_command_projection_is_bounded_and_redacted(self) -> None:
        output = self.root / "github-output"
        stderr = io.StringIO()
        context = CIWContext(
            root=ROOT,
            environment={
                "GITHUB_OUTPUT": str(output),
                "INPUT_ADMITTED_SHA": "a" * 40,
                "INPUT_VALIDATION_PROFILE": "toolchain-smoke",
                "INPUT_TASK_PROFILE": "app-toolchain",
            },
            stdout=io.StringIO(),
            stderr=stderr,
        )
        error = AndroidValidationError(
            "tracked_secret_detected",
            rule_id="tracked_secret_detected",
            subject=SAFE_PATH,
        )
        ciw_android._failure_outputs(context, error)
        values = dict(
            line.split("=", 1)
            for line in output.read_text(encoding="utf-8").splitlines()
        )
        summary = json.loads(values["test_summary"])
        self.assertEqual(
            summary["failure_code"],
            "tracked_secret_detected",
        )
        self.assertEqual(
            summary["policy_rule"],
            "tracked_secret_detected",
        )
        self.assertEqual(summary["policy_subject"], SAFE_PATH)
        combined = output.read_text(encoding="utf-8") + stderr.getvalue()
        self.assertNotIn(str(self.root), combined)
        self.assertIsNone(
            re.search(r"ghp_[A-Za-z0-9]{20,}", combined)
        )


if __name__ == "__main__":
    unittest.main()
