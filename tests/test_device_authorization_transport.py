from __future__ import annotations

import hashlib
import json
import unittest

from ci_workflows.ciw_device import _authorization_receipt, _execute_command
from ci_workflows.device_admission import request_from_environment
from ci_workflows.device_live import validate_authorization_receipt
from ci_workflows.device_plan_contract import build_plan
from ci_workflows.device_profile_contract import load_device_contract
from ci_workflows.device_types import DeviceValidationError
from device_test_support import ROOT, real_environment


class DeviceAuthorizationTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)
        self.environment = real_environment(
            repository="StreamScapeTV/example-consumer",
            family="ios",
            capability="transport-contract",
            host_capacity="apple",
            prepare_script_path="tests/fixtures/device-validation/scripts/prepare.sh",
            test_script_path="tests/fixtures/device-validation/scripts/test.sh",
            evidence_script_path="tests/fixtures/device-validation/scripts/evidence.sh",
            cleanup_script_path="tests/fixtures/device-validation/scripts/cleanup.sh",
        )
        self.environment["INPUT_REQUEST_ID"] = "issue-481-transport-contract"
        self.environment["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "true"
        self.plan = build_plan(
            self.contract,
            request_from_environment(self.environment, self.contract),
        )
        self.payload = {
            "packet_version": "device-authorization/1",
            "repository": self.plan.request.repository,
            "source_sha": self.plan.request.admitted_sha,
            "device_family": self.plan.request.family.value,
            "device_capability": self.plan.request.capability,
            "request_id": self.plan.request.request_id,
            "not_after_epoch": 4102444799,
        }

    def test_transport_whitespace_is_not_authorization_authority(self) -> None:
        transported = json.dumps(
            self.payload,
            sort_keys=False,
            indent=2,
        ) + "\n"
        environment = dict(self.environment)
        environment["CIW_DEVICE_AUTHORIZATION_RECEIPT"] = transported

        normalized = _authorization_receipt(environment)
        canonical = json.dumps(
            self.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        self.assertEqual(canonical, normalized)
        self.assertEqual(json.loads(transported), json.loads(normalized))
        self.assertEqual(
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            validate_authorization_receipt(normalized, plan=self.plan, now_epoch=1000),
        )

    def test_noncanonical_transport_reaches_authorized_plan(self) -> None:
        environment = dict(self.environment)
        environment["CIW_DEVICE_AUTHORIZATION_RECEIPT"] = (
            '{ "request_id": "issue-481-transport-contract", '
            '"repository": "StreamScapeTV/example-consumer", '
            '"device_capability": "transport-contract", '
            '"packet_version": "device-authorization/1", '
            '"not_after_epoch": 4102444799, '
            f'"source_sha": "{self.plan.request.admitted_sha}", '
            '"device_family": "ios" }\n'
        )

        outputs = _execute_command(
            root=ROOT,
            command="plan",
            source_root="source",
            inventory_fixture="",
            environment=environment,
        )
        self.assertEqual("planned", outputs["result"])
        self.assertEqual("true", outputs["execution_authorized"])
        self.assertEqual("", outputs["authorization_failure"])
        self.assertEqual(self.plan.request.request_id, outputs["request_id"])

    def test_duplicate_transport_keys_are_rejected_before_canonicalization(self) -> None:
        environment = dict(self.environment)
        environment["CIW_DEVICE_AUTHORIZATION_RECEIPT"] = (
            '{"device_capability":"transport-contract","device_family":"ios",'
            '"not_after_epoch":4102444799,"packet_version":"device-authorization/1",'
            '"repository":"StreamScapeTV/example-consumer",'
            '"request_id":"issue-481-transport-contract",'
            '"request_id":"issue-481-shadowed",'
            f'"source_sha":"{self.plan.request.admitted_sha}"}}'
        )
        with self.assertRaisesRegex(DeviceValidationError, "authorization_rejected"):
            _authorization_receipt(environment)

    def test_semantic_normalization_does_not_weaken_exact_binding(self) -> None:
        wrong = dict(self.payload)
        wrong["source_sha"] = "b" * 40
        environment = dict(self.environment)
        environment["CIW_DEVICE_AUTHORIZATION_RECEIPT"] = json.dumps(wrong, indent=1)
        normalized = _authorization_receipt(environment)
        with self.assertRaisesRegex(DeviceValidationError, "authorization_rejected"):
            validate_authorization_receipt(normalized, plan=self.plan, now_epoch=1000)

    def test_malformed_or_non_object_receipt_is_rejected(self) -> None:
        for raw in ("{", "[]", '"receipt"'):
            with self.subTest(raw=raw):
                environment = dict(self.environment)
                environment["CIW_DEVICE_AUTHORIZATION_RECEIPT"] = raw
                with self.assertRaisesRegex(DeviceValidationError, "authorization_rejected"):
                    _authorization_receipt(environment)


if __name__ == "__main__":
    unittest.main()
