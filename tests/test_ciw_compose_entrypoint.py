from __future__ import annotations

import json
import unittest

from ci_workflows.ciw_compose_entrypoint import _adapter_environment
from ci_workflows.service_compose_primitives import ServiceComposeError


class ComposeEntrypointTests(unittest.TestCase):
    def _environment(self, plan: dict[str, object]) -> dict[str, str]:
        return {
            "INPUT_ADMITTED_SHA": "a" * 40,
            "INPUT_WORKING_DIRECTORY": "integration",
            "INPUT_VALIDATION_PLAN_JSON": json.dumps(plan),
            "PATH": "/usr/bin",
        }

    def _plan(self) -> dict[str, object]:
        return {
            "compose_file": "compose.test.yml",
            "services": ["api", "db"],
            "env_files": [".env.test"],
            "readiness": [
                {
                    "service": "api",
                    "kind": "http",
                    "url": "http://127.0.0.1:8080/ready",
                    "expected_statuses": [200],
                },
                {
                    "service": "db",
                    "kind": "tcp",
                    "host": "127.0.0.1",
                    "port": 5432,
                },
            ],
            "validation_script_path": "scripts/validate-services.sh",
            "validation_timeout_seconds": 45,
        }

    def test_projects_bounded_plan_into_existing_adapter_inputs_and_fixes_podman(self) -> None:
        projected = _adapter_environment(self._environment(self._plan()))

        self.assertEqual("a" * 40, projected["INPUT_ADMITTED_SHA"])
        self.assertEqual("integration", projected["INPUT_WORKING_DIRECTORY"])
        self.assertEqual("compose.test.yml", projected["INPUT_COMPOSE_FILE"])
        self.assertEqual("podman", projected["INPUT_COMPOSE_TOOL"])
        self.assertEqual(["api", "db"], json.loads(projected["INPUT_SERVICES_JSON"]))
        self.assertEqual([".env.test"], json.loads(projected["INPUT_ENV_FILES_JSON"]))
        self.assertEqual(2, len(json.loads(projected["INPUT_READINESS_JSON"])))
        self.assertEqual(
            "scripts/validate-services.sh",
            projected["INPUT_VALIDATION_SCRIPT_PATH"],
        )
        self.assertEqual("45", projected["INPUT_VALIDATION_TIMEOUT_SECONDS"])
        self.assertEqual("/usr/bin", projected["PATH"])

    def test_optional_lists_and_timeout_have_bounded_defaults(self) -> None:
        plan = self._plan()
        plan.pop("services")
        plan.pop("env_files")
        plan.pop("validation_timeout_seconds")
        projected = _adapter_environment(self._environment(plan))

        self.assertEqual([], json.loads(projected["INPUT_SERVICES_JSON"]))
        self.assertEqual([], json.loads(projected["INPUT_ENV_FILES_JSON"]))
        self.assertEqual("900", projected["INPUT_VALIDATION_TIMEOUT_SECONDS"])

    def test_rejects_missing_required_and_unknown_plan_fields(self) -> None:
        missing = self._plan()
        missing.pop("readiness")
        with self.assertRaises(ServiceComposeError) as captured:
            _adapter_environment(self._environment(missing))
        self.assertEqual("compose_plan_invalid", captured.exception.code)

        unknown = self._plan()
        unknown["container_engine"] = "docker"
        with self.assertRaises(ServiceComposeError) as captured:
            _adapter_environment(self._environment(unknown))
        self.assertEqual("compose_plan_invalid", captured.exception.code)

    def test_rejects_non_array_topology_and_readiness_fields(self) -> None:
        for field in ("services", "env_files", "readiness"):
            with self.subTest(field=field):
                plan = self._plan()
                plan[field] = "not-an-array"
                with self.assertRaises(ServiceComposeError):
                    _adapter_environment(self._environment(plan))

    def test_rejects_invalid_validation_timeout_types_and_bounds(self) -> None:
        for timeout in (True, 0, 3601, "45"):
            with self.subTest(timeout=timeout):
                plan = self._plan()
                plan["validation_timeout_seconds"] = timeout
                with self.assertRaises(ServiceComposeError) as captured:
                    _adapter_environment(self._environment(plan))
                self.assertEqual(
                    "compose_validation_timeout_invalid",
                    captured.exception.code,
                )

    def test_rejects_oversized_and_non_json_plans(self) -> None:
        environment = self._environment(self._plan())
        environment["INPUT_VALIDATION_PLAN_JSON"] = "{" + ("x" * (16 * 1024))
        with self.assertRaises(ServiceComposeError) as captured:
            _adapter_environment(environment)
        self.assertEqual("compose_plan_invalid", captured.exception.code)

        environment["INPUT_VALIDATION_PLAN_JSON"] = "not-json"
        with self.assertRaises(ServiceComposeError) as captured:
            _adapter_environment(environment)
        self.assertEqual("compose_plan_invalid", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
