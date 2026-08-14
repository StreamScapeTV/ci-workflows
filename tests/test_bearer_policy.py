from __future__ import annotations

import unittest

from ci_workflows.policy import _split_marker_matches

BEARER_MARKER = {
    "id": "bearer-token",
    "segments": ["Authorization", ": Bearer "],
    "minimum_suffix_length": 1,
    "allowed_suffix_patterns": ["TOKEN", "synthetic-*"],
}


class BearerPolicyTests(unittest.TestCase):
    def test_explicit_placeholders_are_allowed(self) -> None:
        self.assertFalse(
            _split_marker_matches("Authorization: Bearer TOKEN", BEARER_MARKER)
        )
        self.assertFalse(
            _split_marker_matches(
                "Authorization: Bearer synthetic-access-token",
                BEARER_MARKER,
            )
        )

    def test_short_credential_shaped_suffix_is_rejected(self) -> None:
        value = "A1b2" + "C3d4"
        self.assertTrue(
            _split_marker_matches(
                "Authorization: Bearer " + value,
                BEARER_MARKER,
            )
        )


if __name__ == "__main__":
    unittest.main()
