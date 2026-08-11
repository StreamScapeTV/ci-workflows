from __future__ import annotations
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import yaml
from ci_workflows.gitops_contract import build_plan, load_gitops_contract, request_from_environment, safe_relative
from ci_workflows.gitops_execution import GitOpsTools, _safe_tar_member, assert_zero_gitops_residue, cleanup_gitops_state, execute_gitops_plan
from ci_workflows.gitops_runtime import _download
from ci_workflows.gitops_types import GitOpsProfile, GitOpsRequest, GitOpsToolPin, GitOpsValidationError
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'gitops-validation'

class GitOpsValidationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / 'source'
        self.state = self.base / 'state' / 'gitops'
        destination = self.source / 'tests' / 'fixtures' / 'gitops-validation'
        destination.parent.mkdir(parents=True)
        shutil.copytree(FIXTURES, destination)
        self._commit('initial')
        self.contract = load_gitops_contract(ROOT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _commit(self, message: str) -> str:
        if not (self.source / '.git').exists():
            self.source.mkdir(parents=True, exist_ok=True)
            subprocess.run(['git', 'init', '-q'], cwd=self.source, check=True)
            subprocess.run(['git', 'config', 'user.name', 'GitOps Test'], cwd=self.source, check=True)
            subprocess.run(['git', 'config', 'user.email', 'gitops@example.invalid'], cwd=self.source, check=True)
        subprocess.run(['git', 'add', '-A'], cwd=self.source, check=True)
        subprocess.run(['git', 'commit', '-q', '--allow-empty', '-m', message], cwd=self.source, check=True)
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.source, text=True).strip()

    def _request(self, profile: GitOpsProfile=GitOpsProfile.FULL, *, sha: str | None=None, base: str | None=None, policy: str | None='synthetic-policy') -> GitOpsRequest:
        return GitOpsRequest(repository='StreamScapeTV/ci-workflows', admitted_sha=sha or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.source, text=True).strip(), consumer_contract='synthetic', validation_profile=profile, source_trust='trusted-exact', change_base_sha=base, policy_script_profile=policy)

    def _fake_tools(self, *, helm_name: str='synthetic-helm', kustomize_environment: str='test') -> GitOpsTools:
        binaries = self.base / 'fake-tools'
        binaries.mkdir(exist_ok=True)
        helm = binaries / 'helm'
        helm.write_text(f"""#!/bin/sh
set -eu
if [ "${{1:-}}" = lint ]; then exit 0; fi
if [ "${{1:-}}" = template ]; then
cat <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: {helm_name}
data:
  image: "example.invalid/synthetic:sha-0000000000000000000000000000000000000000"
  message: "source-only"
EOF
exit 0
fi
exit 2
""", encoding='utf-8')
        helm.chmod(493)
        kustomize = binaries / 'kustomize'
        kustomize.write_text(f"#!/bin/sh\nset -eu\ncat <<'EOF'\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: synthetic-kustomize\ndata:\n  environment: {kustomize_environment}\nEOF\n", encoding='utf-8')
        kustomize.chmod(493)
        return GitOpsTools(yaml=yaml, binaries={'helm': helm, 'kustomize': kustomize}, versions={'helm': '3.18.6', 'kustomize': '5.8.1', 'pyyaml': '6.0.3'})

    def _execute(self, request: GitOpsRequest | None=None, **tool_options: str):
        chosen = request or self._request()
        plan = build_plan(self.contract, chosen, self.source)
        return execute_gitops_plan(plan, self.source, self.state, tools=self._fake_tools(**tool_options))

    def test_contract_is_complete_canonical_and_exactly_pinned(self) -> None:
        path = ROOT / 'contracts' / 'gitops-validation.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(path.read_text(encoding='utf-8'), json.dumps(payload, indent=2, sort_keys=True) + '\n')
        self.assertEqual('validation.gitops', payload['api']['name'])
        self.assertEqual('portable', payload['runner_profile'])
        self.assertEqual({'source-audit', 'yaml', 'helm-render', 'kustomize-render', 'changed-tree', 'full'}, set(payload['profiles']))
        self.assertEqual('3.18.6', payload['tools']['helm']['version'])
        self.assertEqual('5.8.1', payload['tools']['kustomize']['version'])
        self.assertEqual(64, len(payload['tools']['helm']['sha256']))
        self.assertTrue(all(payload['cleanup'].values()))

    def test_request_rejects_caller_selected_authority_and_fork_privilege(self) -> None:
        event = self.base / 'event.json'
        event.write_text(json.dumps({'pull_request': {'head': {'repo': {'full_name': 'outside/fork'}}}}))
        environment = {'GITHUB_REPOSITORY': 'StreamScapeTV/ci-workflows', 'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': str(event), 'INPUT_ADMITTED_SHA': 'a' * 40, 'INPUT_CONSUMER_CONTRACT': 'synthetic', 'INPUT_VALIDATION_PROFILE': 'full', 'INPUT_POLICY_SCRIPT_PROFILE': 'synthetic-policy'}
        request = request_from_environment(environment, self.contract)
        self.assertEqual('untrusted-fork', request.source_trust)
        with self.assertRaisesRegex(GitOpsValidationError, 'source_trust_rejected'):
            build_plan(self.contract, request, None)
        for field in ('INPUT_RUNNER', 'INPUT_COMMAND', 'INPUT_REGISTRY', 'INPUT_KUBECONFIG'):
            with self.subTest(field=field):
                contaminated = {**environment, field: 'attacker-controlled'}
                with self.assertRaisesRegex(GitOpsValidationError, 'invalid_input'):
                    request_from_environment(contaminated, self.contract)

    def test_safe_paths_reject_traversal_absolute_and_symlink(self) -> None:
        for value in ('../escape', '/absolute', 'a/../../b', 'a\\b', ''):
            with self.subTest(value=value):
                with self.assertRaises(GitOpsValidationError):
                    safe_relative(value)
        link = self.source / 'tests' / 'fixtures' / 'gitops-validation' / 'synthetic' / 'yaml' / 'escape.yaml'
        link.symlink_to(self.base / 'outside')
        self._commit('symlink')
        with self.assertRaisesRegex(GitOpsValidationError, 'path_symlink_rejected'):
            self._execute()

    def test_full_execution_validates_yaml_helm_kustomize_policy_and_cleanup(self) -> None:
        result = self._execute()
        self.assertEqual(('synthetic-yaml', 'synthetic-helm', 'synthetic-kustomize'), result.selected_targets)
        self.assertEqual(4, result.rendered_objects)
        self.assertGreaterEqual(result.validated_files, 8)
        self.assertEqual('success', result.policy_result)
        self.assertRegex(result.render_digest, '^[0-9a-f]{64}$')
        self.assertTrue(result.clean_tree)
        cleanup_gitops_state(self.state)
        assert_zero_gitops_residue(self.state)

    def test_duplicate_yaml_key_style_schema_and_sops_plaintext_fail_closed(self) -> None:
        yaml_root = self.source / 'tests/fixtures/gitops-validation/synthetic/yaml'
        mutations = {'duplicate': 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: a\n  name: b\n', 'tab': 'apiVersion:\tv1\nkind: ConfigMap\nmetadata:\n  name: tabbed\n', 'schema': 'apiVersion: v1\nkind: ConfigMap\nmetadata: {}\n'}
        expected = {'duplicate': 'yaml_invalid', 'tab': 'yaml_style_failed', 'schema': 'schema_invalid'}
        for name, content in mutations.items():
            with self.subTest(name=name):
                path = yaml_root / f'{name}.yaml'
                path.write_text(content, encoding='utf-8')
                self._commit(name)
                with self.assertRaisesRegex(GitOpsValidationError, expected[name]):
                    self._execute(self._request(GitOpsProfile.YAML))
                path.unlink()
                self._commit(f'remove-{name}')
        secret = yaml_root / 'secret.enc.yaml'
        secret.write_text(secret.read_text().replace('ENC[AES256_GCM,data:ZmFrZQ==,iv:AA==,tag:AA==,type:str]', 'plaintext'))
        self._commit('plaintext')
        with self.assertRaisesRegex(GitOpsValidationError, 'sops_plaintext_rejected'):
            self._execute(self._request(GitOpsProfile.YAML))

    def test_helm_lock_required_values_and_render_drift_are_rejected(self) -> None:
        helm_root = self.source / 'tests/fixtures/gitops-validation/synthetic/helm'
        lock = helm_root / 'Chart.lock'
        lock.unlink()
        self._commit('remove-lock')
        with self.assertRaisesRegex(GitOpsValidationError, 'helm_lock_invalid'):
            self._execute(self._request(GitOpsProfile.HELM_RENDER))
        shutil.copy2(FIXTURES / 'synthetic/helm/Chart.lock', lock)
        values = helm_root / 'values.yaml'
        values.write_text(values.read_text().replace('message: source-only\n', ''))
        self._commit('missing-value')
        with self.assertRaisesRegex(GitOpsValidationError, 'required_value_missing'):
            self._execute(self._request(GitOpsProfile.HELM_RENDER))
        shutil.copy2(FIXTURES / 'synthetic/helm/values.yaml', values)
        self._commit('restore-value')
        with self.assertRaisesRegex(GitOpsValidationError, 'render_drift'):
            self._execute(self._request(GitOpsProfile.HELM_RENDER), helm_name='wrong-render')

    def test_helm_dependency_must_match_the_declared_vendored_path(self) -> None:
        helm_root = self.source / 'tests/fixtures/gitops-validation/synthetic/helm'
        chart_path = helm_root / 'Chart.yaml'
        lock_path = helm_root / 'Chart.lock'
        chart = yaml.safe_load(chart_path.read_text(encoding='utf-8'))
        chart['dependencies'][0]['repository'] = 'file:///tmp'
        chart_path.write_text(yaml.safe_dump(chart, sort_keys=False), encoding='utf-8')
        lock = yaml.safe_load(lock_path.read_text(encoding='utf-8'))
        lock['dependencies'][0]['repository'] = 'file:///tmp'
        lock['digest'] = 'sha256:' + hashlib.sha256(
            json.dumps(lock['dependencies'], sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()
        lock_path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding='utf-8')
        self._commit('external-helm-dependency')
        with self.assertRaisesRegex(GitOpsValidationError, 'helm_lock_invalid'):
            self._execute(self._request(GitOpsProfile.HELM_RENDER))

    def test_kustomize_remote_and_render_drift_are_rejected(self) -> None:
        path = self.source / 'tests/fixtures/gitops-validation/synthetic/kustomize/kustomization.yaml'
        path.write_text(path.read_text() + '  - https://example.invalid/remote.yaml\n')
        self._commit('remote')
        with self.assertRaisesRegex(GitOpsValidationError, 'kustomize_invalid'):
            self._execute(self._request(GitOpsProfile.KUSTOMIZE_RENDER))
        shutil.copy2(FIXTURES / 'synthetic/kustomize/kustomization.yaml', path)
        self._commit('restore-kustomize')
        with self.assertRaisesRegex(GitOpsValidationError, 'render_drift'):
            self._execute(self._request(GitOpsProfile.KUSTOMIZE_RENDER), kustomize_environment='wrong')

    def test_duplicate_object_ownership_is_deterministic(self) -> None:
        expected = self.source / 'tests/fixtures/gitops-validation/synthetic/expected/helm.yaml'
        expected.write_text(expected.read_text().replace('synthetic-helm', 'synthetic-yaml'))
        self._commit('duplicate-render-owner')
        with self.assertRaisesRegex(GitOpsValidationError, 'duplicate_object_ownership'):
            self._execute(helm_name='synthetic-yaml')

    def test_duplicate_object_within_one_target_is_rejected(self) -> None:
        yaml_root = self.source / 'tests/fixtures/gitops-validation/synthetic/yaml'
        (yaml_root / 'duplicate-object.yaml').write_text(
            (yaml_root / 'configmap.yaml').read_text(),
            encoding='utf-8',
        )
        self._commit('duplicate-yaml-owner')
        with self.assertRaisesRegex(GitOpsValidationError, 'duplicate_object_ownership'):
            self._execute(self._request(GitOpsProfile.YAML))

    def test_changed_tree_selects_only_affected_target(self) -> None:
        base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.source, text=True).strip()
        path = self.source / 'tests/fixtures/gitops-validation/synthetic/yaml/configmap.yaml'
        path.write_text(path.read_text().replace('source-only', 'changed'))
        head = self._commit('yaml-change')
        request = self._request(GitOpsProfile.CHANGED_TREE, sha=head, base=base)
        result = self._execute(request)
        self.assertEqual(('synthetic-yaml',), result.selected_targets)

    def test_policy_hash_output_and_source_mutation_fail_closed(self) -> None:
        policy = self.source / 'tests/fixtures/gitops-validation/synthetic/policy/check.py'
        policy.write_text(policy.read_text() + '\n# drift\n')
        self._commit('policy-drift')
        with self.assertRaisesRegex(GitOpsValidationError, 'policy_profile_rejected'):
            self._execute(self._request(GitOpsProfile.SOURCE_AUDIT))

    def test_cleanup_preserves_outside_sentinel_and_combines_failures(self) -> None:
        outside = self.base / 'outside-sentinel'
        outside.write_text('keep')
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.symlink_to(outside)
        with self.assertRaisesRegex(GitOpsValidationError, 'cleanup_failed'):
            cleanup_gitops_state(self.state)
        self.assertEqual('keep', outside.read_text())
        self.state.unlink()
        plan = build_plan(self.contract, self._request(GitOpsProfile.YAML), self.source)
        with mock.patch('ci_workflows.gitops_execution._execute', side_effect=GitOpsValidationError('yaml_invalid')), mock.patch('ci_workflows.gitops_execution.cleanup_gitops_state', side_effect=GitOpsValidationError('cleanup_failed')):
            with self.assertRaisesRegex(GitOpsValidationError, 'primary_and_cleanup_failed: primary=yaml_invalid;cleanup=cleanup_failed'):
                execute_gitops_plan(plan, self.source, self.state, tools=self._fake_tools())

    def test_safe_archive_rejects_traversal_and_accepts_exact_member(self) -> None:
        pin = GitOpsToolPin(name='helm', version='3.18.6', url='https://get.helm.sh/helm-v3.18.6-linux-amd64.tar.gz', sha256='0' * 64, archive_member='linux-amd64/helm', max_bytes=1024, version_args=('version',), version_pattern='3.18.6', allowed_hosts=('get.helm.sh',))
        archive = self.base / 'tool.tar.gz'
        with tarfile.open(archive, 'w:gz') as handle:
            data = b'binary'
            member = tarfile.TarInfo('linux-amd64/helm')
            member.size = len(data)
            handle.addfile(member, io.BytesIO(data))
        output = self.base / 'helm'
        _safe_tar_member(archive, pin, output)
        self.assertEqual(b'binary', output.read_bytes())
        with tarfile.open(archive, 'w:gz') as handle:
            data = b'escape'
            member = tarfile.TarInfo('../escape')
            member.size = len(data)
            handle.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(GitOpsValidationError, 'tool_archive_rejected'):
            _safe_tar_member(archive, pin, output)

    def test_tool_state_writes_reject_preexisting_symlinks(self) -> None:
        pin = GitOpsToolPin(name='helm', version='3.18.6', url='https://get.helm.sh/helm-v3.18.6-linux-amd64.tar.gz', sha256='0' * 64, archive_member='linux-amd64/helm', max_bytes=1024, version_args=('version',), version_pattern='3.18.6', allowed_hosts=('get.helm.sh',))
        outside = self.base / 'outside-sentinel'
        outside.write_text('keep', encoding='utf-8')
        archive = self.base / 'tool.tar.gz'
        with tarfile.open(archive, 'w:gz') as handle:
            data = b'binary'
            member = tarfile.TarInfo('linux-amd64/helm')
            member.size = len(data)
            handle.addfile(member, io.BytesIO(data))

        output = self.base / 'state' / 'bin' / 'helm'
        output.parent.mkdir(parents=True)
        output.symlink_to(outside)
        with self.assertRaisesRegex(GitOpsValidationError, 'tool_archive_rejected'):
            _safe_tar_member(archive, pin, output)

        download = self.base / 'state' / 'archives' / 'helm.tar.gz'
        download.parent.mkdir(parents=True)
        download.symlink_to(outside)
        with self.assertRaisesRegex(GitOpsValidationError, 'tool_download_failed'):
            _download(pin, download)
        self.assertEqual('keep', outside.read_text(encoding='utf-8'))

    def test_download_rejects_every_redirect_hop_outside_pinned_hosts(self) -> None:
        pin = GitOpsToolPin(name='helm', version='3.18.6', url='https://get.helm.sh/helm-v3.18.6-linux-amd64.tar.gz', sha256='0' * 64, archive_member='linux-amd64/helm', max_bytes=1024 * 1024, version_args=('version',), version_pattern='3.18.6', allowed_hosts=('get.helm.sh',))

        class RedirectingOpener:
            def __init__(self, handler):
                self.handler = handler

            def open(self, request, *, timeout):
                return self.handler.redirect_request(
                    request,
                    None,
                    302,
                    'Found',
                    {},
                    'https://attacker.invalid/tool.tar.gz',
                )

        with mock.patch(
            'ci_workflows.gitops_runtime.urllib.request.build_opener',
            side_effect=lambda handler: RedirectingOpener(handler),
        ):
            with self.assertRaisesRegex(GitOpsValidationError, 'tool_download_failed'):
                _download(pin, self.base / 'tool.tar.gz')
if __name__ == '__main__':
    unittest.main()
