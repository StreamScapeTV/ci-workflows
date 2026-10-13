"""Synthetic bounded readiness regression; never uses real private endpoints."""
from __future__ import annotations

import contextlib
import io
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
    start = script.index('        # A transient DNS/transport outage') if '        # A transient DNS/transport outage' in script else script.index('# A transient DNS/transport outage')
    end = script.index('echo "::add-mask::${access_token}"', start) + len('echo "::add-mask::${access_token}"')
    return script[start:end]


class PrivateNetworkReadinessTests(unittest.TestCase):
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
            curl.write_text('#!/usr/bin/env bash\nset -e\ncount="$(cat "$FAKE_COUNT" 2>/dev/null || echo 0)"\ncount=$((count+1))\nprintf "%s" "$count" > "$FAKE_COUNT"\nif test "$FAKE_VALID_ON_THIRD" = true && test "$count" -eq 3; then\n  echo \'{"access_token":"synthetic_google_access_token_0123456789"}\'\nelse\n  echo \'not json\'\nfi\n')
            curl.chmod(0o755)
            sleep = fakebin / 'sleep'; sleep.write_text('#!/usr/bin/env bash\nexit 0\n'); sleep.chmod(0o755)
            env = {**os.environ, 'PATH': str(fakebin) + os.pathsep + os.environ['PATH'], 'GOOGLE_DRIVE_CLIENT_ID':'client-fixture', 'GOOGLE_DRIVE_CLIENT_SECRET':'client-secret-fixture', 'GOOGLE_DRIVE_REFRESH_TOKEN':'refresh-fixture', 'FAKE_COUNT':str(attempts), 'FAKE_VALID_ON_THIRD':'false'}
            fail = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n'+script], cwd=ROOT, env=env, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(fail.returncode, 0)
            self.assertIn('credential exchange failed after bounded attempts', fail.stderr)
            self.assertNotIn('Traceback', fail.stderr)
            self.assertNotIn('client-secret-fixture', fail.stdout+fail.stderr)
            self.assertEqual(attempts.read_text(), '3')
            attempts.unlink()
            success = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n'+script], cwd=ROOT, env=dict(env, FAKE_VALID_ON_THIRD='true'), text=True, capture_output=True, timeout=10)
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(attempts.read_text(), '3')
            self.assertIn('::add-mask::synthetic_google_access_token_0123456789', success.stdout)
            self.assertEqual(success.stderr, '')

    def test_drive_empty_transport_and_invalid_token_shapes_never_leak(self):
        script = drive_auth_script()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            fakebin = path / 'bin'; fakebin.mkdir()
            curl = fakebin / 'curl'
            curl.write_text('#!/usr/bin/env bash\nprintf "attempt\\n" >> "$FAKE_COUNT"\nprintf "%s" "$FAKE_BODY"\necho "private transport credential-detail" >&2\nexit "$FAKE_EXIT"\n')
            curl.chmod(0o755)
            sleep = fakebin / 'sleep'; sleep.write_text('#!/usr/bin/env bash\nexit 0\n'); sleep.chmod(0o755)
            attempts = path / 'attempts'
            env = {**os.environ, 'PATH': str(fakebin) + os.pathsep + os.environ['PATH'], 'GOOGLE_DRIVE_CLIENT_ID':'client-fixture', 'GOOGLE_DRIVE_CLIENT_SECRET':'client-secret-fixture', 'GOOGLE_DRIVE_REFRESH_TOKEN':'refresh-fixture', 'FAKE_COUNT':str(attempts)}
            for body, status in [('', '6'), ('[]', '0'), ('{}', '0'), ('{"access_token":42}', '0'), ('{"access_token":"bad token"}', '0')]:
                with self.subTest(body=body, status=status):
                    attempts.write_text('')
                    result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + script], env=dict(env, FAKE_BODY=body, FAKE_EXIT=status), text=True, capture_output=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(attempts.read_text().splitlines()), 3)
                    self.assertEqual(result.stderr.strip(), 'Google Drive credential exchange failed after bounded attempts')
                    self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
