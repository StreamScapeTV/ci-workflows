"""Synthetic bounded readiness regression; never uses real private endpoints."""
from __future__ import annotations

import contextlib
import io
import importlib.util
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_GIT = yaml.safe_load((ROOT / 'actions/private-git/action.yml').read_text())
DRIVE = yaml.safe_load((ROOT / 'actions/google-drive/action.yml').read_text())


def private_git_script() -> str:
    runner = PRIVATE_GIT['runs']['steps'][-1]['run']
    return runner.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]


def drive_auth_script() -> str:
    script = DRIVE['runs']['steps'][0]['run']
    start = script.index('auth_response_file="$(mktemp')
    end = script.index('echo "::add-mask::${access_token}"', start) + len('echo "::add-mask::${access_token}"')
    return script[start:end]


class PrivateNetworkReadinessTests(unittest.TestCase):
    def test_hosted_macos_restores_original_dns_and_search_after_upstream_rewrite(self):
        spec = importlib.util.spec_from_file_location('macos_dns', ROOT / 'actions/private-git/macos_dns.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for answers, expected in [
            (["There aren't any DNS Servers set on Ethernet.\n", "There aren't any Search Domains set on Ethernet.\n"],
             [['Empty'], ['Empty']]),
            (['1.1.1.1\n8.8.8.8\n', 'example.test\ninternal.test\n'],
             [['1.1.1.1', '8.8.8.8'], ['example.test', 'internal.test']]),
        ]:
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as td:
                output = Path(td) / 'outputs'
                with mock.patch.dict(os.environ, {'RUNNER_TEMP': td, 'GITHUB_OUTPUT': str(output)}), \
                     mock.patch.object(module.subprocess, 'check_output', side_effect=answers):
                    module.capture()
                state = output.read_text().strip().split('=', 1)[1]
                self.assertEqual(Path(state).stat().st_mode & 0o777, 0o600)
                # Simulate upstream replacing DNS, including when connection later fails.
                with mock.patch.dict(os.environ, {'DNS_STATE': state}), \
                     mock.patch.object(module.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                    module.restore()
                self.assertEqual(run.call_args_list, [
                    mock.call(['sudo', 'networksetup', '-setdnsservers', 'Ethernet', *expected[0]], check=True),
                    mock.call(['sudo', 'networksetup', '-setsearchdomains', 'Ethernet', *expected[1]], check=True),
                ])
                self.assertFalse(Path(state).exists())

    def test_macos_dns_restore_runs_after_connection_failure_without_touching_self_hosted(self):
        steps = PRIVATE_GIT['runs']['steps']
        names = [step['name'] for step in steps]
        save = next(step for step in steps if step.get('id') == 'runner_dns')
        connect = next(step for step in steps if step.get('uses') == 'tailscale/github-action@v4')
        restore = next(step for step in steps if step['name'] == 'Restore hosted macOS DNS settings')
        self.assertLess(names.index(save['name']), names.index(connect['name']))
        self.assertLess(names.index(connect['name']), names.index(restore['name']))
        self.assertLess(names.index(restore['name']), names.index('Verify private Git service reachability'))
        self.assertIn('--accept-dns=false', connect['with']['args'])
        for step in (save, restore):
            self.assertIn("runner.environment == 'github-hosted'", step['if'])
            self.assertIn("runner.os == 'macOS'", step['if'])
        self.assertIn('always()', restore['if'])
        self.assertIn("steps.runner_dns.outcome == 'success'", restore['if'])
        self.assertNotIn('restart', restore['run'])

    def test_transient_dns_failure_recovers_with_fixed_bounded_probes(self):
        script = private_git_script()
        out = io.StringIO()
        fake_success = subprocess.CompletedProcess(args=[], returncode=0)
        with mock.patch('subprocess.run', side_effect=[subprocess.CalledProcessError(1, ['probe']), fake_success]) as run, mock.patch('time.sleep') as sleep, contextlib.redirect_stdout(out):
            exec(compile(script, '<private-git>', 'exec'), {})
        self.assertEqual(run.call_count, 2)
        self.assertEqual(sleep.call_args.args, (3,))
        for call in run.call_args_list:
            self.assertEqual(call.kwargs['timeout'], 12)
            self.assertEqual(call.kwargs['stdout'], subprocess.DEVNULL)
            self.assertEqual(call.kwargs['stderr'], subprocess.DEVNULL)
        self.assertIn('reachable', out.getvalue())

    def test_permanently_unreachable_fails_without_private_errors(self):
        err = io.StringIO()
        with mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired(['synthetic'], 12)) as run, mock.patch('time.sleep') as sleep, contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as exited:
            exec(compile(private_git_script(), '<private-git>', 'exec'), {})
        self.assertEqual(exited.exception.code, 1)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(err.getvalue().strip(), 'private Git service unavailable after bounded network readiness probes')

    def test_failed_dns_probe_uses_same_bounded_redacted_failure(self):
        err = io.StringIO()
        with mock.patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['synthetic'], stderr='private resolver details')) as run, mock.patch('time.sleep'), contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            exec(compile(private_git_script(), '<private-git>', 'exec'), {})
        self.assertEqual(run.call_count, 4)
        self.assertNotIn('private resolver details', err.getvalue())

    def test_readiness_is_shared_without_changing_product_execution(self):
        for name in ('repository', 'apple'):
            workflow = (ROOT / '.github/workflows' / (name + '.yml')).read_text()
            self.assertIn('StreamScapeTV/ci-workflows/actions/private-git@main', workflow)
        workflow = yaml.safe_load((ROOT / '.github/workflows/repository.yml').read_text())
        steps = workflow['jobs']['execute']['steps']
        for name in ('Cleanup ephemeral registry and repository evidence', 'Finalize bounded Agent State progress relay'):
            step = next(step for step in steps if step.get('name') == name)
            self.assertIn('always()', step['if'])
            self.assertNotIn('success()', step['if'])

    def test_drive_oauth_empty_or_invalid_json_fails_safely(self):
        script = drive_auth_script()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            fakebin = path / 'bin'; fakebin.mkdir()
            attempts = path / 'attempts'
            curl = fakebin / 'curl'
            curl.write_text(r'''#!/usr/bin/env bash
set -e
output=""
while test "$#" -gt 0; do
  if test "$1" = --output; then output="$2"; shift; fi
  shift
done
count="$(cat "$FAKE_COUNT" 2>/dev/null || echo 0)"
count=$((count+1)); printf '%s' "$count" > "$FAKE_COUNT"
if test "$FAKE_VALID_ON_THIRD" = true && test "$count" -eq 3; then
  printf '%s' '{"access_token":"synthetic_google_access_token_0123456789"}' > "$output"
  printf '200'
else
  printf 'not json' > "$output"
  printf '503'
fi
''')
            curl.chmod(0o755)
            sleep = fakebin / 'sleep'; sleep.write_text('#!/usr/bin/env bash\nexit 0\n'); sleep.chmod(0o755)
            env = {**os.environ, 'PATH': str(fakebin) + os.pathsep + os.environ['PATH'], 'GOOGLE_DRIVE_CLIENT_ID':'client-fixture', 'GOOGLE_DRIVE_CLIENT_SECRET':'client-secret-fixture', 'GOOGLE_DRIVE_REFRESH_TOKEN':'refresh-fixture', 'FAKE_COUNT':str(attempts), 'FAKE_VALID_ON_THIRD':'false', 'RUNNER_TEMP':str(path)}
            fail = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n'+script], cwd=ROOT, env=env, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(fail.returncode, 0)
            self.assertIn('credential exchange failed after bounded attempts', fail.stderr)
            self.assertNotIn('Traceback', fail.stderr)
            self.assertNotIn('client-secret-fixture', fail.stdout+fail.stderr)
            self.assertEqual(attempts.read_text(), '11')
            attempts.unlink()
            success = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n'+script], cwd=ROOT, env=dict(env, FAKE_VALID_ON_THIRD='true'), text=True, capture_output=True, timeout=10)
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(attempts.read_text(), '3')
            self.assertIn('::add-mask::synthetic_google_access_token_0123456789', success.stdout)
            self.assertIn('curl=0 http=503 category=invalid_token_response', success.stderr)

    def test_drive_empty_transport_and_invalid_token_shapes_never_leak(self):
        script = drive_auth_script()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            fakebin = path / 'bin'; fakebin.mkdir()
            curl = fakebin / 'curl'
            curl.write_text(r'''#!/usr/bin/env bash
output=""
while test "$#" -gt 0; do
  if test "$1" = --output; then output="$2"; shift; fi
  shift
done
printf 'attempt\n' >> "$FAKE_COUNT"
printf '%s' "$FAKE_BODY" > "$output"
printf '200'
echo 'private transport credential-detail' >&2
exit "$FAKE_EXIT"
''')
            curl.chmod(0o755)
            sleep = fakebin / 'sleep'; sleep.write_text('#!/usr/bin/env bash\nexit 0\n'); sleep.chmod(0o755)
            attempts = path / 'attempts'
            env = {**os.environ, 'PATH': str(fakebin) + os.pathsep + os.environ['PATH'], 'GOOGLE_DRIVE_CLIENT_ID':'client-fixture', 'GOOGLE_DRIVE_CLIENT_SECRET':'client-secret-fixture', 'GOOGLE_DRIVE_REFRESH_TOKEN':'refresh-fixture', 'FAKE_COUNT':str(attempts), 'RUNNER_TEMP':str(path)}
            for body, status in [('', '6'), ('[]', '0'), ('{}', '0'), ('{"access_token":42}', '0'), ('{"access_token":"bad token"}', '0')]:
                with self.subTest(body=body, status=status):
                    attempts.write_text('')
                    result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + script], env=dict(env, FAKE_BODY=body, FAKE_EXIT=status), text=True, capture_output=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(attempts.read_text().splitlines()), 11 if status != '0' else 1)
                    self.assertIn('category=invalid_token_response', result.stderr)
                    self.assertNotIn('private transport credential-detail', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
