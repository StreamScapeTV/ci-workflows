from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cache', ROOT / 'scripts/ci/apple_dependency_cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class AppleDependencyCacheTests(unittest.TestCase):
    def test_current_remote_project_needs_no_retired_bootstrap_and_ignores_app_commits(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(['git', 'init', '-q', d], check=True)
            project = root / 'App.xcodeproj/project.pbxproj'
            project.parent.mkdir()
            declarations = '/* Begin XCRemoteSwiftPackageReference section */\nurl = package; version = 2.1.7;\n/* End XCRemoteSwiftPackageReference section */'
            project.write_text(declarations)
            code = root / 'App.swift'
            code.write_text('first')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            before = cache.fingerprint(root)
            self.assertIsNotNone(before)
            code.write_text('unrelated change')
            project.write_text('unrelated setting\n' + declarations)
            self.assertEqual(before, cache.fingerprint(root))
            project.write_text(declarations.replace('2.1.7', '2.1.8'))
            self.assertNotEqual(before, cache.fingerprint(root))

    def test_lockfile_changes_key_but_generated_and_untracked_files_do_not(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(['git', 'init', '-q', d], check=True)
            self.assertIsNone(cache.fingerprint(root))
            lock = root / 'Package.resolved'
            lock.write_text('{"pins":["one"]}')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            before = cache.fingerprint(root)
            (root / 'generated').mkdir()
            (root / 'generated/Package.resolved').write_text('ignore')
            self.assertEqual(before, cache.fingerprint(root))
            lock.write_text('{"pins":["two"]}')
            self.assertNotEqual(before, cache.fingerprint(root))
            variant = root / 'Package@swift-6.0.swift'
            variant.write_text('version specific dependency')
            subprocess.run(['git', 'add', str(variant)], cwd=root, check=True)
            variant_before = cache.fingerprint(root)
            variant.write_text('updated dependency')
            self.assertNotEqual(variant_before, cache.fingerprint(root))

    def test_native_and_generic_paths_share_artifact_only_cache_and_trusted_writer(self):
        for name, command_step in [('apple.yml', 'commands'), ('repository.yml', 'execute_contract')]:
            with self.subTest(workflow=name):
                workflow = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
                steps = next(j['steps'] for j in workflow['jobs'].values() if any(s.get('id') == 'apple_default_cache' for s in j.get('steps', [])))
                identity = next(s for s in steps if s.get('id') == 'apple_default_cache')
                restore = next(s for s in steps if s.get('id') == 'apple_default_cache_restore')
                save = next(s for s in steps if s.get('name') == 'Save native Apple binary artifacts')
                self.assertEqual(identity['run'], 'python3 ' + ('../' if name == 'repository.yml' else '') + 'central-ci/scripts/ci/apple_dependency_cache.py')
                if name == 'repository.yml':
                    self.assertEqual(identity['working-directory'], 'source')
                self.assertEqual(restore['with'], save['with'])
                self.assertIn(f'steps.{command_step}.outcome', save['if'])
                self.assertIn('github.event.repository.default_branch', save['if'])
                self.assertIn("github.event_name != 'pull_request'", save['if'])
                self.assertNotIn('bootstrap-streamscape-media', identity['run'])
                self.assertNotIn('Vendor/', restore['with']['path'])

    def test_generic_cache_helper_runs_from_the_actual_nested_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            source = workspace / 'source'
            source.mkdir()
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            (source / 'Package.swift').write_text('// swift-tools-version: 6.0')
            subprocess.run(['git', 'add', '.'], cwd=source, check=True)
            helper = workspace / 'central-ci/scripts/ci/apple_dependency_cache.py'
            helper.parent.mkdir(parents=True)
            helper.write_bytes((ROOT / 'scripts/ci/apple_dependency_cache.py').read_bytes())
            output = workspace / 'outputs'
            workflow = yaml.safe_load((ROOT / '.github/workflows/repository.yml').read_text())
            step = next(s for j in workflow['jobs'].values() for s in j.get('steps', []) if s.get('id') == 'apple_default_cache')
            result = subprocess.run(['bash', '-c', step['run']], cwd=workspace / step['working-directory'],
                                    env={**os.environ, 'SOURCE_REPOSITORY': 'fixture/app', 'GITHUB_OUTPUT': str(output)},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('enabled=true', output.read_text())
            self.assertIn('key=apple-binary-artifacts-v1-', output.read_text())

    def test_key_has_no_source_sha_and_paths_exclude_authentication_and_build_products(self):
        text = (ROOT / 'scripts/ci/apple_dependency_cache.py').read_text()
        self.assertNotIn('GITHUB_SHA', text)
        self.assertIn("'Library/Caches/org.swift.swiftpm/artifacts'", text)
        self.assertNotIn("'repositories'", text)
        self.assertNotIn("'.netrc'", text)


if __name__ == '__main__':
    unittest.main()
