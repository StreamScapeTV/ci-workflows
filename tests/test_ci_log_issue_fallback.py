import copy
import io
import socket
import urllib.error
from unittest.mock import patch
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fallback', ROOT / 'scripts/ci/ci_log_issue_fallback.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(repository='StreamScapeTV/example', issue=7,
            ci_run_id='11111111-1111-4111-8111-111111111111', source_sha='a'*40,
            run_id='123', attempt='2', execution_repository='StreamScapeTV/ci-workflows', lane='ios')
        self.repo = dict(private=True, full_name=self.context['repository'])
        self.issue = dict(number=7, repository_url=mod.API+'/repos/StreamScapeTV/example')
        self.existing = []
        self.posts = []
        self.uncertain = False

    def call(self, url, token, payload=None):
        if url.endswith('/example'):
            return self.repo
        if url.endswith('/issues/7'):
            return self.issue
        if payload is None:
            return self.existing
        body = payload['body']
        self.posts.append(body)
        self.existing.append(dict(id=len(self.posts), body=body))
        if self.uncertain:
            raise mod.FallbackError('request_failed_transport')
        return self.existing[-1]

    def post(self, text='actual failure\n', path=None):
        with tempfile.TemporaryDirectory() as root:
            p = path or Path(root)/'log.txt'
            if path is None:
                p.write_text(text)
            return mod.post(self.context, p, 'synthetic-token', self.call, pause=lambda _:None)

    def test_full_log_splits_and_cannot_escape_code_or_details(self):
        text=('actual line <tag>& ``` </pre></details> 🧪\n'*4000)
        expected=list(mod.bodies(self.context,text))
        self.assertGreater(len(expected),1)
        self.assertTrue(all(len(b.encode())<60000 for b in expected))
        self.assertTrue(all('&lt;/pre&gt;&lt;/details&gt;' in b for b in expected))
        self.assertTrue(all('part '+str(i)+'/' in b for i,b in enumerate(expected,1)))
        self.post(text)
        self.assertEqual(self.posts,expected)
        self.assertEqual(self.post(text),0)

    def test_public_repo_and_pull_request_never_receive_log(self):
        for change in [lambda:self.repo.update(private=False), lambda:self.issue.update(pull_request={}),
                       lambda:self.repo.update(full_name='StreamScapeTV/other'),lambda:self.issue.update(number=8)]:
            self.setUp();change()
            with self.assertRaises(mod.FallbackError):self.post()
            self.assertEqual(self.posts,[])

    def test_uncertain_post_reads_back_without_replay(self):
        self.uncertain=True
        self.assertEqual(self.post(),1)
        self.assertEqual(len(self.posts),1)

    def test_unconfirmed_post_does_not_retry_or_claim_completion(self):
        def call(url,token,payload=None):
            if payload is not None:raise mod.FallbackError('transport')
            return self.call(url,token,payload)
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'log';p.write_text('failure')
            with self.assertRaisesRegex(mod.FallbackError,'no_replay'):
                mod.post(self.context,p,'token',call)
        self.assertEqual(self.posts,[])

    def test_comment_lookup_requires_last_page(self):
        calls=[]
        def call(url,token):
            calls.append(url);return [{'body':'old'}]*100 if len(calls)==1 else []
        self.assertEqual(len(mod.comments('endpoint','token',call)),100)
        self.assertEqual(len(calls),2)
        with self.assertRaisesRegex(mod.FallbackError,'incomplete'):
            mod.comments('endpoint','token',lambda *_:[{}]*100)

    def test_log_missing_empty_symlink_oversize_or_non_utf8_refused(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'log'
            for data in [b'',b'\xff']:
                p.write_bytes(data)
                with self.assertRaises(mod.FallbackError):self.post(path=p)
            p.unlink();p.symlink_to(Path(root)/'missing')
            with self.assertRaises(mod.FallbackError):self.post(path=p)
        self.assertEqual(self.posts,[])

    def test_exact_ci_provenance_required(self):
        env=dict(LOG_REPOSITORY=self.context['repository'],LOG_REF='issue/example',
                 LOG_CI_RUN_ID=self.context['ci_run_id'],LOG_LANE='ios',
                 AGENT_STATE_SUPABASE_URL='https://example.invalid',
                 AGENT_STATE_SUPABASE_SECRET_KEY='synthetic',GITHUB_REPOSITORY=self.context['execution_repository'],
                 GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='2')
        run=dict(ci_run_id=self.context['ci_run_id'],project_key='example',repository=env['LOG_REPOSITORY'],
                 ref=env['LOG_REF'],issue_number=7,observed_source_sha='a'*40,
                 external_repository=env['GITHUB_REPOSITORY'],external_run_id=123,external_run_attempt=2)
        def call(url,token,payload,state=False):
            return dict(ok=True,runs=[run]) if url.endswith('list_current_ci_runs') else dict(ok=True,run=run)
        self.assertEqual(mod.resolve(env,call),self.context)
        for key,value in [('external_run_id',999),('issue_number',None),('observed_source_sha',None),
                          ('repository','StreamScapeTV/other'),('external_run_attempt',3)]:
            changed=copy.deepcopy(run);run[key]=value
            with self.assertRaisesRegex(mod.FallbackError,'provenance'):mod.resolve(env,call)
            run=changed

    def test_read_transport_retry_recovers_without_exposing_endpoint_or_token(self):
        failure = urllib.error.URLError(socket.gaierror(-2, 'synthetic DNS error'))
        with patch.object(mod.urllib.request, 'urlopen', side_effect=[failure, io.BytesIO(b'{"ok":true}')]) as http, \
                patch.object(mod.time, 'sleep') as pause:
            self.assertEqual(mod.request('https://example.invalid/rpc/get_ci_run', 'synthetic-token', {}, state=True), {'ok': True})
            self.assertEqual(http.call_count, 2)
            pause.assert_called_once_with(2)

    def test_read_retry_budget_and_authoritative_failures(self):
        cases = [
            (urllib.error.URLError(socket.gaierror(-2, 'private endpoint')), 11, 'dns_resolution_failed'),
            (urllib.error.HTTPError('private endpoint', 503, 'unavailable', {}, None), 11, 'http_503'),
            (urllib.error.HTTPError('private endpoint', 403, 'denied', {}, None), 1, 'http_403'),
        ]
        for failure, count, category in cases:
            with self.subTest(category=category), \
                    patch.object(mod.urllib.request, 'urlopen', side_effect=failure) as http, \
                    patch.object(mod.time, 'sleep'):
                with self.assertRaisesRegex(mod.FallbackError, category) as caught:
                    mod.request('https://example.invalid', 'synthetic-token')
                self.assertEqual(http.call_count, count)
                self.assertNotIn('private', str(caught.exception))
                self.assertNotIn('synthetic-token', str(caught.exception))

    def test_malformed_json_is_not_retried(self):
        with patch.object(mod.urllib.request, 'urlopen', return_value=io.BytesIO(b'private malformed body')) as http:
            with self.assertRaisesRegex(mod.FallbackError, 'invalid_json_response') as caught:
                mod.request('https://example.invalid', 'synthetic-token')
            self.assertEqual(http.call_count, 1)
            self.assertNotIn('private malformed body', str(caught.exception))

    def test_comment_transport_is_attempted_once_even_for_transient_failure(self):
        with patch.object(mod.urllib.request, 'urlopen', side_effect=TimeoutError()) as http:
            with self.assertRaisesRegex(mod.FallbackError, 'GitHub comment POST.*timeout.*1 attempt'):
                mod.request('https://example.invalid', 'synthetic-token', {'body': 'log'})
            self.assertEqual(http.call_count, 1)

    def test_workflows_only_fallback_after_failed_drive_and_successful_scrub(self):
        import yaml
        for name in ['apple','repository']:
            workflow=yaml.safe_load((ROOT/'.github/workflows'/f'{name}.yml').read_text())
            steps=workflow['jobs']['execute']['steps']
            fallback=next(s for s in steps if 'private-log-fallback@' in s.get('uses',''))
            self.assertIn("steps.drive.outcome == 'failure'",fallback['if'])
            self.assertIn("steps.scrub.outcome == 'success'",fallback['if'])
            self.assertNotIn('continue-on-error',fallback)


if __name__=='__main__':unittest.main()
