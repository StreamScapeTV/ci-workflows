"""Retirement guards for Android's product-owned generic screenshot route."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class AndroidScreenshotRetirementTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((ROOT / '.github/workflows/android.yml').read_text())
        self.dispatch = yaml.safe_load((ROOT / '.github/workflows/central-ci-dispatch.yml').read_text())

    def test_legacy_capture_jobs_are_retired(self):
        self.assertEqual(set(self.workflow['jobs']), {'physical_performance', 'ci'})
        self.assertNotIn('screenshot-review', str(self.workflow['jobs']))

    def test_retained_device_and_publication_authority(self):
        physical = self.workflow['jobs']['physical_performance']
        self.assertEqual(physical['runs-on'], 'macos-latest-xl')
        self.assertEqual(physical['if'], "${{ inputs.test_profile == 'physical-performance' }}")
        self.assertFalse(physical['concurrency']['cancel-in-progress'])
        self.assertNotIn('strategy', physical)
        self.assertEqual(self.workflow['jobs']['ci']['if'], "${{ inputs.test_profile == 'play' }}")
        self.assertEqual(self.dispatch['jobs']['android_release']['with']['test_profile'], 'play')

    def test_dispatch_rejects_retired_specialist_profile(self):
        steps = self.dispatch['jobs']['request']['steps']
        specialist = next(s['run'] for s in steps if s.get('name') == 'Validate retained specialist validation request')
        self.assertNotIn('("validation.android","screenshot-review")', specialist)
        self.assertIn('("validation.android","physical-performance")', specialist)
        self.assertIn('unsupported specialist validation profile', specialist)
        self.assertNotIn('validation.android/screenshot-review', str(steps))

    def test_shared_drive_and_generic_executor_remain(self):
        self.assertTrue((ROOT / 'actions/google-drive/action.yml').is_file())
        self.assertTrue((ROOT / '.github/workflows/repository.yml').is_file())
        script = next(s['run'] for s in self.dispatch['jobs']['request']['steps']
                      if s.get('name') == 'Resolve bounded validation concurrency lane')
        self.assertIn('"validation.repository": frozenset(("lint", "build", "test", "full", "ui-test"))', script)
        self.assertNotIn('"validation.android": frozenset', script)
