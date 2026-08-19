from __future__ import annotations

import unittest

from ci_workflows import ciw_android
from ci_workflows.ciw_types import CIWError


class AndroidProtectedFullCompileGroupTests(unittest.TestCase):
    def test_compile_group_runs_after_pre_unit_and_before_unit(self) -> None:
        plan = ciw_android._protected_full_plan(
            {
                "pre_unit_tasks": [":app:kspDebugKotlin"],
                "compile_tasks": [":app:compileDebugKotlin"],
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "gradle", "tasks": [":app:verifyRoomSchemas"]},
            },
            "validation_plan_invalid",
        )

        self.assertEqual(plan.pre_unit_tasks, (":app:kspDebugKotlin",))
        self.assertEqual(plan.compile_tasks, (":app:compileDebugKotlin",))
        self.assertEqual(
            [name for name, tasks in plan.gradle_groups if tasks],
            ["pre_unit", "compile", "unit", "lint", "assemble", "schema"],
        )
        self.assertEqual(
            plan.gradle_tasks,
            (
                ":app:kspDebugKotlin",
                ":app:compileDebugKotlin",
                ":app:testDebugUnitTest",
                ":app:lintDebug",
                ":app:assembleDebug",
                ":app:verifyRoomSchemas",
            ),
        )

    def test_legacy_plan_omitting_compile_group_is_unchanged(self) -> None:
        plan = ciw_android._protected_full_plan(
            {
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "none"},
            },
            "validation_plan_invalid",
        )

        self.assertEqual(plan.pre_unit_tasks, ())
        self.assertEqual(plan.compile_tasks, ())
        self.assertEqual(
            [name for name, tasks in plan.gradle_groups if tasks],
            ["unit", "lint", "assemble"],
        )

    def test_empty_compile_group_fails_closed(self) -> None:
        with self.assertRaises(CIWError) as failure:
            ciw_android._protected_full_plan(
                {
                    "compile_tasks": [],
                    "unit_tasks": [":app:testDebugUnitTest"],
                    "lint_tasks": [":app:lintDebug"],
                    "assemble_tasks": [":app:assembleDebug"],
                    "schema": {"mode": "none"},
                },
                "validation_plan_invalid",
            )
        self.assertEqual(failure.exception.code, "validation_plan_invalid")

    def test_compile_task_cannot_duplicate_any_other_group(self) -> None:
        duplicate_cases = (
            {
                "pre_unit_tasks": [":app:compileDebugKotlin"],
                "compile_tasks": [":app:compileDebugKotlin"],
            },
            {
                "compile_tasks": [":app:testDebugUnitTest"],
            },
            {
                "compile_tasks": [":app:lintDebug"],
            },
            {
                "compile_tasks": [":app:assembleDebug"],
            },
        )
        for extra in duplicate_cases:
            with self.subTest(extra=extra):
                value = {
                    "unit_tasks": [":app:testDebugUnitTest"],
                    "lint_tasks": [":app:lintDebug"],
                    "assemble_tasks": [":app:assembleDebug"],
                    "schema": {"mode": "none"},
                    **extra,
                }
                with self.assertRaises(CIWError) as failure:
                    ciw_android._protected_full_plan(value, "validation_plan_invalid")
                self.assertEqual(failure.exception.code, "validation_plan_invalid")


if __name__ == "__main__":
    unittest.main()
