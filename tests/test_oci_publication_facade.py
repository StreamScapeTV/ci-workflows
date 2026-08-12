from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import oci
from ci_workflows import oci_publish_contract as publication
from ci_workflows.oci_publish import OciPublishError


class OciPublicationFacadeTests(unittest.TestCase):
    def test_publish_and_read_back_delegate_to_guarded_publication(self) -> None:
        plan = object()
        environment = {"GITHUB_EVENT_NAME": "push"}
        with patch("ci_workflows.oci._publish", return_value={"result": "published"}) as publish:
            result = oci.publish(plan, environment)  # type: ignore[arg-type]
        self.assertEqual(result["result"], "published")
        publish.assert_called_once_with(plan, environment)

        with patch("ci_workflows.oci._read_back", return_value={"result": "read-back"}) as read_back:
            result = oci.read_back(plan, environment)  # type: ignore[arg-type]
        self.assertEqual(result["result"], "read-back")
        read_back.assert_called_once_with(plan, environment)

    def test_internal_build_fixture_is_not_a_publication_product(self) -> None:
        environment = {
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            "GITHUB_EVENT_NAME": "push",
            "INPUT_ADMITTED_SHA": "a" * 40,
            "INPUT_RELEASE_AUTHORITY_SHA": "a" * 40,
            "INPUT_PRODUCT_ID": "ciw-oci-smoke",
            "INPUT_RELEASE_VERSION": "1.2.3",
        }
        with self.assertRaisesRegex(OciPublishError, "unsupported_product"):
            publication.request_from_environment(environment)

    def test_registered_publication_facade_names_exist(self) -> None:
        self.assertTrue(callable(oci.publish))
        self.assertTrue(callable(oci.read_back))
        self.assertEqual(oci.publish.__module__, "ci_workflows.oci")
        self.assertEqual(oci.read_back.__module__, "ci_workflows.oci")


if __name__ == "__main__":
    unittest.main()
