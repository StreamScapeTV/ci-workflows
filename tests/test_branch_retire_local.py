import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/ci'))
import branch_retire_local as local

class LocalBranchRetirementTests(unittest.TestCase):
    def setUp(self):
        self.client=Mock()
        self.client.exact_folders.side_effect=lambda parent,name: ([{'id':'repo'}] if parent=='root' else [])
        self.client._json.return_value={'id':'ref','trashed':True}
        self.calls=[]
        def deletion(client,**kwargs):
            self.calls.append(('drive',isinstance(client,local.PreflightDrive),kwargs))
            client.trash('ref')
            return 'trashed'
        self.delete=patch.object(local,'delete_snapshot',side_effect=deletion).start()
        self.drive=patch.object(local,'DriveClient',return_value=self.client).start()
        self.run=patch.object(local.subprocess,'run',return_value=Mock(returncode=0)).start()
        self.absent=patch.object(local,'branch_absent',return_value=True).start()
        self.addCleanup(patch.stopall)

    def retire(self,**changes):
        args=dict(github_token='synthetic-gh',drive_token='synthetic-drive',root_folder_id='root')
        args.update(changes)
        return local.retire('StreamScapeTV/example','issue-1-fix','a'*40,**args)

    def test_production_guard_is_used_verbatim_with_all_protections(self):
        guard=local.workflow_guard()
        for marker in ['validate_live_state()', 'repository live default branch', 'expected head is stale',
                       'protected branch', 'protected by a deletion rule', 'range(1, 11)']:
            self.assertIn(marker,guard)
        compile(guard,'fixed-production-guard','exec')

    def test_pair_is_preflighted_revalidated_and_read_back(self):
        result=self.retire()
        self.assertTrue(result['drive_verified'])
        self.assertFalse(result['actions_requested'])
        self.assertEqual([c[1] for c in self.calls],[True,False])
        self.assertEqual(self.client.trash.call_count,1)
        self.assertEqual(self.absent.call_count,2)
        self.assertEqual(self.run.call_count,2)
        invocation=self.run.call_args
        self.assertEqual(invocation.args[0][0],sys.executable)
        self.assertNotIn('synthetic-gh',' '.join(invocation.args[0]))
        self.assertEqual(invocation.kwargs['env']['TARGET_BRANCH'],'issue-1-fix')
        self.assertEqual(invocation.kwargs['env']['EXPECTED_HEAD'],'a'*40)
        self.client._json.assert_called_once_with('/files/ref?fields=id,trashed')

    def test_missing_credentials_stop_before_any_remote_or_guard_work(self):
        for key in ['github_token','drive_token','root_folder_id']:
            with self.subTest(key=key),self.assertRaises(local.SnapshotDeleteError): self.retire(**{key:''})
        self.drive.assert_not_called()
        self.run.assert_not_called()

    def test_namespace_content_refusal_prevents_branch_mutation(self):
        self.delete.side_effect=local.SnapshotDeleteError('ambiguous ref')
        with self.assertRaises(local.SnapshotDeleteError): self.retire()
        self.assertEqual(self.run.call_count,1,'only git ref-format validation occurred')
        self.client.trash.assert_not_called()

    def test_branch_guard_refusal_preserves_drive(self):
        self.run.side_effect=[Mock(returncode=0),Mock(returncode=1)]
        with self.assertRaises(local.SnapshotDeleteError): self.retire()
        self.assertEqual(self.delete.call_count,1)
        self.client.trash.assert_not_called()

    def test_branch_absence_required_before_trash(self):
        self.absent.return_value=False
        with self.assertRaises(local.SnapshotDeleteError): self.retire()
        self.client.trash.assert_not_called()

    def test_reappearing_branch_and_unconfirmed_trash_are_incomplete(self):
        self.absent.side_effect=[True,False]
        with self.assertRaises(local.SnapshotDeleteError): self.retire()
        self.absent.side_effect=None
        for metadata in [{'id':'other','trashed':True},{'id':'ref','trashed':False}]:
            self.client._json.return_value=metadata
            with self.assertRaises(local.SnapshotDeleteError): self.retire()

    def test_fixed_integration_names_and_malformed_scope_refuse(self):
        for branch in ['main','develop','refs/heads/issue-1-fix']:
            with self.assertRaises(local.SnapshotDeleteError):
                local.retire('StreamScapeTV/example',branch,'a'*40,github_token='x',drive_token='x',root_folder_id='root')
        self.client.trash.assert_not_called()
        self.run.assert_not_called()

if __name__=='__main__': unittest.main()
