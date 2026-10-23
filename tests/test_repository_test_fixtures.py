"""Shared fixture admission/materialization; product capture remains product-owned."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import zipfile

import yaml
from tests import test_repository_workflow as workflow_tests
from tests import test_repository_log_checkpoint as checkpoint_tests

ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    'STREAMSCAPE_API_BASE', 'STREAMSCAPE_EMAIL', 'STREAMSCAPE_PASSWORD',
    'STREAMSCAPE_DEMO_EMAIL', 'STREAMSCAPE_DEMO_PASSWORD',
    'XTREAM_URL', 'XTREAM_USERNAME', 'XTREAM_PASSWORD',
)


class RepositoryTestFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow_tests.RepositoryWorkflowTests.setUpClass()
        cls.harness = workflow_tests.RepositoryWorkflowTests()

    def test_existing_request_grant_controls_fixture_capability(self):
        state = {'repository_ci': {
            'schemaVersion': 1, 'repository': 'ExampleOrg/apple-application',
            'capabilities': ['integration_test_fixtures'],
        }}
        for operation in ('test', 'full', 'ui-test', 'build', 'release'):
            with self.subTest(operation=operation):
                result, values = self.harness.run_capability_resolver(
                    operation=operation, project_state=state,
                    run_ref='1.2.3' if operation == 'release' else 'feature',
                    run_is_tag=operation == 'release',
                    run_profile='linux' if operation == 'release' else operation,
                    run_workflow='release.repository' if operation == 'release' else 'validation.repository',
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values['integration_test_fixtures'],
                                 'true' if operation in ('test', 'full', 'ui-test') else 'false')
        for arguments in ({'ci_run_id': ''}, {'project_state': {}}):
            result, values = self.harness.run_capability_resolver(operation='ui-test', **arguments)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(values['integration_test_fixtures'], 'false')

    def test_actual_materialization_and_always_cleanup_keep_credentials_private(self):
        steps = self.harness.steps_by_name
        step = steps['Materialize reviewed integration-test fixtures']
        self.assertEqual(step['if'], "${{ steps.capabilities.outputs.integration_test_fixtures == 'true' }}")
        self.assertEqual(set(step['env']), set(FIELDS))
        redactors = [value for value in steps.values() if 'CI_SECRET_REGISTRY_READ_TOKEN' in value.get('env', {})]
        self.assertEqual(len(redactors), 2)
        for redactor in redactors:
            for field in FIELDS:
                self.assertEqual(redactor['env']['CI_SECRET_FIXTURE_' + field],
                                 "${{ steps.capabilities.outputs.integration_test_fixtures == 'true' && secrets." + field + " || '' }}")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'central-ci').symlink_to(ROOT, target_is_directory=True)
            github_env = root / 'github-env'
            env = {**os.environ, **{name: 'synthetic-' + name for name in FIELDS},
                   'RUNNER_TEMP': td, 'GITHUB_ENV': str(github_env)}
            result = subprocess.run(['bash', '-c', step['run']], cwd=root, env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = root / 'central-test-fixtures.json'
            self.assertEqual(json.loads(path.read_text()), {name: env[name] for name in FIELDS})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(github_env.read_text(), f'CI_TEST_FIXTURE_FILE={path}\n')
            self.assertNotIn('synthetic-', result.stdout + result.stderr + github_env.read_text())
            cleanup = steps['Cleanup ephemeral registry and repository evidence']
            self.assertEqual(cleanup['if'], '${{ always() }}')
            result = subprocess.run(['bash', '-c', cleanup['run']], cwd=root, env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(path.exists())

    def test_delivered_json_is_scrubbed_from_checkpoints_and_final_evidence(self):
        secret = 'synthetic-"quoted"\\slash-ü\nnext'
        encoded = json.dumps(secret)[1:-1].encode()
        with self.harness.evidence_fixture() as (root, env):
            env['STREAMSCAPE_PASSWORD'] = secret
            env['CI_SECRET_FIXTURE_STREAMSCAPE_PASSWORD'] = secret
            subprocess.run(['python3', str(ROOT / 'scripts/ci/repository_test_fixtures.py')],
                           env=env, check=True, capture_output=True)
            delivered = (root / 'central-test-fixtures.json').read_bytes()
            self.assertIn(encoded, delivered)
            log = Path(env['CI_LOG'])
            log.write_bytes(delivered)
            scrubbed = checkpoint_tests.mod.scrubbed_snapshot(log, env)
            self.assertNotIn(encoded, scrubbed)
            self.assertIn(b'[REDACTED]', scrubbed)
            (root / 'artifacts' / 'fixture.json').write_bytes(delivered)
            result = self.harness.execute_evidence_fixture(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(encoded, log.read_bytes())
            with zipfile.ZipFile(root / 'central-repository-ci-evidence.zip') as archive:
                self.assertFalse(any(name.endswith('fixture.json') for name in archive.namelist()))
                for name in archive.namelist():
                    self.assertNotIn(encoded, archive.read(name), name)

    def test_fixture_requirements_remain_product_owned(self):
        with tempfile.TemporaryDirectory() as td:
            env = {k: v for k, v in os.environ.items() if k not in FIELDS}
            env['RUNNER_TEMP'] = td
            subprocess.run(['python3', str(ROOT / 'scripts/ci/repository_test_fixtures.py')],
                           env=env, check=True, capture_output=True)
            self.assertEqual(json.loads((Path(td) / 'central-test-fixtures.json').read_text()),
                             dict.fromkeys(FIELDS, ''))

    def test_fixed_bundle_is_forwarded_through_existing_workflow_chain(self):
        workflows = {name: yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
                     for name in ('central-ci-dispatch.yml', 'repository-plan.yml', 'repository.yml')}
        for name in ('repository-plan.yml', 'repository.yml'):
            declarations = workflows[name]['on']['workflow_call']['secrets']
            self.assertTrue(set(FIELDS) <= declarations.keys())
        for name, job in (('central-ci-dispatch.yml', 'repository'), ('repository-plan.yml', 'execute')):
            forwarding = workflows[name]['jobs'][job]['secrets']
            for field in FIELDS:
                self.assertEqual(forwarding[field], '${{ secrets.' + field + ' }}')
