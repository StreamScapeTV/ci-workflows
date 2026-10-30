from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import yaml
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.ci.source_checkpoint_recover import recover, CheckpointPublishError
from scripts.ci.source_checkpoint_publish import checkpoint_filename

REPO = 'StreamScapeTV/example'
BRANCH = 'issue-42-recovery'
HEAD = 'a' * 40
TREE = 'b' * 40
PENDING = 'c' * 40


class Drive:
    def __init__(self, trees):
        self.items = [{'id': f'checkpoint_file_{i}', 'name': checkpoint_filename(REPO, BRANCH, 1),
                       'mimeType': 'application/zip'} for i in range(len(trees))]
        self.archives = dict(zip((x['id'] for x in self.items), trees))
        self.deleted = []
        self.fail_delete = False
        self.incomplete = False
        self.reads = 0

    def exact_folders(self, parent, name):
        return [{'id': 'canonical_folder_123', 'name': name, 'mimeType': 'application/vnd.google-apps.folder'}]

    def children(self, parent):
        self.reads += 1
        return [] if self.incomplete and self.reads > 1 else list(self.items)

    def media(self, identity):
        if self.archives[identity] == 'denied':
            raise CheckpointPublishError('missing access')
        return self.archives[identity].encode()

    def delete(self, identity):
        if self.fail_delete:
            raise CheckpointPublishError('cleanup failed')
        self.deleted.append(identity)
        self.items = [x for x in self.items if x['id'] != identity]


class Github:
    repo_path = '/repos/StreamScapeTV/example'
    head = HEAD

    def _json(self, path):
        if path == self.repo_path:
            return {'full_name': REPO, 'default_branch': 'main'}
        return {'name': BRANCH, 'protected': False,
                'commit': {'sha': self.head, 'commit': {'tree': {'sha': TREE}}}}


def rows(children, repository, branch):
    result = {}
    for item in children:
        sequence = int(re.search(r'([0-9]{6})\.zip$', item['name'])[1])
        result.setdefault(sequence, []).append(item)
    return result


def rules():
    return SimpleNamespace(
        __file__='/canonical-rules/utilities/tools/checkpoint_recovery.py',
        REPO=re.compile(r'StreamScapeTV/[a-z]+'), safe_branch=lambda value, label: value,
        checkpoint_rows=rows, sha256=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
        materialize_and_tree=lambda path: (path.read_text(), None))


class RecoveryTests(unittest.TestCase):
    def run_recovery(self, drive, plan=None, github=None, operation='settle'):
        def execute(command, **kwargs):
            self.assertIn('checkpoint_recovery.py', command[1])
            self.assertIn('--expected-head', command)
            return SimpleNamespace(returncode=0 if plan['ok'] else 2, stdout=json.dumps(plan))
        with contextlib.redirect_stdout(io.StringIO()), patch('scripts.ci.source_checkpoint_recover.subprocess.run', side_effect=execute), patch('scripts.ci.source_checkpoint_recover.subprocess.check_output', return_value='d' * 40):
            return recover(drive, github or Github(), root_folder_id='root_folder_123',
                           repository=REPO, branch=BRANCH, head=HEAD, tree=TREE,
                           operation=operation, rules=rules())

    def plan(self, *identities):
        return {'ok': True, 'connector_sequence': [
            {'action': 'Google_Drive.delete_file', 'payload': {'url': f'https://drive.google.com/file/d/{identity}/view'}}
            for identity in identities]}

    def test_no_checkpoint_is_verified_settled(self):
        receipt = self.run_recovery(Drive([]))
        self.assertTrue(receipt['readback_verified'])
        self.assertEqual(receipt['next_sequence'], 1)

    def test_already_settled_unique_checkpoint_is_retained(self):
        drive = Drive([TREE])
        receipt = self.run_recovery(drive)
        self.assertEqual(receipt['next_sequence'], 2)
        self.assertEqual(drive.deleted, [])

    def test_identical_duplicates_delete_only_helper_selected_copy(self):
        drive = Drive([TREE, TREE])
        receipt = self.run_recovery(drive, self.plan('checkpoint_file_1'))
        self.assertEqual(receipt['deleted_ids'], ['checkpoint_file_1'])
        self.assertEqual(len(drive.items), 1)

    def test_mixed_published_pending_retains_unique_pending(self):
        drive = Drive([TREE, PENDING])
        receipt = self.run_recovery(drive, self.plan('checkpoint_file_0'))
        self.assertEqual(drive.items[0]['id'], 'checkpoint_file_1')
        self.assertTrue(receipt['unique_sequences'])

    def test_conflict_preserves_every_archive(self):
        drive = Drive([PENDING, 'e' * 40])
        with self.assertRaisesRegex(CheckpointPublishError, 'preserved'):
            self.run_recovery(drive, {'ok': False})
        self.assertEqual(drive.deleted, [])

    def test_inspection_does_not_mutate(self):
        drive = Drive([TREE, TREE])
        receipt = self.run_recovery(drive, self.plan('checkpoint_file_1'), operation='inspect')
        self.assertEqual(drive.deleted, [])
        self.assertFalse(receipt['unique_sequences'])

    def test_stale_head_preserves_every_archive(self):
        github = Github(); github.head = 'f' * 40
        drive = Drive([TREE, TREE])
        with self.assertRaisesRegex(CheckpointPublishError, 'unchanged'):
            self.run_recovery(drive, self.plan('checkpoint_file_1'), github)
        self.assertEqual(drive.deleted, [])

    def test_missing_access_fails_before_cleanup(self):
        drive = Drive(['denied', TREE])
        with self.assertRaisesRegex(CheckpointPublishError, 'access'):
            self.run_recovery(drive, self.plan('checkpoint_file_1'))
        self.assertEqual(drive.deleted, [])

    def test_incomplete_readback_fails(self):
        drive = Drive([TREE, TREE]); drive.incomplete = True
        with self.assertRaisesRegex(CheckpointPublishError, 'listing changed'):
            self.run_recovery(drive, self.plan('checkpoint_file_1'))
        self.assertEqual(drive.deleted, [])

    def test_failed_cleanup_is_not_success(self):
        drive = Drive([TREE, TREE]); drive.fail_delete = True
        with self.assertRaisesRegex(CheckpointPublishError, 'cleanup failed'):
            self.run_recovery(drive, self.plan('checkpoint_file_1'))
        self.assertEqual(len(drive.items), 2)


class RecoveryAdmissionTests(unittest.TestCase):
    def test_existing_transport_admits_recovery_and_legacy_publication(self):
        workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/central-ci-dispatch.yml').read_text())
        script = next(step['run'] for step in workflow['jobs']['request']['steps']
                      if step.get('name') == 'Validate canonical source checkpoint publication request')
        identity = {'expected_head': HEAD, 'expected_tree': TREE}
        cases = [(dict(identity, operation='inspect'), 0), (dict(identity, operation='settle'), 0),
                 (dict(identity, archive_sha256='d' * 64, archive_size_bytes=1, commit_message='test'), 0),
                 (dict(identity, operation='settle', file_id='forbidden'), 1),
                 (dict(identity, operation='arbitrary'), 1)]
        for inputs, expected in cases:
            with self.subTest(inputs=inputs):
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                    env=dict(os.environ, TEST_PROFILE='publish', REQUEST_IS_TAG='false',
                             REQUEST_REF=BRANCH, INPUTS_JSON=json.dumps(inputs)))
                self.assertEqual(result.returncode, expected, result.stderr)
