from __future__ import annotations

import unittest

from ci_workflows.oci_publish_contract import OciPublishError, runner_rebuild_decision


class RunnerPublicationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.local = "sha256:" + "1" * 64
        self.other = "sha256:" + "2" * 64

    def test_first_publish_writes_version_and_source_tags(self) -> None:
        self.assertEqual(
            (True, True, False),
            runner_rebuild_decision(self.local, None, None),
        )

    def test_exact_replay_is_a_noop(self) -> None:
        self.assertEqual(
            (False, False, True),
            runner_rebuild_decision(self.local, self.local, self.local),
        )

    def test_version_tag_may_be_replaced_when_source_tag_is_unchanged(self) -> None:
        self.assertEqual(
            (True, False, True),
            runner_rebuild_decision(self.local, self.other, self.local),
        )

    def test_conflicting_source_sha_tag_fails_closed(self) -> None:
        with self.assertRaisesRegex(OciPublishError, "immutable_reference_conflict"):
            runner_rebuild_decision(self.local, self.local, self.other)


if __name__ == "__main__":
    unittest.main()
