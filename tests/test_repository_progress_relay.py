from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/repository_progress_relay.py"
SPEC = importlib.util.spec_from_file_location("repository_progress_relay", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class FakeReporter:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls: list[tuple[str, dict[str, object]]] = []

    def report(self, ci_run_id: str, projection: dict[str, object]) -> str:
        self.calls.append((ci_run_id, projection))
        if self.fail_first and len(self.calls) == 1:
            raise mod.ReportError("transport", transient=True)
        return "progress_recorded"


class ScriptedEvent:
    def __init__(self, stop_after_waits: int) -> None:
        self.waits = 0
        self.stop_after_waits = stop_after_waits

    def is_set(self) -> bool:
        return self.waits >= self.stop_after_waits

    def wait(self, _seconds: float) -> bool:
        self.waits += 1
        return self.is_set()


class RepositoryProgressRelayTests(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path, Path]:
        progress = root / "progress.txt"
        timeline = root / "timeline.json"
        log = root / "log.txt"
        state = root / "relay.json"
        progress.write_text("", encoding="utf-8")
        log.write_text("", encoding="utf-8")
        return progress, timeline, log, state

    def _timeline(
        self,
        path: Path,
        *,
        completed: dict[str, str] | None = None,
        active: str | None = None,
    ) -> None:
        completed = completed or {}
        last_completed = len(completed)
        active_value = None
        if active is not None:
            active_value = {
                "name": active,
                "ordinal": mod.FIXED_WRAPPERS.index(active) + 1,
                "started_unix_ns": 1,
                "started_monotonic_ns": 1,
            }
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "last_completed_ordinal": last_completed,
                    "completed": completed,
                    "active": active_value,
                }
            ),
            encoding="utf-8",
        )

    def test_free_form_progress_is_private_and_structured_plan_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "progress.txt"
            path.write_text(
                "private URL=https://example.invalid/token=secret\n"
                'CI_PROGRESS_V1 {"kind":"plan","steps":["dependencies","build","tests"]}\n'
                'CI_PROGRESS_V1 {"kind":"step","step":"dependencies","status":"running"}\n'
                'CI_PROGRESS_V1 {"kind":"step","step":"dependencies","status":"succeeded"}\n'
                'CI_PROGRESS_V1 {"kind":"step","step":"build","status":"succeeded"}\n'
                'CI_PROGRESS_V1 {"kind":"step","step":"tests","status":"running"}\n',
                encoding="utf-8",
            )
            parsed = mod.parse_product_progress(path)
            assert parsed is not None
            self.assertEqual(parsed.steps, ("dependencies", "build", "tests"))
            self.assertEqual(parsed.statuses, ("succeeded", "succeeded", "running"))
            self.assertNotIn("private", json.dumps(parsed.status_map()))
            self.assertNotIn("example.invalid", json.dumps(parsed.status_map()))

    def test_structured_progress_rejects_metadata_reserved_ids_and_order_conflicts(self) -> None:
        fixtures = (
            'CI_PROGRESS_V1 {"kind":"plan","steps":["source-admission"]}\n',
            'CI_PROGRESS_V1 {"kind":"plan","steps":["build"],"label":"secret"}\n',
            'CI_PROGRESS_V1 {"kind":"plan","steps":["build","tests"]}\n'
            'CI_PROGRESS_V1 {"kind":"step","step":"tests","status":"running"}\n',
            'CI_PROGRESS_V1 {"kind":"plan","steps":["build"]}\n'
            'CI_PROGRESS_V1 {"kind":"step","step":"build","status":"running","url":"https://private.invalid"}\n',
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                with tempfile.TemporaryDirectory() as td:
                    path = Path(td) / "progress.txt"
                    path.write_text(fixture, encoding="utf-8")
                    with self.assertRaises(mod.ProgressError):
                        mod.parse_product_progress(path)

    def test_projection_falls_back_to_fixed_central_wrapper_without_product_plan(self) -> None:
        timeline = mod.TimelineProgress(
            {"source-admission": "complete", "private-capabilities": "complete"},
            "repository-entrypoint",
        )
        projection = mod.compose_projection(timeline, None)
        self.assertEqual(
            projection["steps"],
            [
                {"id": "source-admission", "status": "succeeded"},
                {"id": "private-capabilities", "status": "succeeded"},
                {"id": "repository-entrypoint", "status": "running"},
                {"id": "evidence", "status": "pending"},
                {"id": "cleanup-settlement", "status": "pending"},
            ],
        )
        self.assertEqual(projection["completed_steps"], 2)
        self.assertEqual(projection["current_step_id"], "repository-entrypoint")

    def test_product_plan_is_inserted_after_entrypoint_gate_and_terminalized_safely(self) -> None:
        product = mod.ProductProgress(
            ("dependencies", "build", "tests"),
            ("succeeded", "running", "pending"),
        )
        running = mod.compose_projection(
            mod.TimelineProgress(
                {"source-admission": "complete", "private-capabilities": "complete"},
                "repository-entrypoint",
            ),
            product,
        )
        self.assertEqual(running["steps"][2], {"id": "repository-entrypoint", "status": "succeeded"})
        self.assertEqual(running["current_step_id"], "build")
        self.assertEqual(running["current_step_status"], "running")

        failed = mod.compose_projection(
            mod.TimelineProgress(
                {
                    "source-admission": "complete",
                    "private-capabilities": "complete",
                    "repository-entrypoint": "failed",
                },
                "evidence",
            ),
            product,
        )
        status = {item["id"]: item["status"] for item in failed["steps"]}
        self.assertEqual(status["dependencies"], "succeeded")
        self.assertEqual(status["build"], "failed")
        self.assertEqual(status["tests"], "skipped")
        self.assertEqual(status["evidence"], "running")
        self.assertEqual(failed["current_step_id"], "evidence")

    def test_entrypoint_failure_preserves_explicit_product_failure_and_skips_later_work(self) -> None:
        product = mod.ProductProgress(
            ("dependencies", "tests", "package"),
            ("succeeded", "failed", "pending"),
        )
        settled = mod._settle_product(product, "failed")
        self.assertEqual(
            settled.statuses,
            ("succeeded", "failed", "skipped"),
        )

    def test_failed_entrypoint_with_terminal_product_plan_surfaces_failed_settlement(self) -> None:
        product = mod.ProductProgress(
            ("dependencies", "build", "tests"),
            ("succeeded", "succeeded", "succeeded"),
        )
        projection = mod.compose_projection(
            mod.TimelineProgress(
                {
                    "source-admission": "complete",
                    "private-capabilities": "complete",
                    "repository-entrypoint": "failed",
                    "evidence": "complete",
                    "cleanup-settlement": "failed",
                },
                None,
            ),
            product,
        )
        statuses = {item["id"]: item["status"] for item in projection["steps"]}
        self.assertEqual(statuses["dependencies"], "succeeded")
        self.assertEqual(statuses["build"], "succeeded")
        self.assertEqual(statuses["tests"], "succeeded")
        self.assertEqual(statuses["cleanup-settlement"], "failed")
        self.assertEqual(projection["current_step_id"], "cleanup-settlement")
        self.assertEqual(projection["current_step_status"], "failed")

    def test_cleanup_settlement_uses_overall_ci_result_not_cleanup_alone(self) -> None:
        import yaml

        workflow = yaml.safe_load((ROOT / ".github/workflows/repository.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["execute"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        settlement = by_name["Finalize cleanup settlement timeline"]
        env = settlement["env"]
        for key in (
            "REQUEST_OUTCOME",
            "SOURCE_IDENTITY_OUTCOME",
            "EXECUTE_OUTCOME",
            "SCRUB_OUTCOME",
            "EVIDENCE_OUTCOME",
            "DRIVE_OUTCOME",
            "EVIDENCE_DRIVE_OUTCOME",
            "CLEANUP_OUTCOME",
        ):
            self.assertIn(key, env)
        script = settlement["run"]
        for name in (
            "REQUEST_OUTCOME",
            "SOURCE_IDENTITY_OUTCOME",
            "EXECUTE_OUTCOME",
            "SCRUB_OUTCOME",
            "EVIDENCE_OUTCOME",
            "DRIVE_OUTCOME",
            "EVIDENCE_DRIVE_OUTCOME",
            "CLEANUP_OUTCOME",
        ):
            self.assertIn(f'test "${{{name}}}" = success', script)

    def test_relay_reports_only_changes_then_final_flush(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            progress, timeline, log, state = self._paths(root)
            self._timeline(timeline, active="source-admission")
            reporter = FakeReporter()
            event = ScriptedEvent(stop_after_waits=2)
            result = mod.relay_loop(
                ci_run_id="00000000-0000-4000-8000-000000000001",
                progress_path=progress,
                timeline_state_path=timeline,
                log_path=log,
                state_path=state,
                reporter=reporter,
                stop_event=event,  # type: ignore[arg-type]
                poll_seconds=0.01,
                heartbeat_seconds=60,
                monotonic_fn=lambda: 1.0,
            )
            self.assertEqual(len(reporter.calls), 2)
            self.assertEqual([call[1]["revision"] for call in reporter.calls], [1, 2])
            self.assertEqual(result.revision, 2)

    def test_relay_emits_bounded_activity_heartbeat_only_after_actual_activity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            progress, timeline, log, state = self._paths(root)
            self._timeline(timeline, active="source-admission")
            reporter = FakeReporter()
            event = ScriptedEvent(stop_after_waits=2)
            with mock.patch.object(mod, "_activity_mtime_ns", side_effect=[1, 2, 2]), mock.patch.object(
                mod, "write_relay_state"
            ):
                times = iter((0.0, 61.0, 62.0))
                result = mod.relay_loop(
                    ci_run_id="00000000-0000-4000-8000-000000000001",
                    progress_path=progress,
                    timeline_state_path=timeline,
                    log_path=log,
                    state_path=state,
                    reporter=reporter,
                    stop_event=event,  # type: ignore[arg-type]
                    poll_seconds=0.01,
                    heartbeat_seconds=60,
                    monotonic_fn=lambda: next(times),
                )
            self.assertEqual(len(reporter.calls), 3)
            self.assertEqual([call[1]["revision"] for call in reporter.calls], [1, 2, 3])
            self.assertEqual(result.revision, 3)

    def test_relay_rpc_failure_is_warning_only_and_revision_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            progress, timeline, log, state = self._paths(root)
            self._timeline(timeline, active="source-admission")
            reporter = FakeReporter(fail_first=True)
            event = ScriptedEvent(stop_after_waits=1)
            result = mod.relay_loop(
                ci_run_id="00000000-0000-4000-8000-000000000001",
                progress_path=progress,
                timeline_state_path=timeline,
                log_path=log,
                state_path=state,
                reporter=reporter,
                stop_event=event,  # type: ignore[arg-type]
                poll_seconds=0.01,
                heartbeat_seconds=60,
                monotonic_fn=lambda: 1.0,
            )
            self.assertEqual([call[1]["revision"] for call in reporter.calls], [1, 1])
            self.assertEqual(result.revision, 1)

    def test_projection_contains_only_backend_safe_fields(self) -> None:
        projection = mod.compose_projection(
            mod.TimelineProgress({}, "source-admission"),
            None,
        )
        payload = {**projection, "revision": 1}
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "revision",
                "total_steps",
                "completed_steps",
                "current_step_id",
                "current_step_status",
                "steps",
            },
        )
        self.assertLess(len(json.dumps(payload).encode("utf-8")), 8192)
        rendered = json.dumps(payload)
        for forbidden in ("url", "path", "label", "selector", "credential", "device", "metadata", "timestamp"):
            self.assertNotIn(forbidden, rendered)


    def test_learned_product_plan_cannot_disappear_or_regress_between_snapshots(self) -> None:
        previous = mod.ProductProgress(("build", "tests"), ("succeeded", "running"))
        self.assertEqual(mod.preserve_monotonic_product(previous, None), previous)
        with self.assertRaises(mod.ProgressError):
            mod.preserve_monotonic_product(
                previous,
                mod.ProductProgress(("build", "tests"), ("pending", "pending")),
            )
        with self.assertRaises(mod.ProgressError):
            mod.preserve_monotonic_product(
                previous,
                mod.ProductProgress(("build", "package"), ("succeeded", "running")),
            )

    def test_workflow_relay_is_trusted_single_child_failure_soft_and_preterminal(self) -> None:
        import yaml

        workflow_path = ROOT / ".github/workflows/repository.yml"
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertNotIn("progress_ci_run_id", inputs)
        steps = workflow["jobs"]["execute"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        names = [step.get("name") for step in steps]

        capabilities = by_name["Resolve trusted private infrastructure capabilities"]["run"]
        self.assertIn("repository_lifecycle|repository_parent", capabilities)
        self.assertIn("progress_relay_allowed=true", capabilities)
        self.assertIn("progress_ci_run_id=%s", capabilities)

        owner = by_name["Resolve bounded progress relay owner"]
        owner_script = owner["run"]
        self.assertIn("repository_execution_plan.py", owner_script)
        self.assertIn("child_count", owner_script)
        self.assertIn('test "${child_count}" = 1', owner_script)
        self.assertIn('test "${selected_os}" = "${HOST_OS}"', owner_script)
        self.assertIn("trusted parent identity mismatch", owner_script)

        relay = by_name["Start bounded Agent State progress relay"]
        self.assertTrue(relay["continue-on-error"] )
        self.assertEqual(relay["if"], "${{ steps.progress_owner.outputs.ci_run_id != '' }}")
        relay_script = relay["run"]
        self.assertIn("env -i", relay_script)
        self.assertIn("repository_progress_relay.py watch", relay_script)
        self.assertIn("< <(printf '%s\\n'", relay_script)
        self.assertNotIn("--key", relay_script)
        self.assertNotIn("--url", relay_script)

        relay_finish = by_name["Finalize bounded Agent State progress relay"]
        self.assertTrue(relay_finish["continue-on-error"] )
        self.assertLess(names.index("Finalize cleanup settlement timeline"), names.index("Finalize bounded Agent State progress relay"))
        self.assertLess(names.index("Finalize bounded Agent State progress relay"), names.index("Finish Agent State run"))
        self.assertLess(names.index("Cleanup retained repository progress evidence"), names.index("Finish Agent State run"))

        finish = by_name["Finish Agent State run"]
        self.assertIn("steps.progress_cleanup.outcome == 'success'", finish["with"]["status"] )
        self.assertNotIn("progress_relay_finish", finish["with"]["status"] )

    def test_contract_documents_private_freeform_and_bounded_v1_projection(self) -> None:
        contract = json.loads((ROOT / "contracts/repository-ci-v1.json").read_text(encoding="utf-8"))
        protocol = contract["adoption"]["progressProtocol"]
        self.assertEqual(protocol["structuredPrefix"], "CI_PROGRESS_V1 ")
        self.assertEqual(protocol["schemaVersion"], 1)
        self.assertEqual(protocol["maximumProductSteps"], 32)
        self.assertTrue(protocol["freeFormLinesRemainPrivate"])
        self.assertFalse(protocol["productCallsAgentStateDirectly"])
        self.assertTrue(protocol["centralOwnsAgentStateRelay"])
        self.assertFalse(protocol["multiOsChildrenRaceOneProgressProjection"])
        self.assertFalse(protocol["progressReportingAffectsCiResult"])


if __name__ == "__main__":
    unittest.main()
