"""Execute Central's default auth lifecycle with synthetic registry/tool boundaries."""
from __future__ import annotations
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / 'actions/dockerhub-read/action.yml').read_text())
SCRIPT = ACTION['runs']['steps'][0]['run'].split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]

class DockerHubPullTests(unittest.TestCase):
    def execute(self, directory, phase='setup', tool='docker', failure=None, **extra):
        env = {'RUNNER_TEMP': str(directory), 'GITHUB_ENV': str(directory / 'env'), 'AUTH_PHASE': phase,
               'DOCKERHUB_USERNAME': 'synthetic-reader', 'DOCKERHUB_READ_TOKEN': 'synthetic-token-0123456789', **extra}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch('shutil.which', side_effect=lambda name: '/synthetic/' + name if name == tool else None), mock.patch('subprocess.run', side_effect=failure, return_value=subprocess.CompletedProcess([], 0)) as call, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                exec(compile(SCRIPT, '<default-dockerhub-auth>', 'exec'), {})
                code = 0
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue(), call.call_args_list

    def test_default_auth_and_cleanup_for_docker_buildah_and_skopeo(self):
        for tool in ['docker', 'buildah', 'skopeo']:
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp); (directory / 'env').touch()
                code, out, err, calls = self.execute(directory, tool=tool)
                self.assertEqual(code, 0, err)
                self.assertEqual(len(calls), 1)
                command = calls[0].args[0]
                self.assertEqual(command[0], tool)
                self.assertEqual(command[-1], 'docker.io')
                self.assertNotIn('synthetic-token-0123456789', command)
                self.assertEqual(calls[0].kwargs['input'], b'synthetic-token-0123456789')
                self.assertTrue(calls[0].kwargs['check'])
                self.assertEqual(calls[0].kwargs['timeout'], 60)
                self.assertEqual(calls[0].kwargs['stderr'], subprocess.DEVNULL)
                root = directory / 'central-dockerhub-read'
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                encoded = base64.b64encode(b'synthetic-reader:synthetic-token-0123456789').decode()
                for name in ['auth.json', 'config.json']:
                    self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), 0o600)
                    self.assertEqual(json.loads((root / name).read_text())['auths']['docker.io']['auth'], encoded)
                self.assertNotIn('synthetic-token', (directory / 'env').read_text())
                self.assertEqual(len(out.splitlines()), 3)
                code, _, err, _ = self.execute(directory, 'cleanup')
                self.assertEqual(code, 0, err)
                self.assertFalse(root.exists())
                self.assertIn('DOCKER_CONFIG=\n', (directory / 'env').read_text())

    def test_rejected_token_and_timeout_fail_redacted_without_exports(self):
        for failure in [subprocess.CalledProcessError(1, ['synthetic'], stderr='private provider details'), subprocess.TimeoutExpired(['synthetic'], 60)]:
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp); (directory / 'env').touch()
                code, out, err, calls = self.execute(directory, failure=failure)
                self.assertEqual(code, 1)
                self.assertEqual(err, 'Docker Hub read authentication setup failed\n')
                self.assertEqual((directory / 'env').read_text(), '')
                self.assertFalse((directory / 'central-dockerhub-read').exists())
                self.assertEqual(len(calls), 1)
                self.assertNotIn('private provider details', out + err)

    def test_missing_credentials_and_preexisting_config_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); (directory / 'env').touch()
            code, out, _, calls = self.execute(directory, DOCKERHUB_READ_TOKEN='')
            self.assertEqual(code, 1); self.assertEqual(out, ''); self.assertEqual(calls, [])
            root = directory / 'central-dockerhub-read'; root.mkdir(); (root / 'existing').write_text('do not borrow')
            code, _, _, calls = self.execute(directory)
            self.assertEqual(code, 1); self.assertEqual(calls, [])
            self.assertEqual((root / 'existing').read_text(), 'do not borrow')

    def test_all_pull_paths_use_default_setup_and_always_cleanup(self):
        for workflow, job in [('repository', 'execute'), ('python', 'ci'), ('public-native-image-chart', 'publish'), ('oci-reproducibility', 'prove')]:
            with self.subTest(workflow=workflow):
                value = yaml.safe_load((ROOT / '.github/workflows' / (workflow + '.yml')).read_text())
                steps = value['jobs'][job]['steps']
                setup = next(s for s in steps if s.get('id') == 'dockerhub_read')
                cleanup = next(s for s in steps if s.get('id') == 'dockerhub_read_cleanup')
                self.assertNotIn('capabilities', setup.get('if', ''))
                self.assertEqual(setup['uses'], 'StreamScapeTV/ci-workflows/actions/dockerhub-read@main')
                self.assertIn('always()', cleanup['if'])
                self.assertEqual(cleanup['with'], {'phase': 'cleanup'})
                for secret in ['DOCKERHUB_USERNAME', 'DOCKERHUB_READ_TOKEN']:
                    self.assertIn(secret, value['on']['workflow_call']['secrets'])
                self.assertIn('CI_SECRET_DOCKERHUB_READ_TOKEN', (ROOT / '.github/workflows' / (workflow + '.yml')).read_text())
        contract = json.loads((ROOT / 'contracts/repository-ci-v1.json').read_text())
        self.assertNotIn('dockerhub_public_pull', contract['capabilityTypes'])
        dispatch = yaml.safe_load((ROOT / '.github/workflows/central-ci-dispatch.yml').read_text())
        for job in ['repository', 'repository_release']:
            self.assertEqual(dispatch['jobs'][job]['secrets']['DOCKERHUB_READ_TOKEN'], '${{ secrets.DOCKERHUB_READ_TOKEN }}')
        plan = yaml.safe_load((ROOT / '.github/workflows/repository-plan.yml').read_text())
        self.assertNotIn('release', plan['jobs']['execute']['secrets']['DOCKERHUB_READ_TOKEN'])

    def test_private_evidence_scrubs_default_raw_and_derived_secrets(self):
        from tests.test_repository_workflow import RepositoryWorkflowTests
        harness = RepositoryWorkflowTests(methodName='test_private_agent_state_capability_grant_is_exactly_bound_and_fail_closed')
        harness.setUpClass()
        with harness.evidence_fixture() as (directory, env):
            user, token = 'synthetic-reader', 'synthetic-token-0123456789'
            encoded = base64.b64encode((user + ':' + token).encode()).decode()
            env.update(CI_SECRET_DOCKERHUB_USERNAME=user, CI_SECRET_DOCKERHUB_READ_TOKEN=token)
            (directory / 'central.log').write_text('raw ' + token + ' encoded ' + encoded + '\n')
            result = harness.execute_evidence_fixture(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(token, (directory / 'central.log').read_text())
            self.assertNotIn(encoded, (directory / 'central.log').read_text())
            self.assertIn('[REDACTED]', (directory / 'central.log').read_text())

if __name__ == '__main__':
    unittest.main()
