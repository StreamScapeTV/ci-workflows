import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = yaml.safe_load((ROOT / '.github/workflows/repository.yml').read_text())
PLAN = yaml.safe_load((ROOT / '.github/workflows/repository-plan.yml').read_text())
DISPATCH = yaml.safe_load((ROOT / '.github/workflows/central-ci-dispatch.yml').read_text())
STEPS = {x['name']:x for x in WF['jobs']['execute']['steps'] if x.get('name')}

class DockerHubPullTests(unittest.TestCase):
    def test_bound_linux_validation_only_and_no_release_secrets(self):
        contract=json.loads((ROOT/'contracts/repository-ci-v1.json').read_text())
        self.assertIn('dockerhub_public_pull', contract['capabilityTypes'])
        run=STEPS['Resolve trusted private infrastructure capabilities']['run']
        self.assertIn('enabled_capabilities.discard("dockerhub_public_pull")',run)
        self.assertIn('operation not in {"build", "test", "full", "ui-test"}',run)
        self.assertIn('host_os != "linux"',run)
        self.assertIn('capability_context not in {"repository_lifecycle", "repository_parent"}',run)
        step=STEPS['Authenticate bounded Docker Hub image pulls']
        self.assertEqual(step['if'],"${{ steps.capabilities.outputs.dockerhub_public_pull == 'true' }}")
        self.assertEqual(step['env']['DOCKERHUB_READ_TOKEN'],'${{ secrets.DOCKERHUB_READ_TOKEN }}')
        self.assertIn('docker --config "${auth_root}" login',step['run'])
        self.assertIn('--password-stdin docker.io',step['run'])
        self.assertIn('DOCKER_CONFIG=',step['run'])
        for key in ('DOCKERHUB_USERNAME','DOCKERHUB_READ_TOKEN'):
            self.assertIn(key,WF['on']['workflow_call']['secrets'])
            self.assertIn(key,PLAN['on']['workflow_call']['secrets'])
            self.assertIn(key,DISPATCH['jobs']['repository']['secrets'])
            self.assertNotIn(key,DISPATCH['jobs']['repository_release']['secrets'])
        self.assertIn("matrix.host_os == 'linux'",PLAN['jobs']['execute']['secrets']['DOCKERHUB_READ_TOKEN'])
        self.assertIn("inputs.operation != 'release'",PLAN['jobs']['execute']['secrets']['DOCKERHUB_READ_TOKEN'])
        self.assertIn('CI_SECRET_DOCKERHUB_READ_TOKEN',STEPS['Scrub configured CI secrets from private text evidence']['env'])
        self.assertIn('central-dockerhub-pull',STEPS['Cleanup ephemeral registry and repository evidence']['run'])

    def test_ephemeral_login_and_invalid_credential_fails_closed(self):
        script=STEPS['Authenticate bounded Docker Hub image pulls']['run']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            fakebin=root/'bin';fakebin.mkdir()
            docker=fakebin/'docker';docker.write_text('''#!/usr/bin/env python3
import os,sys,json,base64,pathlib
args=sys.argv[1:]
if args[0]!='--config' or args[2:5]!=['login','--username','synthetic-readonly'] or args[-2:]!=['--password-stdin','docker.io']:
    raise SystemExit(5)
config=pathlib.Path(args[1]); token=sys.stdin.read()
if token!='synthetic-example-token-1234': raise SystemExit(6)
value=base64.b64encode(('synthetic-readonly:'+token).encode()).decode()
(config/'config.json').write_text(json.dumps({'auths':{'https://index.docker.io/v1/':{'auth':value}}}))
''');docker.chmod(0o755)
            gh=root/'github_env';gh.touch()
            env={**os.environ,'PATH':str(fakebin)+os.pathsep+os.environ['PATH'],'RUNNER_TEMP':tmp,
                'GITHUB_ENV':str(gh),'DOCKERHUB_USERNAME':'synthetic-readonly','DOCKERHUB_READ_TOKEN':'synthetic-example-token-1234',
                'HOST_OS':'linux','CI_OPERATION':'full'}
            r=subprocess.run(['bash','-c',script],cwd=ROOT,env=env,text=True,capture_output=True,timeout=12)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            auth=root/'central-dockerhub-pull';self.assertTrue((auth/'config.json').is_file())
            self.assertEqual(stat.S_IMODE((auth/'config.json').stat().st_mode),0o600)
            self.assertIn('DOCKER_CONFIG='+str(auth),gh.read_text())
            # GitHub commands register the raw/derived masks before product output.
            self.assertEqual(len([line for line in r.stdout.splitlines() if line.startswith('::add-mask::')]),2)
            self.assertNotIn('synthetic-example-token',r.stderr)
            bad=dict(env,DOCKERHUB_READ_TOKEN='')
            r=subprocess.run(['bash','-c',script],cwd=ROOT,env=bad,text=True,capture_output=True,timeout=12)
            self.assertNotEqual(r.returncode,0)
            self.assertNotIn('synthetic-example-token',r.stderr)
            self.assertIn('missing its fixed credentials',r.stderr)
            cleanup=STEPS['Cleanup ephemeral registry and repository evidence']['run']
            result=subprocess.run(['bash','-c',cleanup],cwd=ROOT,env=env,text=True,capture_output=True,timeout=12)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertFalse(auth.exists())

    def test_secret_scrub_covers_raw_and_docker_base64(self):
        from tests.test_repository_workflow import RepositoryWorkflowTests
        harness=RepositoryWorkflowTests(methodName='test_private_agent_state_capability_grant_is_exactly_bound_and_fail_closed')
        harness.setUpClass()
        with harness.evidence_fixture() as (root,env):
            user='synthetic-readonly'; token='synthetic-example-token-1234'
            b64=base64.b64encode((user+':'+token).encode()).decode()
            env.update(CI_SECRET_DOCKERHUB_USERNAME=user,CI_SECRET_DOCKERHUB_READ_TOKEN=token)
            (root/'central.log').write_text('raw '+token+' encoded '+b64+'\n')
            result=harness.execute_evidence_fixture(env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn(token,(root/'central.log').read_text())
            self.assertNotIn(b64,(root/'central.log').read_text())
            self.assertIn('[REDACTED]',(root/'central.log').read_text())

if __name__=='__main__': unittest.main()
