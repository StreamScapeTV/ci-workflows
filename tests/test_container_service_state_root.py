from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ContainerServiceStateRootTests(unittest.TestCase):
    def test_buildah_state_root_precedes_private_child_directories(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/container-service.yml").read_text(encoding="utf-8")
        )
        build = next(
            step
            for step in workflow["jobs"]["conformance"]["steps"]
            if step.get("name") == "Build exact local service image"
        )["run"]

        assignment = 'state_root="${RUNTIME_ROOT}/buildah"'
        root_create = 'mkdir -m 0700 "${state_root}"'
        first_child = '"${state_root}/graphroot"'

        self.assertLess(build.index(assignment), build.index(root_create))
        self.assertLess(build.index(root_create), build.index(first_child))
        for child in ("graphroot", "runroot", "tmp", "home", "cache"):
            self.assertIn(f'"${{state_root}}/{child}"', build)
