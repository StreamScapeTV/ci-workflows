"""Execute the shipped isolation and admission diagnostics against disposable fixtures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / 'actions/private-git/action.yml').read_text())
STEP = next(s for s in ACTION['runs']['steps'] if s['name'] == 'Isolate owned macOS Tailscale build source')
SCRIPT = STEP['run'].split("python3 - <<'PYCODE'\n", 1)[1].rsplit('\nPYCODE', 1)[0]

class TailscaleSourceIsolationTests(unittest.TestCase):
    def fixture(self, root):
        workspace=root/'product';workspace.mkdir()
        runtime=root/'runtime';runtime.mkdir()
        clients=root/'installed';clients.mkdir()
        for name in ('tailscale','tailscaled'):
            p=clients/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o755)
        def git(path,*args):
            return subprocess.run(['git','-C',str(path),*args],check=True,capture_output=True,text=True)
        git(workspace,'init','-q')
        source=workspace/'tailscale';source.mkdir();git(source,'init','-q')
        git(source,'remote','add','origin','https://github.com/tailscale/tailscale.git')
        (source/'source.go').write_text('synthetic source\n')
        git(source,'add','source.go');git(source,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')
        return workspace,runtime,clients,source,git
    def run_script(self, workspace,runtime,clients):
        script=SCRIPT.replace("Path('/usr/local/bin')",f'Path({str(clients)!r})')
        return subprocess.run(['python3','-c',script],env={**os.environ,'GITHUB_WORKSPACE':str(workspace),'RUNNER_TEMP':str(runtime)},text=True,capture_output=True)
    def test_owned_build_source_moves_without_removing_installed_clients(self):
        with tempfile.TemporaryDirectory() as d:
            w,r,c,s,g=self.fixture(Path(d));before=[p.read_bytes() for p in c.iterdir()]
            result=self.run_script(w,r,c);self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(s.exists());self.assertEqual(len(list(r.glob('central-tailscale-build-*/source/source.go'))),1)
            self.assertEqual(before,[p.read_bytes() for p in c.iterdir()])
            self.assertEqual(g(w,'status','--porcelain').stdout,'')
            self.assertEqual(self.run_script(w,r,c).returncode,0)
    def test_foreign_modified_tracked_symlink_or_missing_clients_fail_closed(self):
        for kind in ('foreign','modified','untracked','tracked','symlink','client','temp'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                w,r,c,s,g=self.fixture(Path(d))
                if kind=='foreign':g(s,'remote','set-url','origin','https://example.invalid/unrelated.git')
                elif kind=='modified':(s/'source.go').write_text('modified private fixture')
                elif kind=='untracked':(s/'unrelated').write_text('unrelated retained fixture')
                elif kind=='tracked':g(w,'add','tailscale')
                elif kind=='symlink':s.rename(r/'foreign');s.symlink_to(r/'foreign',target_is_directory=True)
                elif kind=='client':(c/'tailscaled').unlink()
                elif kind=='temp':r=w/'runtime';r.mkdir()
                result=self.run_script(w,r,c);self.assertNotEqual(result.returncode,0)
                self.assertTrue(s.exists());self.assertNotIn('Traceback',result.stderr)
                self.assertNotIn('example.invalid',result.stderr)
                self.assertEqual(list(r.glob('central-tailscale-build-*')),[])
    def test_nonempty_fixed_admission_failure_keeps_original_exit(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/apple.yml').read_text())
        steps=next(job['steps'] for job in workflow['jobs'].values() if any(s.get('name')=='Run fixed Apple lane' for s in job.get('steps',[])))
        code=next(s['run'] for s in steps if s.get('name')=='Run fixed Apple lane')
        prefix=code.split('run_logged() {',1)[0]
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'lane.log';log.touch()
            result=subprocess.run(['bash','-c',prefix+'\nexit 23\n'],env={**os.environ,'CI_LOG':str(log)},capture_output=True,text=True)
            self.assertEqual(result.returncode,23)
            self.assertGreater(log.stat().st_size,0)
            self.assertIn('exit_code=23',log.read_text())
            self.assertNotIn('git status',log.read_text())
