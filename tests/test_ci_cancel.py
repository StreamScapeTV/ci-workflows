import copy
import io
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

import yaml

from scripts.ci import ci_cancel as subject

ROOT = Path(__file__).resolve().parents[1]
CONTROL = '00000000-0000-4000-8000-000000000001'
TARGET = '00000000-0000-4000-8000-000000000002'
STAMP = '2026-10-17T00:00:00+00:00'


class CancellationTests(unittest.TestCase):
    def setUp(self):
        common = dict(project_key='example', repository='example/app', ref='develop')
        self.control = dict(common, ci_run_id=CONTROL, status='running',
                            workflow_key='maintenance.ci-cancel', test_profile='cancel',
                            inputs=dict(target_ci_run_id=TARGET, requested_at=STAMP))
        self.target = dict(common, ci_run_id=TARGET, external_repository=subject.CENTRAL,
                           external_run_id=42, external_run_attempt=3,
                           cancellation_requested_at=STAMP, cancellation_accepted_at=None)
        self.plan = dict(ok=True, run=self.target)
        self.provider = [dict(id=42, run_attempt=3, status='in_progress')]
        self.http_status = 202
        self.calls = []
        self.record_reply = dict(ok=True)

    def rpc(self, name, payload):
        self.calls.append((name, copy.deepcopy(payload)))
        if name == 'claim_ci_run':
            self.assertEqual(payload, dict(p_ci_run_id=CONTROL))
            return dict(ok=True, run=self.control, replayed=True)
        if name == 'get_ci_cancellation':
            self.assertEqual(payload, dict(p_ci_run_id=TARGET))
            return self.plan
        if name == 'record_ci_cancellation':
            self.assertEqual(payload['p_ci_run_id'], TARGET)
            return self.record_reply
        self.fail('unexpected RPC ' + name)

    def github(self, method, path):
        self.calls.append((method, path))
        self.assertTrue(path.startswith('/repos/' + subject.CENTRAL + '/actions/runs/42'))
        if method == 'GET':
            return 200, self.provider.pop(0)
        self.assertEqual(path, '/repos/' + subject.CENTRAL + '/actions/runs/42/cancel')
        return self.http_status, None

    def run_cancel(self):
        return subject.cancel(CONTROL, self.rpc, self.github)

    def test_acceptance_records_only_target_and_is_not_completion(self):
        self.assertEqual(self.run_cancel(), dict(cancellation_requested=True, provider_terminal=False))
        record = self.calls[-1][1]
        self.assertEqual(record['p_result']['outcome'], 'accepted')
        self.assertEqual(record['p_result']['external_run_attempt'], 3)

    def test_terminal_races_preserve_actual_result(self):
        for conclusion, outcome in [('success', 'succeeded'), ('cancelled', 'cancelled'), ('failure', 'failed')]:
            with self.subTest(conclusion=conclusion):
                self.setUp()
                self.provider = [dict(id=42, run_attempt=3, status='completed', conclusion=conclusion)]
                self.assertTrue(self.run_cancel()['provider_terminal'])
                self.assertNotIn('POST', [x[0] for x in self.calls])
                self.assertEqual(self.calls[-1][1]['p_result']['outcome'], outcome)

    def test_409_rechecks_once_and_does_not_invent_acceptance(self):
        self.http_status = 409
        self.provider.append(dict(id=42, run_attempt=3, status='completed', conclusion='cancelled'))
        self.assertTrue(self.run_cancel()['provider_terminal'])
        self.assertEqual([x[0] for x in self.calls].count('GET'), 2)
        self.setUp()
        self.http_status = 409
        self.provider.append(dict(id=42, run_attempt=3, status='in_progress'))
        with self.assertRaisesRegex(ValueError, 'outcome unknown'):
            self.run_cancel()
        self.assertNotIn('record_ci_cancellation', [x[0] for x in self.calls])

    def test_refused_plan_or_changed_correlation_never_calls_provider(self):
        for change in ('plan', 'stamp', 'project', 'self', 'extra', 'profile'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'plan': self.plan = dict(ok=False, code='ci_run_not_stale')
                if change == 'stamp': self.target['cancellation_requested_at'] = 'changed'
                if change == 'project': self.target['project_key'] = 'other'
                if change == 'self': self.control['inputs']['target_ci_run_id'] = CONTROL
                if change == 'extra': self.control['inputs']['url'] = 'not allowed'
                if change == 'profile': self.control['test_profile'] = 'other'
                with self.assertRaises(ValueError): self.run_cancel()
                self.assertNotIn('GET', [x[0] for x in self.calls])

    def test_changed_attempt_or_unknown_status_never_posts(self):
        for provider in [dict(id=42, run_attempt=4, status='in_progress'),
                         dict(id=42, run_attempt=3, status='unknown'),
                         dict(id=42, run_attempt=3, status='completed', conclusion='unknown')]:
            self.setUp()
            self.provider = [provider]
            with self.assertRaises(ValueError): self.run_cancel()
            self.assertNotIn('POST', [x[0] for x in self.calls])

    def test_duplicate_acceptance_and_terminal_target_are_quiet(self):
        self.target['cancellation_accepted_at'] = STAMP
        self.assertTrue(self.run_cancel()['cancellation_requested'])
        self.assertNotIn('GET', [x[0] for x in self.calls])
        self.setUp()
        self.plan = dict(ok=False, code='already_terminal')
        self.assertTrue(self.run_cancel()['already_terminal'])
        self.assertNotIn('GET', [x[0] for x in self.calls])

    def test_failed_recording_or_transport_never_reports_success(self):
        self.record_reply = dict(ok=False, code='provider_or_request_mismatch')
        with self.assertRaisesRegex(ValueError, 'recording refused'): self.run_cancel()
        self.setUp()
        def timeout(*_args): raise TimeoutError('provider timeout')
        with self.assertRaises(TimeoutError): subject.cancel(CONTROL, self.rpc, timeout)
        self.assertNotIn('record_ci_cancellation', [x[0] for x in self.calls])

    def test_http_adapter_accepts_empty_202_and_409(self):
        response = io.BytesIO(b'')
        response.code = 202
        with patch.object(subject.urllib.request, 'urlopen', return_value=response):
            self.assertEqual(subject.http('POST', 'https://example.invalid', {}, expected=(202, 409)), (202, None))
        error = urllib.error.HTTPError('https://example.invalid', 409, 'conflict', {}, io.BytesIO(b''))
        with patch.object(subject.urllib.request, 'urlopen', side_effect=error):
            self.assertEqual(subject.http('POST', 'https://example.invalid', {}, expected=(202, 409)), (409, None))

    def test_route_permissions_and_settlement_keep_control_separate(self):
        dispatch = yaml.safe_load((ROOT/'.github/workflows/central-ci-dispatch.yml').read_text())
        route = dispatch['jobs']['ci_cancel']
        self.assertIn('maintenance.ci-cancel', route['if'])
        self.assertEqual(route['with'], {'ci_run_id': '${{ inputs.ci_run_id }}'})
        self.assertFalse(route['concurrency']['cancel-in-progress'])
        self.assertEqual(route['permissions']['actions'], 'write')
        self.assertNotIn('actions', dispatch['permissions'])
        settle = dispatch['jobs']['settle_cancelled']
        self.assertIn('ci_cancel', settle['needs'])
        self.assertIn("needs.ci_cancel.result == 'failure'", settle['if'])
        workflow = yaml.safe_load((ROOT/'.github/workflows/ci-cancel.yml').read_text())
        self.assertEqual(set(workflow['on']['workflow_call']['inputs']), {'ci_run_id'})
        self.assertEqual(set(workflow['on']['workflow_call']['secrets']),
                         {'AGENT_STATE_SUPABASE_URL', 'AGENT_STATE_SUPABASE_SECRET_KEY'})
        self.assertEqual(workflow['jobs']['cancel']['permissions']['actions'], 'write')
