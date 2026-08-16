from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.web_primitives import (
    ProcessOutcome,
    WebPrimitiveError,
    deploy_cloudflare_pages,
    inspect_static_output,
    run_static_verification,
)


class FakeRunner:
    def __init__(self, outcomes=None, callback=None):
        self.outcomes = list(outcomes or [])
        self.callback = callback
        self.calls = []

    def run(self, argv, *, cwd, env, timeout_seconds):
        self.calls.append((tuple(argv), cwd, dict(env), timeout_seconds))
        if self.callback is not None:
            return self.callback(tuple(argv), cwd, dict(env), timeout_seconds)
        return self.outcomes.pop(0) if self.outcomes else ProcessOutcome(0)


class WebPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "dist"
        self.output.mkdir()
        (self.output / "index.html").write_text("<h1>hello</h1>\n", encoding="utf-8")
        assets = self.output / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("console.log('ok')\n", encoding="utf-8")
        self.state = self.root / "state"
        self.state.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_is_deterministic_and_content_bound(self):
        first = inspect_static_output(self.output)
        second = inspect_static_output(self.output)
        self.assertEqual(first, second)
        self.assertEqual(["assets/app.js", "index.html"], [row.path for row in first.files])
        self.assertEqual(2, first.file_count)
        self.assertEqual(64, len(first.sha256))
        (self.output / "index.html").write_text("<h1>changed</h1>\n", encoding="utf-8")
        self.assertNotEqual(first.sha256, inspect_static_output(self.output).sha256)

    def test_manifest_rejects_empty_symlink_and_file_limit(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(WebPrimitiveError, "static_output_empty"):
            inspect_static_output(empty)
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.output / "escape").symlink_to(outside)
        with self.assertRaisesRegex(WebPrimitiveError, "static_output_symlink"):
            inspect_static_output(self.output)
        (self.output / "escape").unlink()
        with self.assertRaisesRegex(WebPrimitiveError, "static_output_too_large"):
            inspect_static_output(self.output, maximum_files=1)

    def test_verification_runs_direct_argv_and_requires_unchanged_output(self):
        runner = FakeRunner([ProcessOutcome(0, "ok", "")])
        result = run_static_verification(
            self.output,
            ("node", "verify-static.mjs", "--strict"),
            runner,
            environment={"CI": "true"},
        )
        self.assertEqual(0, result.returncode)
        argv, cwd, env, timeout = runner.calls[0]
        self.assertEqual(("node", "verify-static.mjs", "--strict"), argv)
        self.assertEqual(self.output.resolve(), cwd)
        self.assertEqual("true", env["CI"])
        self.assertEqual(600, timeout)

    def test_verification_failure_and_mutation_fail_closed(self):
        with self.assertRaisesRegex(WebPrimitiveError, "verification_failed"):
            run_static_verification(
                self.output,
                ("node", "verify.mjs"),
                FakeRunner([ProcessOutcome(2)]),
                environment={},
            )

        def mutate(argv, cwd, env, timeout):
            (cwd / "index.html").write_text("mutated", encoding="utf-8")
            return ProcessOutcome(0)

        with self.assertRaisesRegex(WebPrimitiveError, "verification_mutated_output"):
            run_static_verification(
                self.output,
                ("node", "verify.mjs"),
                FakeRunner(callback=mutate),
                environment={},
            )

    def test_pages_deploy_uses_fixed_current_cloudflare_credentials_and_cleans_state(self):
        account = "a" * 32

        def pages(argv, cwd, env, timeout):
            self.assertEqual(
                (
                    "wrangler",
                    "pages",
                    "deploy",
                    str(self.output.resolve()),
                    "--project-name",
                    "example-site",
                    "--branch",
                    "preview/one",
                    "--commit-dirty=false",
                    "--no-bundle",
                    "--commit-hash",
                    "b" * 40,
                ),
                argv,
            )
            self.assertEqual(account, env["CLOUDFLARE_ACCOUNT_ID"])
            self.assertEqual("token-value", env["CLOUDFLARE_API_TOKEN"])
            self.assertNotIn("CF_API_TOKEN", env)
            self.assertNotIn("CLOUDFLARE_API_KEY", env)
            self.assertEqual("false", env["WRANGLER_SEND_METRICS"])
            self.assertEqual("true", env["WRANGLER_LOG_SANITIZE"])
            self.assertTrue(str(env["WRANGLER_CACHE_DIR"]).startswith(str(cwd)))
            self.assertTrue(str(env["WRANGLER_OUTPUT_FILE_PATH"]).startswith(str(cwd)))
            Path(env["WRANGLER_OUTPUT_FILE_PATH"]).write_text(
                json.dumps(
                    {
                        "type": "pages-deploy",
                        "deployment_id": "deploy-123",
                        "url": "https://abc.example-site.pages.dev",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return ProcessOutcome(0)

        result = deploy_cloudflare_pages(
            self.output,
            FakeRunner(callback=pages),
            project_name="example-site",
            account_id=account,
            branch="preview/one",
            commit_hash="b" * 40,
            environment={
                "CLOUDFLARE_API_TOKEN": "token-value",
                "CF_API_TOKEN": "legacy",
                "CLOUDFLARE_API_KEY": "legacy-key",
            },
            state_parent=self.state,
        )
        self.assertEqual("deploy-123", result.deployment_id)
        self.assertEqual("https://abc.example-site.pages.dev", result.url)
        self.assertEqual("example-site", result.project_name)
        self.assertEqual("preview/one", result.branch)
        self.assertEqual([], list(self.state.iterdir()))

    def test_pages_deploy_requires_token_valid_account_and_structured_output(self):
        with self.assertRaisesRegex(WebPrimitiveError, "cloudflare_account_invalid"):
            deploy_cloudflare_pages(
                self.output,
                FakeRunner(),
                project_name="site",
                account_id="not-an-account",
                branch="main",
                environment={"CLOUDFLARE_API_TOKEN": "token"},
                state_parent=self.state,
            )
        with self.assertRaisesRegex(WebPrimitiveError, "cloudflare_token_required"):
            deploy_cloudflare_pages(
                self.output,
                FakeRunner(),
                project_name="site",
                account_id="a" * 32,
                branch="main",
                environment={},
                state_parent=self.state,
            )
        self.assertEqual([], list(self.state.iterdir()))

        def no_record(argv, cwd, env, timeout):
            return ProcessOutcome(0)

        with self.assertRaisesRegex(WebPrimitiveError, "cloudflare_output_missing"):
            deploy_cloudflare_pages(
                self.output,
                FakeRunner(callback=no_record),
                project_name="site",
                account_id="a" * 32,
                branch="main",
                environment={"CLOUDFLARE_API_TOKEN": "token"},
                state_parent=self.state,
            )
        self.assertEqual([], list(self.state.iterdir()))

    def test_pages_deploy_process_failure_cleans_isolated_state(self):
        with self.assertRaisesRegex(WebPrimitiveError, "cloudflare_deploy_failed"):
            deploy_cloudflare_pages(
                self.output,
                FakeRunner([ProcessOutcome(1, "", "denied")]),
                project_name="site",
                account_id="a" * 32,
                branch="main",
                environment={"CLOUDFLARE_API_TOKEN": "token"},
                state_parent=self.state,
            )
        self.assertEqual([], list(self.state.iterdir()))

    def test_pages_deploy_rejects_credentialed_or_non_https_output_url(self):
        def bad_url(argv, cwd, env, timeout):
            Path(env["WRANGLER_OUTPUT_FILE_PATH"]).write_text(
                '{"type":"pages-deploy","url":"http://user:pass@example.test"}\n',
                encoding="utf-8",
            )
            return ProcessOutcome(0)

        with self.assertRaisesRegex(WebPrimitiveError, "cloudflare_output_invalid"):
            deploy_cloudflare_pages(
                self.output,
                FakeRunner(callback=bad_url),
                project_name="site",
                account_id="a" * 32,
                branch="main",
                environment={"CLOUDFLARE_API_TOKEN": "token"},
                state_parent=self.state,
            )
        self.assertEqual([], list(self.state.iterdir()))

    def test_pages_deploy_detects_output_mutation_and_still_cleans_state(self):
        def mutate(argv, cwd, env, timeout):
            (self.output / "index.html").write_text("changed during deploy", encoding="utf-8")
            Path(env["WRANGLER_OUTPUT_FILE_PATH"]).write_text(
                '{"type":"pages-deploy","id":"deploy-1","deployment_url":"https://site.pages.dev"}\n',
                encoding="utf-8",
            )
            return ProcessOutcome(0)

        with self.assertRaisesRegex(WebPrimitiveError, "deployment_mutated_output"):
            deploy_cloudflare_pages(
                self.output,
                FakeRunner(callback=mutate),
                project_name="site",
                account_id="a" * 32,
                branch="main",
                environment={"CLOUDFLARE_API_TOKEN": "token"},
                state_parent=self.state,
            )
        self.assertEqual([], list(self.state.iterdir()))


if __name__ == "__main__":
    unittest.main()
