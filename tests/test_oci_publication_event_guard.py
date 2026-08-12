from __future__ import annotations

import unittest
from types import SimpleNamespace

from ci_workflows import oci_publish_guards as guards
from ci_workflows.oci_publish import OciPublishError


class PublicationEventGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = SimpleNamespace(release_version="1.2.3")

    def test_only_exact_version_tag_push_enables_registry_writes(self) -> None:
        environment = {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF_TYPE": "tag",
            "GITHUB_REF_NAME": "1.2.3",
            "GITHUB_REF": "refs/tags/1.2.3",
        }
        self.assertTrue(guards._publication_allowed(self.plan, environment))  # type: ignore[arg-type]

        for broken in (
            {**environment, "GITHUB_REF_TYPE": "branch", "GITHUB_REF": "refs/heads/main"},
            {**environment, "GITHUB_REF_NAME": "9.9.9", "GITHUB_REF": "refs/tags/9.9.9"},
            {**environment, "GITHUB_REF": "refs/heads/1.2.3"},
        ):
            with self.subTest(broken=broken):
                with self.assertRaisesRegex(OciPublishError, "publication_ref_forbidden"):
                    guards._publication_allowed(self.plan, broken)  # type: ignore[arg-type]

    def test_manual_dispatch_is_read_only_and_other_events_are_untrusted(self) -> None:
        self.assertFalse(
            guards._publication_allowed(  # type: ignore[arg-type]
                self.plan, {"GITHUB_EVENT_NAME": "workflow_dispatch"}
            )
        )
        with self.assertRaisesRegex(OciPublishError, "publication_untrusted"):
            guards._publication_allowed(  # type: ignore[arg-type]
                self.plan, {"GITHUB_EVENT_NAME": "pull_request"}
            )


if __name__ == "__main__":
    unittest.main()
