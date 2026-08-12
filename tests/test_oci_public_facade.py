from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import unittest

from ci_workflows import oci


class OciPublicFacadeTests(unittest.TestCase):
    def test_build_delegates_to_hardened_safe_executor(self) -> None:
        sentinel = object()
        repository_root = Path("repo")
        source_root = Path("source")
        plan = object()
        environment = {"GITHUB_RUN_ID": "1"}
        secret_files = {"token": Path("secret")}
        with patch("ci_workflows.oci._execute_plan", return_value=sentinel) as execute:
            result = oci.build(
                repository_root,
                source_root,
                plan,  # type: ignore[arg-type]
                environment,
                secret_files,
            )
        self.assertIs(sentinel, result)
        execute.assert_called_once_with(
            repository_root,
            source_root,
            plan,
            environment,
            secret_files,
        )

    def test_inspect_delegates_to_strict_layout_inspector(self) -> None:
        sentinel = object()
        layout = Path("layout")
        target = object()
        labels = {"org.opencontainers.image.revision": "a" * 40}
        with patch("ci_workflows.oci._inspect_layout", return_value=sentinel) as inspect:
            result = oci.inspect(
                layout,
                target,  # type: ignore[arg-type]
                labels,
            )
        self.assertIs(sentinel, result)
        inspect.assert_called_once_with(layout, target, labels)


if __name__ == "__main__":
    unittest.main()
