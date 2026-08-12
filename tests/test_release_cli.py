from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci_workflows.release import main


ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]["flux-runner-assets"]


def registered_outputs() -> tuple[dict[str, str], dict[str, object]]:
    image = CASE["image"]
    targets: dict[str, object] = {}
    for target, version_reference in image["version_references"].items():
        repository = version_reference.rsplit(":", 1)[0]
        targets[target] = {
            "repository": repository,
            "version": version_reference,
            "source_sha": image["source_references"][target],
            "manifest_digest": image["digests"][target],
        }
    return (
        dict(image["digests"]),
        {
            "targets": targets,
            "release": {
                "source_sha": CASE["source_sha"],
                "version": CASE["release_version"],
            },
            "flux": {
                "canary_id": "runner-images-canary",
                "previous_known_good": "flux-policy:runner-images/current-known-good",
                "rollback_id": "runner-images-rollback",
            },
        },
    )


def output_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        values[key] = value
    return values


class ReleaseCliTest(unittest.TestCase):
    def run_with_output(self, argv: list[str]) -> tuple[int, dict[str, str]]:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}, clear=False):
                result = main(["--root", str(ROOT), *argv])
            return result, output_values(output) if output.exists() else {}

    def test_public_plan_projects_registered_request(self) -> None:
        source = "a" * 40
        result, values = self.run_with_output(
            [
                "plan",
                "--release-contract",
                "backend",
                "--repository",
                "StreamScapeTV/iptv-backend",
                "--admitted-sha",
                source,
                "--release-tag",
                "v1.2.3",
                "--release-version",
                "1.2.3",
                "--request-id",
                "fixture-request-0003",
            ]
        )
        self.assertEqual(0, result)
        self.assertEqual("iptv-backend", values["release_id"])
        self.assertEqual("backend", values["release_contract"])
        self.assertEqual("v1.2.3", values["release_tag"])
        self.assertEqual(source, values["admitted_sha"])
        self.assertEqual("iptv-backend-image", values["image_product_id"])
        self.assertEqual("iptv-backend-chart", values["chart_product_id"])

    def test_runner_plan_chooses_stronger_contract_owned_buildah_tier(self) -> None:
        result, values = self.run_with_output(
            [
                "runner-plan",
                "--image-runs-on-json",
                '["linux","amd64","buildah","medium"]',
                "--chart-runs-on-json",
                '["linux","amd64","buildah","high"]',
            ]
        )
        self.assertEqual(0, result)
        self.assertEqual(
            ["linux", "amd64", "buildah", "high"],
            json.loads(values["runs_on_json"]),
        )

    def test_runner_plan_preserves_buildah_when_chart_is_general(self) -> None:
        result, values = self.run_with_output(
            [
                "runner-plan",
                "--image-runs-on-json",
                '["linux","amd64","buildah","small"]',
                "--chart-runs-on-json",
                '["linux","amd64","general"]',
            ]
        )
        self.assertEqual(0, result)
        self.assertEqual(
            ["linux", "amd64", "buildah", "small"],
            json.loads(values["runs_on_json"]),
        )

    def test_image_bindings_emits_sorted_helm_ready_digest_array(self) -> None:
        digests, immutable = registered_outputs()
        result, values = self.run_with_output(
            [
                "image-bindings",
                "--image-digest-json",
                json.dumps(digests),
                "--immutable-references-json",
                json.dumps(immutable),
                "--expected-source-sha",
                str(CASE["source_sha"]),
                "--expected-release-version",
                str(CASE["release_version"]),
            ]
        )
        self.assertEqual(0, result)
        required = json.loads(values["required_image_references_json"])
        expected = []
        for target in sorted(digests):
            repository = immutable["targets"][target]["repository"]
            expected.append(f"{repository}@{digests[target]}")
        self.assertEqual(expected, required)
        self.assertEqual(
            dict(sorted(digests.items())),
            json.loads(values["image_digests_json"]),
        )
        self.assertEqual("runner-images-canary", values["canary_id"])


if __name__ == "__main__":
    unittest.main()
