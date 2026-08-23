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

    def test_release_is_exact_tag_only_on_github_hosted_capacity(self) -> None:
        events = self.document.data["on"]
        self.assertEqual(set(events), {"push"})
        self.assertEqual(events["push"]["tags"], ["ci-broker-*"])
        self.assertEqual(self.document.data["permissions"], {"contents": "read", "packages": "write"})
        self.assertEqual(set(self.document.data["jobs"]), {"release"})
        job = self.document.data["jobs"]["release"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["timeout-minutes"], 30)
        self.assertIn("^ci-broker-", self.workflow)
        self.assertIn("refs/tags/${RELEASE_TAG}", self.workflow)

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

    def test_release_builds_once_smokes_before_push_and_readbacks_anonymously(self) -> None:
        self.assertEqual(self.workflow.count("buildah bud"), 1)
        smoke = self.workflow.index("Smoke exact local broker image")
        publish = self.workflow.index("Publish exact immutable release tag")
        self.assertLess(smoke, publish)
        self.assertIn("ghcr.io/streamscapetv/ci-broker:${RELEASE_TAG}", self.workflow)
        self.assertIn("skopeo inspect --authfile", self.workflow)
        self.assertIn("buildah logout", self.workflow)
        self.assertIn("anonymous=", self.workflow)
        self.assertNotIn(":latest", self.workflow)
        self.assertNotIn("upload-artifact", self.workflow)

    def test_release_has_unconditional_registry_and_image_cleanup(self) -> None:
        self.assertIn("if: always()", self.workflow)
        self.assertIn("buildah rmi", self.workflow)
        self.assertIn("rm -f -- \"${authfile}\" \"${anon_authfile}\"", self.workflow)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", self.workflow)


if __name__ == "__main__":
    unittest.main()
