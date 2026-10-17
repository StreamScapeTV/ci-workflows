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
reply=json.loads(os.environ['REPLY']) if os.environ.get('REPLY') else {'ok':True,'run':{'status':payload['p_patch']['status']}}
Path(args[args.index('--output')+1]).write_text(json.dumps(reply))
print('200',end='')
''')
            curl.chmod(0o755)
            result=subprocess.run(['bash','-c',action['runs']['steps'][0]['run']],capture_output=True,text=True,env={
                **os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],'RUNNER_TEMP':str(root),
                'AGENT_STATE_SUPABASE_URL':'https://example.invalid','AGENT_STATE_SUPABASE_SECRET_KEY':'synthetic',
                'PHASE':phase,'CI_RUN_ID':'00000000-0000-4000-8000-000000000001','TERMINAL_STATUS':status,
                'ERROR_SUMMARY':'','DIAGNOSTIC_KEY':'','DIAGNOSTIC_STATUS':'','OBSERVED_SOURCE_SHA':'',
                'CAPTURE':str(capture),'REPLY':json.dumps(reply) if reply else '',
            })
            payload=json.loads(capture.read_text()) if capture.exists() else None
            return result,payload

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
