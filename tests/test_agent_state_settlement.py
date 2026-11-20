import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class AgentStateSettlementTests(unittest.TestCase):
    def invoke(self, phase, status, reply=None):
        action=yaml.safe_load((ROOT/'actions/agent-state/action.yml').read_text())
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').exists() else None) as temp:
            root=Path(temp);capture=root/'payload.json'
            curl=root/'curl'
            curl.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
payload=json.loads(args[args.index('--data')+1]);Path(os.environ['CAPTURE']).write_text(json.dumps(payload))
Path(os.environ['CAPTURE']+'.args').write_text(json.dumps(args))
reply=json.loads(os.environ['REPLY']) if os.environ.get('REPLY') else {'ok':True,'run':{'status':payload['p_patch']['status']}}
Path(args[args.index('--output')+1]).write_text(json.dumps(reply))
print('200',end='')
''')
            curl.chmod(0o755)
            result=subprocess.run(['bash','-c',action['runs']['steps'][0]['run']],capture_output=True,text=True,env={
                **os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],'RUNNER_TEMP':str(root),
                'AGENT_STATE_URL':'https://example.invalid','AGENT_STATE_API_KEY':'synthetic','CF_ACCESS_CLIENT_ID':'cf-id','CF_ACCESS_CLIENT_SECRET':'cf-secret',
                'PHASE':phase,'CI_RUN_ID':'00000000-0000-4000-8000-000000000001','TERMINAL_STATUS':status,
                'ERROR_SUMMARY':'','DIAGNOSTIC_KEY':'','DIAGNOSTIC_STATUS':'','OBSERVED_SOURCE_SHA':'',
                'CAPTURE':str(capture),'REPLY':json.dumps(reply) if reply else '',
            })
            payload=json.loads(capture.read_text()) if capture.exists() else None
            return result,payload

    def test_calls_send_apikey_and_cloudflare_access_headers_to_the_mirror_url(self):
        action=yaml.safe_load((ROOT/'actions/agent-state/action.yml').read_text())
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').exists() else None) as temp:
            root=Path(temp);capture=root/'payload.json'
            curl=root/'curl'
            curl.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
Path(os.environ['CAPTURE']).write_text(json.dumps(args))
Path(args[args.index('--output')+1]).write_text(json.dumps({'ok':True,'run':{'status':'failed'}}))
print('200',end='')
''')
            curl.chmod(0o755)
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],'RUNNER_TEMP':str(root),
                 'AGENT_STATE_URL':'https://mirror.example.invalid/','AGENT_STATE_API_KEY':'api-key-fixture',
                 'CF_ACCESS_CLIENT_ID':'cf-id-fixture','CF_ACCESS_CLIENT_SECRET':'cf-secret-fixture',
                 'PHASE':'finish','CI_RUN_ID':'00000000-0000-4000-8000-000000000001','TERMINAL_STATUS':'failed',
                 'ERROR_SUMMARY':'','DIAGNOSTIC_KEY':'logs/project/00000000-0000-4000-8000-000000000001/1-1.txt','DIAGNOSTIC_STATUS':'available',
                 'OBSERVED_SOURCE_SHA':'','CAPTURE':str(capture)}
            result=subprocess.run(['bash','-c',action['runs']['steps'][0]['run']],capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            args=json.loads(capture.read_text())
            self.assertIn('apikey: api-key-fixture',args)
            self.assertIn('CF-Access-Client-Id: cf-id-fixture',args)
            self.assertIn('CF-Access-Client-Secret: cf-secret-fixture',args)
            self.assertEqual(args[-1],'https://mirror.example.invalid/rest/v1/rpc/transition_ci_run')
            self.assertIn('logs/project/00000000-0000-4000-8000-000000000001/1-1.txt',args[args.index('--data')+1])
            missing={k:v for k,v in env.items() if k!='CF_ACCESS_CLIENT_SECRET'}
            refused=subprocess.run(['bash','-c',action['runs']['steps'][0]['run']],capture_output=True,text=True,env=missing)
            self.assertNotEqual(refused.returncode,0)
            self.assertNotIn('cf-secret-fixture',refused.stdout+refused.stderr)

    def test_finish_maps_provider_outcome_to_five_state_status_and_fixed_reason(self):
        for status in ('succeeded','failed','cancelled'):
            with self.subTest(status=status):
                result,payload=self.invoke('finish',status)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(payload['p_patch']['status'],'succeeded' if status=='succeeded' else 'failed')
                self.assertEqual(payload['p_patch']['terminal_reason'],'provider_'+status)

    def test_fallback_distinguishes_failure_and_cancellation_with_legacy_default(self):
        for status,reason in [('failed','provider_failed'),('cancelled','provider_cancelled'),('','provider_cancelled')]:
            result,payload=self.invoke('cancel-if-active',status)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(payload['p_patch']['status'],'failed')
            self.assertEqual(payload['p_patch']['terminal_reason'],reason)
            self.assertIn('failed' if status=='failed' else 'cancelled',payload['p_patch']['error_summary'])

    def test_fallback_preserves_already_terminal_results(self):
        for status in ('succeeded','failed'):
            result,_payload=self.invoke('cancel-if-active','cancelled',dict(ok=False,code='already_terminal',run=dict(status=status,terminal_reason='superseded')))
            self.assertEqual(result.returncode,0,result.stderr)

    def test_invalid_outcomes_never_make_a_service_call(self):
        for phase,status in [('finish','unknown'),('cancel-if-active','succeeded')]:
            result,payload=self.invoke(phase,status)
            self.assertNotEqual(result.returncode,0)
            self.assertIsNone(payload)


class OutOfBandSettlementTests(unittest.TestCase):
    UUID='1cd460b6-1111-4222-8333-444455556666'

    def workflow(self):
        return yaml.safe_load((ROOT/'.github/workflows/central-ci-settle.yml').read_text())

    def resolve(self,title,conclusion):
        step=next(s for s in self.workflow()['jobs']['settle']['steps'] if s.get('id')=='resolve')
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'out'
            out.write_text('')
            result=subprocess.run(['bash','-c',step['run']],capture_output=True,text=True,env={
                **os.environ,'RUN_TITLE':title,'RUN_CONCLUSION':conclusion,'GITHUB_OUTPUT':str(out)})
            return result,dict(l.split('=',1) for l in out.read_text().splitlines())

    def test_dispatch_title_carries_the_run_id_and_settlement_follows_every_completed_run(self):
        dispatch=yaml.safe_load((ROOT/'.github/workflows/central-ci-dispatch.yml').read_text())
        self.assertEqual(dispatch['run-name'],'Central CI dispatch ${{ inputs.ci_run_id }}')
        workflow=self.workflow()
        trigger=workflow[True] if True in workflow else workflow['on']
        self.assertEqual(trigger,{'workflow_run':{'workflows':['Central CI dispatch'],'types':['completed']}})
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        job=workflow['jobs']['settle']
        self.assertIn("conclusion != 'success'",job['if'])
        self.assertIn('head_repository.full_name == github.repository',job['if'])
        self.assertIn("event == 'workflow_dispatch'",job['if'])
        settle=job['steps'][-1]
        self.assertEqual(settle['uses'],'./actions/agent-state')
        self.assertEqual(settle['with']['phase'],'cancel-if-active')
        self.assertEqual(settle['with']['ci_run_id'],'${{ steps.resolve.outputs.ci_run_id }}')
        self.assertEqual(job['steps'][0]['with']['ref'],'${{ github.event.repository.default_branch }}')

    def test_resolver_extracts_only_a_well_formed_run_id_and_maps_status(self):
        for conclusion,status in [('failure','failed'),('timed_out','failed'),('startup_failure','failed'),('cancelled','cancelled')]:
            result,out=self.resolve('Central CI dispatch '+self.UUID,conclusion)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(out,{'ci_run_id':self.UUID,'status':status})
        for title in ['Central CI dispatch','Central CI dispatch not-a-uuid','Other '+self.UUID,'Central CI dispatch '+self.UUID+' extra','Central CI dispatch '+self.UUID+'\nx']:
            result,out=self.resolve(title,'failure')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(out.get('ci_run_id'),'')
            self.assertNotIn('status',out)
