from __future__ import annotations

import unittest
from pathlib import Path

from ci_workflows.validation_model import load_actions_yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci-broker-image.yml"
CONTAINERFILE = ROOT / "broker/Containerfile"


class BrokerImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_actions_yaml(WORKFLOW, ROOT)
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.containerfile = CONTAINERFILE.read_text(encoding="utf-8")

    def test_release_is_exact_tag_or_owner_replay_on_reviewed_macos_capacity(self) -> None:
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
        self.assertEqual(admit["runs-on"], ["macOS", "ARM64"])
        self.assertEqual(self.document.data["jobs"]["image"]["runs-on"], ["macOS", "ARM64"])
        self.assertEqual(self.document.data["jobs"]["chart"]["runs-on"], ["macOS", "ARM64"])
        self.assertIn("^ci-broker-", self.workflow)
        self.assertIn("format('refs/tags/{0}', inputs.release_tag)", self.workflow)
        self.assertIn('test "${GITHUB_REF}" = "refs/tags/${RELEASE_TAG}"', self.workflow)
        self.assertIn('test "${GITHUB_ACTOR}" = "mimranfaruqi"', self.workflow)
        self.assertNotIn("ubuntu-latest", self.workflow)

    def test_container_uses_one_immutable_base_and_non_root_runtime(self) -> None:
        lines = [line.strip() for line in self.containerfile.splitlines() if line.strip()]
        from_lines = [line for line in lines if line.startswith("FROM ")]
        self.assertEqual(len(from_lines), 1)
        self.assertRegex(
            from_lines[0],
            r"^FROM docker[.]io/library/python@sha256:[0-9a-f]{64}$",
        )
        self.assertIn("USER 65532:65532", lines)
        self.assertIn("EXPOSE 8080", lines)
        self.assertIn(
            'ENTRYPOINT ["python3", "/opt/ci-broker/scripts/ci/ci_broker.py", "server"]',
            lines,
        )
        self.assertIn("command -v openssl", self.containerfile)
        self.assertIn("ci_broker.py self-check", self.containerfile)
        for forbidden in ("apt-get", "pip install", "curl ", "wget ", "ADD http"):
            self.assertNotIn(forbidden, self.containerfile)

    def test_release_cross_builds_once_smokes_before_private_push_and_readback(self) -> None:
        self.assertEqual(self.workflow.count("docker buildx build"), 1)
        smoke = self.workflow.index("Smoke exact local broker image")
        publish = self.workflow.index("Publish immutable image version and read it back")
        chart = self.workflow.index("Package publish and read back broker Helm chart")
        self.assertLess(smoke, publish)
        self.assertLess(publish, chart)
        self.assertIn("--platform linux/amd64", self.workflow)
        self.assertIn("docker run", self.workflow)
        self.assertIn("git.faruqi.dev", self.workflow)
        self.assertIn("${REGISTRY}/${REGISTRY_NAMESPACE}/${IMAGE_NAME}:${VERSION}", self.workflow)
        self.assertIn("docker pull --platform linux/amd64", self.workflow)
        self.assertIn("docker login", self.workflow)
        self.assertIn("canonicalize_chart_archive", self.workflow)
        self.assertIn("helm push", self.workflow)
        self.assertIn("helm pull", self.workflow)
        self.assertNotIn("buildah bud", self.workflow)
        self.assertNotIn("skopeo inspect", self.workflow)
        self.assertNotIn("ghcr.io", self.workflow)
        self.assertNotIn(":latest", self.workflow)
        self.assertNotIn("upload-artifact", self.workflow)

    def test_release_has_unconditional_registry_image_and_helm_cleanup(self) -> None:
        self.assertIn("if: always()", self.workflow)
        self.assertIn("docker image rm -f", self.workflow)
        self.assertIn('docker logout "${REGISTRY}"', self.workflow)
        self.assertIn('rm -rf -- "${BROKER_DOCKER_CONFIG}"', self.workflow)
        self.assertIn('helm registry logout "${REGISTRY}"', self.workflow)
        self.assertIn('rm -rf -- "${BROKER_HELM_ROOT}"', self.workflow)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", self.workflow)


if __name__ == "__main__":
    unittest.main()
