from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import flutter_execution
from ci_workflows.flutter_execution import terminal_cleanup_flutter_state


class FlutterCleanupPortabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp = Path(self.temporary.name)

    def test_procfs_discovery_keeps_gradle_daemons_scoped_to_registered_state(self) -> None:
        state = self.temp / "state/flutter-validation"
        state.mkdir(parents=True)
        proc_root = self.temp / "proc"
        matching = f"java\0GradleDaemon\0--gradle-user-home\0{state / 'gradle-home'}\0"
        other = (
            "java\0GradleDaemon\0--gradle-user-home\0"
            f"{self.temp / 'other/flutter-validation/gradle-home'}\0"
        )
        for pid, command in (("123", matching), ("456", other)):
            process = proc_root / pid
            process.mkdir(parents=True)
            (process / "cmdline").write_bytes(command.encode("utf-8"))
        (proc_root / "not-a-pid").mkdir()

        self.assertEqual(
            (123,),
            flutter_execution._procfs_state_gradle_daemon_pids(state, proc_root),
        )

    def test_cleanup_uses_procfs_fallback_when_ps_is_unavailable(self) -> None:
        source = self.temp / "source"
        source.mkdir()
        state_root = self.temp / "state"
        flutter_state = state_root / "flutter-validation"
        pub_cache = flutter_state / "pub-cache"
        pub_cache.mkdir(parents=True)
        (pub_cache / "generated").write_text("generated\n", encoding="utf-8")

        with mock.patch.object(
            flutter_execution.subprocess,
            "run",
            side_effect=FileNotFoundError("ps"),
        ), mock.patch.object(
            flutter_execution,
            "_procfs_state_gradle_daemon_pids",
            return_value=(),
        ) as procfs:
            result = terminal_cleanup_flutter_state(source, state_root)

        self.assertEqual("success", result["cleanup_result"])
        procfs.assert_called_once_with(flutter_state)
        self.assertFalse(flutter_state.exists())

    def test_ps_unavailable_cleanup_preserves_primary_failure(self) -> None:
        source = self.temp / "source-not-created"
        state_root = self.temp / "state"
        flutter_state = state_root / "flutter-validation"
        flutter_state.mkdir(parents=True)

        with mock.patch.object(
            flutter_execution.subprocess,
            "run",
            side_effect=FileNotFoundError("ps"),
        ), mock.patch.object(
            flutter_execution,
            "_procfs_state_gradle_daemon_pids",
            return_value=(),
        ):
            result = terminal_cleanup_flutter_state(
                source,
                state_root,
                primary_failure_code="command_failed",
            )

        self.assertEqual(
            {
                "result": "failure",
                "failure_code": "command_failed",
                "primary_failure_code": "command_failed",
                "cleanup_failure_code": "",
                "cleanup_result": "success",
            },
            result,
        )
        self.assertFalse(flutter_state.exists())


if __name__ == "__main__":
    unittest.main()
