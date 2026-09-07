"""Execute the actual release-ref shell gates against disposable Git repositories."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/public-native-image-chart.yml').read_text())
STEPS = {s.get('name'): s for s in WORKFLOW['jobs']['publish']['steps']}


class MainRefReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.origin = self.root / 'origin.git'
        self.run_git(self.root, 'init', '--bare', str(self.origin))
        self.run_git(self.root, 'init', '-b', 'main', str(self.source))
        self.run_git(self.source, 'config', 'user.name', 'Test')
        self.run_git(self.source, 'config', 'user.email', 'test@example.invalid')
        (self.source / 'README').write_text('one\n')
        self.run_git(self.source, 'add', 'README')
        self.run_git(self.source, 'commit', '-m', 'one')
        self.first = self.run_git(self.source, 'rev-parse', 'HEAD')
        self.run_git(self.source, 'tag', 'main')  # Deliberately same name as branch.
        self.run_git(self.source, 'tag', '-a', 'release-0.1.0', '-m', 'release')
        (self.source / 'README').write_text('two\n')
        self.run_git(self.source, 'commit', '-am', 'two')
        self.head = self.run_git(self.source, 'rev-parse', 'HEAD')
        self.run_git(self.source, 'remote', 'add', 'origin', str(self.origin))
        self.run_git(self.source, 'push', 'origin', 'refs/heads/main', '--tags')
        self.output = self.root / 'output'

    def run_git(self, cwd, *args):
        return subprocess.run(['git', *args], cwd=cwd, check=True,
                              capture_output=True, text=True).stdout.strip()

    def gate(self, name, **env):
        self.output.write_text('')
        return subprocess.run(['bash', '-c', STEPS[name]['run']], cwd=self.root,
                              env={**os.environ, 'GITHUB_OUTPUT': str(self.output),
                                   'CI_LOG': str(self.root / 'log'), 'SOURCE_TOKEN': 'synthetic', **env},
                              capture_output=True, text=True)

    def test_ref_resolution_rejects_feature_branches_invalid_kinds_and_invalid_tags(self):
        for ref, kind in [('feature/demo', 'false'), ('develop', 'false'), ('main', ''),
                          ('main', 'yes'), ('../bad', 'true'), ('', 'true')]:
            with self.subTest(ref=ref, kind=kind):
                result = self.gate('Resolve bounded release source ref', RELEASE_REF=ref, SOURCE_IS_TAG=kind)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output.read_text(), '')
        for ref, kind, full in [('main', 'false', 'refs/heads/main'),
                                ('main', 'true', 'refs/tags/main'),
                                ('release-0.1.0', 'true', 'refs/tags/release-0.1.0')]:
            result = self.gate('Resolve bounded release source ref', RELEASE_REF=ref, SOURCE_IS_TAG=kind)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.output.read_text(), f'full_ref={full}\n')

    def test_main_is_not_shadowed_by_same_named_tag(self):
        result = self.gate('Verify exact source ref authority', SOURCE_REF='refs/heads/main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), f'source_sha={self.head}\n')
        result = self.gate('Verify exact source ref authority', SOURCE_REF='refs/tags/main')
        self.assertNotEqual(result.returncode, 0)

    def test_annotated_tag_authority_and_remote_readback(self):
        self.run_git(self.source, 'checkout', '--detach', 'refs/tags/release-0.1.0')
        for name in ['Verify exact source ref authority', 'Revalidate exact source ref before publication']:
            result = self.gate(name, SOURCE_REF='refs/tags/release-0.1.0', SOURCE_SHA=self.first)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_clean_main_revalidates_and_moved_or_deleted_main_is_rejected(self):
        kwargs = dict(SOURCE_REF='refs/heads/main', SOURCE_SHA=self.head)
        self.assertEqual(self.gate('Revalidate exact source ref before publication', **kwargs).returncode, 0)
        self.run_git(self.origin, 'update-ref', 'refs/heads/main', self.first)
        self.assertNotEqual(self.gate('Revalidate exact source ref before publication', **kwargs).returncode, 0)
        self.run_git(self.origin, 'update-ref', '-d', 'refs/heads/main')
        self.assertNotEqual(self.gate('Revalidate exact source ref before publication', **kwargs).returncode, 0)

    def test_dirty_and_wrong_checkout_rejected(self):
        kwargs = dict(SOURCE_REF='refs/heads/main', SOURCE_SHA=self.head)
        (self.source / 'untracked').write_text('dirty')
        for name in ['Verify exact source ref authority', 'Revalidate exact source ref before publication']:
            self.assertNotEqual(self.gate(name, **kwargs).returncode, 0)
        (self.source / 'untracked').unlink()
        self.run_git(self.source, 'checkout', '--detach', self.first)
        self.assertNotEqual(self.gate('Revalidate exact source ref before publication', **kwargs).returncode, 0)

    def test_dispatch_propagates_ref_kind_without_changing_tag_caller_default(self):
        self.assertIs(WORKFLOW['on']['workflow_call']['inputs']['source_is_tag']['default'], True)
        dispatch = yaml.safe_load((ROOT / '.github/workflows/central-ci-dispatch.yml').read_text())
        self.assertEqual(dispatch['jobs']['public_native_image_chart']['with']['source_is_tag'],
                         "${{ needs.request.outputs.is_tag == 'true' }}")
        self.assertEqual(STEPS['Check out exact product source ref']['with']['ref'],
                         '${{ steps.requested_ref.outputs.full_ref }}')
        self.assertIn('--digestfile', STEPS['Publish immutable image and chart']['run'])
        self.assertIn('digest != expected', STEPS['Authenticated private registry read-back']['run'])
