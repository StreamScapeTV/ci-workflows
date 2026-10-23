from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/apple.yml').read_text())
STEPS = {s.get('name'): s for s in WORKFLOW['jobs']['execute']['steps'] if s.get('name')}
COMMAND = STEPS['Run fixed Apple lane']


class AppleLogCheckpointTests(unittest.TestCase):
    def test_seed_and_terminal_upload_share_exact_private_object_identity(self):
        seed = STEPS['Seed stable private Apple CI log object']
        final = STEPS['Upload CI log to Google Drive']
        for key in ('repository', 'ref', 'file_name', 'mime_type'):
            self.assertEqual(seed['with'][key], final['with'][key])
        self.assertTrue(seed['continue-on-error'])
        names = list(STEPS)
        self.assertLess(names.index('Prepare private Apple log checkpoint seed'), names.index('Run fixed Apple lane'))
        self.assertIn('source=%s', STEPS['Prepare private Apple log checkpoint seed']['run'])
        self.assertIn('steps.source_identity.outputs.source_sha', STEPS['Prepare private Apple log checkpoint seed']['env']['SOURCE_SHA'])
        self.assertIn('inputs.upload_private_log', STEPS['Check out Central Apple helpers']['if'])

    def test_checkpoint_scrubs_same_existing_secrets_before_external_upload(self):
        scrub = STEPS['Scrub configured CI secrets from private log']['env']
        for key, value in scrub.items():
            if key == 'CI_SECRET_MOBILE_BUMP_TOKEN':  # created after native execution
                continue
            self.assertEqual(COMMAND['env'][key], value)
        self.assertIn('repository_log_checkpoint.py', COMMAND['run'])
        self.assertIn('steps.log_seed.outputs.file_id', COMMAND['env']['CHECKPOINT_FILE_ID'])
        self.assertIn('steps.log_seed.outputs.file_sha256', COMMAND['env']['CHECKPOINT_SEED_SHA256'])
        self.assertIn("steps.log_seed.outcome == 'success'", COMMAND['env']['CHECKPOINT_ENABLED'])

    def test_lane_exit_joins_checkpoint_and_preserves_success_or_failure(self):
        prefix = COMMAND['run'].split('run_logged() {', 1)[0]
        for status in (0, 23):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                helper = root / 'central-ci/scripts/ci/repository_log_checkpoint.py'
                helper.parent.mkdir(parents=True)
                # External uploader process double: tests the actual workflow's
                # launch/signal/join path; real scrub/upload behavior has shared tests.
                helper.write_text('import os,signal,time\nfrom pathlib import Path\n'
                                  'signal.signal(signal.SIGTERM, lambda *_: exit(0))\n'
                                  'Path(os.environ["RUNNER_TEMP"],"started").touch()\n'
                                  'while True: time.sleep(.01)\n')
                script = prefix + '\nwhile ! test -f "$RUNNER_TEMP/started"; do sleep .01; done\n'
                script += 'exit ' + str(status) + '\n'
                result = subprocess.run(['bash', '-c', script], cwd=root, env={**os.environ,
                    'CENTRAL_APPLE_HELPERS': str(root / 'central-ci'), 'CI_LOG': str(root / 'log'), 'RUNNER_TEMP': td, 'CHECKPOINT_ENABLED': 'true',
                    'CHECKPOINT_FILE_ID': 'fixedPrivateFile', 'CHECKPOINT_SEED_SHA256': '0' * 64,
                    'CHECKPOINT_GOOGLE_DRIVE_CLIENT_ID': 'fixture-client',
                    'CHECKPOINT_GOOGLE_DRIVE_CLIENT_SECRET': 'fixture-secret',
                    'CHECKPOINT_GOOGLE_DRIVE_REFRESH_TOKEN': 'fixture-refresh'},
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertNotIn('fixture-secret', result.stdout + result.stderr)
