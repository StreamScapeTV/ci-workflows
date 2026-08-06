from __future__ import annotations

import unittest

from ci_workflows.validation_policy import (
    _contains_mutable_publication_tag,
    _is_always_cleanup_step,
)


class ValidationPolicyHelperTest(unittest.TestCase):
    def test_latest_guard_is_not_a_mutable_publication(self) -> None:
        run = """helm push chart.tgz oci://registry/charts
if grep -Eq 'latest' rendered.yaml; then
  exit 1
fi
"""
        self.assertFalse(_contains_mutable_publication_tag(run))

    def test_latest_publication_is_rejected(self) -> None:
        self.assertTrue(
            _contains_mutable_publication_tag(
                "docker push registry.example/app:latest\n"
            )
        )

    def test_clean_and_cleanup_names_are_unconditional_cleanup(self) -> None:
        self.assertTrue(
            _is_always_cleanup_step(
                {"name": "Clean publication credentials and state", "if": "always()"}
            )
        )
        self.assertTrue(
            _is_always_cleanup_step(
                {"name": "Cleanup temporary state", "if": "${{ always() }}"}
            )
        )

    def test_cleanup_requires_exact_always_condition(self) -> None:
        self.assertFalse(
            _is_always_cleanup_step(
                {"name": "Clean publication credentials", "if": "success()"}
            )
        )


if __name__ == "__main__":
    unittest.main()
