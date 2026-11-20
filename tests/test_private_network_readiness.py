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


def private_git_script() -> str:
    runner = PRIVATE_GIT['runs']['steps'][-1]['run']
    return runner.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]


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


if __name__ == '__main__':
    unittest.main()
