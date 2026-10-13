"""Focused credential split without ever touching a real Kubernetes credential."""
import base64
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/ci/cluster_inspector_credentials.py"
spec = importlib.util.spec_from_file_location("inspector_credentials", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CONFIG = """apiVersion: v1
kind: Config
clusters:
- name: private
  cluster:
    server: https://api.example.test
    certificate-authority-data: ZHVtbXk=
users:
- name: inspector
  user:
    tokenFile: __CLUSTER_INSPECTOR_TOKEN_FILE__
contexts:
- name: private
  context:
    cluster: private
    user: inspector
current-context: private
"""
TOKEN = "synthetic-token.not-a-secret.EXAMPLE1234"
OLD_TOKEN = "example-old-inspector-token0123456789"
INLINE_CONFIG = CONFIG.replace(
    "tokenFile: __CLUSTER_INSPECTOR_TOKEN_FILE__", "token: " + OLD_TOKEN
)


def encode(config=CONFIG):
    return base64.b64encode(config.encode()).decode()


class ClusterInspectorCredentialTests(unittest.TestCase):
    def test_preparation_never_inlines_token_and_restricts_file_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = mod.prepare(root, encode(), TOKEN)
            token = root / mod.TOKEN_NAME
            self.assertEqual(config, root / mod.KUBECONFIG_NAME)
            self.assertEqual(token.read_text(), TOKEN)
            self.assertNotIn(TOKEN, config.read_text())
            self.assertIn("tokenFile: " + str(token), config.read_text())
            for p in (config, token):
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_existing_inline_token_is_replaced_only_in_ephemeral_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = mod.prepare(root, encode(INLINE_CONFIG), TOKEN)
            rendered = config.read_text()
            self.assertIn("tokenFile: " + str(root / mod.TOKEN_NAME), rendered)
            self.assertNotIn(OLD_TOKEN, rendered)
            self.assertNotIn(TOKEN, rendered)
            self.assertEqual((root / mod.TOKEN_NAME).read_text(), TOKEN)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / mod.TOKEN_NAME).stat().st_mode & 0o777, 0o600)
            # Owner's original credential secret is not rewritten or reparsed
            # with a tokenFile placeholder, even during token rotation.
            self.assertIn("token: " + OLD_TOKEN, INLINE_CONFIG)

    def test_existing_quoted_inline_token_is_replaced_without_preserving_old_value(self):
        for quoted in ('"' + OLD_TOKEN + '"', "'" + OLD_TOKEN + "'"):
            with self.subTest(quote=quoted[0]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = INLINE_CONFIG.replace("token: " + OLD_TOKEN, "token: " + quoted)
                rendered = mod.prepare(root, encode(source), TOKEN).read_text()
                self.assertNotIn(OLD_TOKEN, rendered)
                self.assertIn("tokenFile: " + str(root / mod.TOKEN_NAME), rendered)

    def test_inline_token_duplicate_alternate_auth_or_malformed_values_rejected(self):
        for candidate in (
            INLINE_CONFIG + "    token: extra-old-inspector-token0123456789\n",
            INLINE_CONFIG + "  exec:\n    command: test-command\n",
            INLINE_CONFIG + "  client-certificate-data: ZHVtbXk=\n",
            INLINE_CONFIG.replace(OLD_TOKEN, "too-short"),
            INLINE_CONFIG.replace(OLD_TOKEN, OLD_TOKEN + " # unsafe-comment"),
            INLINE_CONFIG.replace(OLD_TOKEN, OLD_TOKEN + "\n    tokenFile: /tmp/other"),
            INLINE_CONFIG + "  <<: *other-user\n",
            INLINE_CONFIG.replace("  user:\n", "  user: &inspector\n"),
            INLINE_CONFIG.replace("    user: inspector", "    user: *inspector"),
        ):
            with self.subTest(candidate=candidate[-45:]), tempfile.TemporaryDirectory() as t:
                with self.assertRaises(mod.CredentialError):
                    mod.prepare(Path(t), encode(candidate), TOKEN)
                self.assertEqual(list(Path(t).iterdir()), [])

    def test_missing_secret_and_bad_token_fail_before_writing_files(self):
        for token in ("", "short", "abc\nunsafe" + "x" * 32, "x" * 20000, "bad unicode " + "ø" * 12):
            with self.subTest(token=repr(token[:6])), tempfile.TemporaryDirectory() as t:
                with self.assertRaises(mod.CredentialError):
                    mod.prepare(Path(t), encode(), token)
                self.assertEqual(list(Path(t).iterdir()), [])

    def test_wrong_placeholder_and_alternate_auth_fail_closed(self):
        for text in (
            CONFIG.replace("tokenFile:", "token:"),
            CONFIG.replace(mod.TOKEN_MARKER, "/var/run/secret"),
            CONFIG.replace("current-context:", "  tokenFile: __CLUSTER_INSPECTOR_TOKEN_FILE__\ncurrent-context:"),
            CONFIG + "  token: other\n",
            CONFIG + "  exec:\n    command: /bin/sh\n",
            CONFIG + "  client-key-data: ZHVtbXk=\n",
            CONFIG + "  auth-provider:\n    name: something\n",
            CONFIG + "  <<: *other\n",
        ):
            with self.subTest(config=text[-50:]), tempfile.TemporaryDirectory() as t:
                with self.assertRaises(mod.CredentialError):
                    mod.prepare(Path(t), encode(text), TOKEN)
                self.assertEqual(list(Path(t).iterdir()), [])

    def test_invalid_base64_and_oversize_are_rejected(self):
        for text in ("", "not_base64!", "AA=\n", encode(CONFIG * 1024)):
            with self.subTest(encoded=text[:8]), tempfile.TemporaryDirectory() as t:
                with self.assertRaises(mod.CredentialError):
                    mod.prepare(Path(t), text, TOKEN)

    def test_persistent_residue_rejected_without_overwriting(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / mod.KUBECONFIG_NAME).write_text("protected")
            with self.assertRaises(mod.CredentialError):
                mod.prepare(Path(t), encode(), TOKEN)
            self.assertEqual((Path(t) / mod.KUBECONFIG_NAME).read_text(), "protected")

    def test_cli_emits_only_fixed_paths_and_no_token(self):
        with tempfile.TemporaryDirectory() as t:
            for fname in ("env", "output"):
                (Path(t) / fname).touch()
            env={**os.environ,"RUNNER_TEMP":t,"CLUSTER_INSPECTOR_KUBECONFIG_B64":encode(),
                 "CLUSTER_INSPECTOR_BEARER_TOKEN":TOKEN,"GITHUB_ENV":str(Path(t)/"env"),
                 "GITHUB_OUTPUT":str(Path(t)/"output")}
            out = subprocess.run([sys.executable,str(SOURCE),"prepare"],env=env,text=True,capture_output=True)
            self.assertEqual(out.returncode,0,out.stdout)
            self.assertNotIn(TOKEN,out.stdout+out.stderr+(Path(t)/"env").read_text()+(Path(t)/"output").read_text())
            self.assertIn("KUBECONFIG="+str(Path(t)/mod.KUBECONFIG_NAME),(Path(t)/"env").read_text())

    def test_cli_failure_remains_fixed_and_does_not_print_secrets(self):
        with tempfile.TemporaryDirectory() as t:
            env={**os.environ,"RUNNER_TEMP":t,"CLUSTER_INSPECTOR_KUBECONFIG_B64":encode(),
                 "CLUSTER_INSPECTOR_BEARER_TOKEN":TOKEN + "\nBAD"}
            out = subprocess.run([sys.executable,str(SOURCE),"prepare"],env=env,text=True,capture_output=True)
            self.assertNotEqual(out.returncode,0)
            self.assertEqual(out.stdout.strip(),"cluster inspector credential preparation failed")
            self.assertNotIn(TOKEN,out.stderr)
            self.assertEqual(list(Path(t).iterdir()),[])


if __name__ == "__main__":
    unittest.main()
