from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

from ci_workflows.validation_model import load_actions_yaml

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "ci-broker"
WORKFLOW = ROOT / ".github/workflows/ci-broker-image.yml"
CONTAINERFILE = PROJECT / "Containerfile"
DEPLOYED_VALUES = PROJECT / "deployment-values.yaml"
HTTP_INTEGRATION = PROJECT / "smoke_test.py"


class BrokerImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_actions_yaml(WORKFLOW, ROOT)
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.containerfile = CONTAINERFILE.read_text(encoding="utf-8")

    def test_standalone_broker_project_contains_runtime_deployment_and_smoke(self) -> None:
        self.assertTrue((PROJECT / "app.py").is_file())
        self.assertTrue(CONTAINERFILE.is_file())
        self.assertTrue(HTTP_INTEGRATION.is_file())
        self.assertTrue(DEPLOYED_VALUES.is_file())
        self.assertTrue((PROJECT / "chart/Chart.yaml").is_file())
        self.assertFalse((ROOT / "broker").exists())
        self.assertFalse((ROOT / "charts/ci-broker").exists())
        self.assertFalse((ROOT / "scripts/ci/ci_broker.py").exists())
        for retired in (
            "ci_broker.py",
            "ci_broker_action.py",
            "ci_broker_dependencies.py",
            "ci_broker_fallback.py",
            "ci_broker_start_guard.py",
            "ci_relay.py",
            "ci_relay_server.py",
        ):
            self.assertFalse((ROOT / "src/ci_workflows" / retired).exists(), retired)

    def test_release_is_exact_tag_or_owner_replay_on_reviewed_arc_capacity(self) -> None:
        events = self.document.data["on"]
        self.assertEqual(set(events), {"push", "workflow_dispatch"})
        self.assertEqual(events["push"]["tags"], ["ci-broker-*"])
        manual = events["workflow_dispatch"]["inputs"]
        self.assertEqual(set(manual), {"release_tag"})
        self.assertTrue(manual["release_tag"]["required"])
        self.assertEqual(manual["release_tag"]["type"], "string")
        self.assertEqual(self.document.data["permissions"], {"contents": "read"})
        self.assertEqual(set(self.document.data["jobs"]), {"admit", "image", "chart"})
        admit = self.document.data["jobs"]["admit"]
        self.assertEqual(
            admit["if"],
            "${{ github.event_name != 'workflow_dispatch' || github.actor == 'mimranfaruqi' }}",
        )
        self.assertEqual(admit["runs-on"], ["linux", "amd64", "general", "tiny"])
        self.assertEqual(self.document.data["jobs"]["image"]["runs-on"], ["linux", "amd64", "buildah", "small"])
        self.assertEqual(self.document.data["jobs"]["chart"]["runs-on"], ["linux", "amd64", "general", "small"])
        self.assertIn("^ci-broker-", self.workflow)
        self.assertIn("format('refs/tags/{0}', inputs.release_tag)", self.workflow)
        self.assertNotIn("ubuntu-latest", self.workflow)

    def test_container_contains_only_standalone_app(self) -> None:
        lines = [line.strip() for line in self.containerfile.splitlines() if line.strip()]
        from_lines = [line for line in lines if line.startswith("FROM ")]
        self.assertEqual(len(from_lines), 1)
        self.assertRegex(from_lines[0], r"^FROM docker[.]io/library/python@sha256:[0-9a-f]{64}$")
        self.assertIn("USER 65532:65532", lines)
        self.assertIn("EXPOSE 8080", lines)
        self.assertIn('ENTRYPOINT ["python3", "/opt/ci-broker/app.py", "server"]', lines)
        self.assertIn("COPY --chown=65532:65532 app.py /opt/ci-broker/app.py", self.containerfile)
        self.assertNotIn("src/ci_workflows", self.containerfile)
        self.assertNotIn("scripts/ci", self.containerfile)
        self.assertIn("command -v openssl", self.containerfile)
        self.assertIn("app.py self-check", self.containerfile)
        for forbidden in ("apt-get", "pip install", "curl ", "wget ", "ADD http"):
            self.assertNotIn(forbidden, self.containerfile)

    def test_repository_self_check_executes_real_broker_http_smoke(self) -> None:
        result = subprocess.run(
            [sys.executable, str(HTTP_INTEGRATION)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("test_health_webhook_claim_and_outbound_dispatch_over_real_http", result.stdout)

    def test_release_builds_project_context_and_smokes_exact_image_before_push(self) -> None:
        self.assertEqual(self.workflow.count("buildah bud"), 1)
        smoke = self.workflow.index("Smoke exact local broker image over HTTP")
        publish = self.workflow.index("Publish immutable image version and read it back")
        chart = self.workflow.index("Package publish and read back broker Helm chart")
        self.assertLess(smoke, publish)
        self.assertLess(publish, chart)
        self.assertIn("--file ci-broker/Containerfile", self.workflow)
        self.assertIn("\n            ci-broker\n", self.workflow)
        self.assertIn("ci-broker/smoke_test.py", self.workflow)
        self.assertIn("python3 /opt/ci-broker/app.py self-check", self.workflow)
        self.assertIn("python3 /opt/ci-broker/smoke_test.py", self.workflow)
        self.assertIn("skopeo inspect --authfile", self.workflow)
        self.assertIn("helm push", self.workflow)
        self.assertIn("helm pull", self.workflow)
        self.assertNotIn("ghcr.io", self.workflow)
        self.assertNotIn(":latest", self.workflow)
        self.assertNotIn("upload-artifact", self.workflow)

    def test_release_validates_project_deployment_values_before_chart_publication(self) -> None:
        self.assertTrue(DEPLOYED_VALUES.is_file())
        self.assertIn('chart="ci-broker/chart"', self.workflow)
        self.assertIn('deployed_values="ci-broker/deployment-values.yaml"', self.workflow)
        self.assertIn('helm lint --strict "${chart}" --values "${deployed_values}" --set-string image.tag="${VERSION}"', self.workflow)
        self.assertIn('helm template ci-broker "${chart}" --values "${deployed_values}" --set-string image.tag="${VERSION}"', self.workflow)
        lint = self.workflow.index('helm lint --strict "${chart}" --values "${deployed_values}"')
        publish = self.workflow.index("Publish immutable chart version and read it back")
        self.assertLess(lint, publish)

    def test_release_has_unconditional_registry_image_and_helm_cleanup(self) -> None:
        self.assertIn("if: always()", self.workflow)
        self.assertIn("buildah rmi", self.workflow)
        self.assertIn('rm -f -- "${BROKER_REGISTRY_AUTH}"', self.workflow)
        self.assertIn('helm registry logout "${REGISTRY}"', self.workflow)
        self.assertIn('rm -rf -- "${BROKER_HELM_ROOT}"', self.workflow)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", self.workflow)


if __name__ == "__main__":
    unittest.main()
