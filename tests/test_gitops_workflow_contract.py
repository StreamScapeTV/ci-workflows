from __future__ import annotations
import json
import unittest
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]

class GitOpsWorkflowContractTests(unittest.TestCase):

    def test_reusable_workflow_exposes_only_bounded_source_inputs(self) -> None:
        path = ROOT / '.github/workflows/reusable-gitops-validation.yml'
        source = path.read_text(encoding='utf-8')
        workflow = yaml.safe_load(source)
        inputs = workflow[True]['workflow_call']['inputs']
        self.assertEqual({'admitted_sha', 'validation_profile', 'consumer_contract', 'change_base_sha', 'policy_script_profile', 'artifact_exception_id'}, set(inputs))
        for forbidden in ('runner', 'runs_on', 'tool_url', 'command', 'arguments', 'registry', 'cluster', 'kubeconfig', 'sops_key', 'secret_name', 'deployment'):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual({'actions': 'read', 'contents': 'read'}, workflow['permissions'])
        self.assertNotIn('secrets:', source)
        self.assertNotIn('upload-artifact', source)
        self.assertNotIn('runs-on: macOS', source)
        self.assertNotIn('self-hosted', source)
        self.assertNotIn('runs-on: portable', source)
        self.assertEqual(['linux', 'amd64', 'general'], workflow['jobs']['plan']['runs-on'])
        self.assertIn('fromJSON(needs.plan.outputs.runs_on_json)', source)
        self.assertIn('CI / GitOps validation', source)
        self.assertIn('if: always()', source)
        self.assertIn('Confirm zero Actions artifacts', source)

    def test_smoke_is_exact_head_direct_general_linux_and_zero_artifact(self) -> None:
        source = (ROOT / '.github/workflows/gitops-validation-smoke.yml').read_text()
        workflow = yaml.safe_load(source)
        self.assertEqual({'actions': 'read', 'contents': 'read'}, workflow['permissions'])
        self.assertIn('github.event.pull_request.head.repo.full_name == github.repository', source)
        self.assertIn('test "$(git rev-parse HEAD)"', source)
        self.assertIn('full', source)
        self.assertIn('synthetic', source)
        self.assertIn('3.18.6', source)
        self.assertIn('5.8.1', source)
        self.assertIn('Verify GitOps smoke retained zero artifacts', source)
        self.assertNotIn('upload-artifact', source)
        self.assertNotIn('macOS', source)
        self.assertNotIn('runs-on: portable', source)
        self.assertEqual(['linux', 'amd64', 'general'], workflow['jobs']['plan']['runs-on'])
        self.assertEqual(['linux', 'amd64', 'general'], workflow['jobs']['artifacts']['runs-on'])
        self.assertEqual('${{ fromJSON(needs.plan.outputs.runs_on_json) }}', workflow['jobs']['execute']['runs-on'])
        self.assertEqual("${{ always() && needs.plan.result != 'skipped' }}", workflow['jobs']['artifacts']['if'])
        for job in workflow['jobs'].values():
            if 'uses' not in job:
                self.assertGreater(job.get('timeout-minutes', 0), 0)

    def test_action_is_thin_and_rejects_authority_inputs(self) -> None:
        source = (ROOT / 'actions/validate-gitops/action.yml').read_text()
        action = yaml.safe_load(source)
        self.assertEqual('composite', action['runs']['using'])
        self.assertEqual(1, len(action['runs']['steps']))
        inputs = set(action['inputs'])
        self.assertEqual({'phase', 'admitted_sha', 'validation_profile', 'consumer_contract', 'change_base_sha', 'policy_script_profile', 'artifact_exception_id'}, inputs)
        self.assertIn('scripts/ci/gitops.py', source)
        self.assertNotIn('curl ', source)
        self.assertNotIn('helm ', source)
        self.assertNotIn('kubectl', source)
        self.assertNotIn('sops', source.lower())

    def test_fixtures_have_descriptive_positive_and_negative_matrix(self) -> None:
        payload = json.loads((ROOT / 'tests/fixtures/gitops-validation/cases.json').read_text())
        self.assertGreaterEqual(len(payload['positive']), 8)
        self.assertGreaterEqual(len(payload['negative']), 10)
        self.assertTrue((ROOT / 'tests/fixtures/gitops-validation/negative/duplicate-key.yaml').is_file())
        self.assertTrue((ROOT / 'tests/fixtures/gitops-validation/negative/plaintext-sops.yaml').is_file())

    def test_docs_preserve_source_only_security_and_current_consumer_boundary(self) -> None:
        combined = ((ROOT / 'docs/workflows/gitops-validation.md').read_text() + (ROOT / 'docs/architecture/gitops-validation.md').read_text()).lower()
        for required in ('validation.gitops', 'source-only', 'helm 3.18.6', 'kustomize 5.8.1', 'sops', 'never decrypt', 'zero routine artifacts', 'portable', 'flux', 'iptv-backend', 'agent-state'):
            self.assertIn(required, combined)
        for forbidden in ('`sops decrypt`', 'kubectl apply', 'flux reconcile', 'registry login', 'runs-on: macos'):
            self.assertNotIn(forbidden, combined)
if __name__ == '__main__':
    unittest.main()
