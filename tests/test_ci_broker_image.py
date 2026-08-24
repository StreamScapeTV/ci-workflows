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

    def test_release_is_exact_tag_only_on_reviewed_arc_capacity(self) -> None:
        events = self.document.data["on"]
        self.assertEqual(set(events), {"push"})
        self.assertEqual(events["push"]["tags"], ["ci-broker-*"])
        self.assertEqual(self.document.data["permissions"], {"contents": "read"})
        self.assertEqual(set(self.document.data["jobs"]), {"admit", "image", "chart"})
        self.assertEqual(
            self.document.data["jobs"]["admit"]["runs-on"],
            ["linux", "amd64", "general", "tiny"],
        )
        self.assertEqual(
            self.document.data["jobs"]["image"]["runs-on"],
            ["linux", "amd64", "buildah", "small"],
        )
        self.assertEqual(
            self.document.data["jobs"]["chart"]["runs-on"],
            ["linux", "amd64", "general", "small"],
        )
        self.assertIn("^ci-broker-", self.workflow)
        self.assertIn("refs/tags/${RELEASE_TAG}", self.workflow)
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

    def test_release_builds_once_smokes_before_private_push_and_readback(self) -> None:
        self.assertEqual(self.workflow.count("buildah bud"), 1)
        smoke = self.workflow.index("Smoke exact local broker image")
        publish = self.workflow.index("Publish immutable image version and read it back")
        chart = self.workflow.index("Package publish and read back broker Helm chart")
        self.assertLess(smoke, publish)
        self.assertLess(publish, chart)
        self.assertIn("git.faruqi.dev", self.workflow)
        self.assertIn("${REGISTRY}/${REGISTRY_NAMESPACE}/${IMAGE_NAME}:${VERSION}", self.workflow)
        self.assertIn("skopeo inspect --authfile", self.workflow)
        self.assertIn("buildah logout", self.workflow)
        self.assertIn("helm push", self.workflow)
        self.assertIn("helm pull", self.workflow)
        self.assertNotIn("ghcr.io", self.workflow)
        self.assertNotIn(":latest", self.workflow)
        self.assertNotIn("upload-artifact", self.workflow)

    def test_release_has_unconditional_registry_image_and_helm_cleanup(self) -> None:
        self.assertIn("if: always()", self.workflow)
        self.assertIn("buildah rmi", self.workflow)
        self.assertIn('rm -f -- "${BROKER_REGISTRY_AUTH}"', self.workflow)
        self.assertIn('helm registry logout "${REGISTRY}"', self.workflow)
        self.assertIn('rm -rf -- "${BROKER_HELM_ROOT}"', self.workflow)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", self.workflow)


if __name__ == "__main__":
    unittest.main()
