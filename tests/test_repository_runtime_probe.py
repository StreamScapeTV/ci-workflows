from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/repository_runtime_probe.py"
SPEC = importlib.util.spec_from_file_location("repository_runtime_probe", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class FakeRunner:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, argv):
        key = tuple(argv)
        self.calls.append(key)
        value = self.mapping.get(key)
        if value is None:
            return mod.CommandResult(1, "", "missing")
        return value


class RepositoryRuntimeProbeTests(unittest.TestCase):
    def test_collect_sample_records_only_bounded_runtime_identity(self) -> None:
        udid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        runner = FakeRunner(
            {
                ("ps", "-axo", "pid=,ppid=,state=,etime=,comm="): mod.CommandResult(
                    0,
                    "10 1 S 00:10 /opt/flutter/bin/cache/dart-sdk/bin/dart\n"
                    "20 10 S 00:05 /usr/bin/xcodebuild\n"
                    "30 1 S 00:02 /Users/runner/Library/Developer/CoreSimulator/Devices/"
                    + udid
                    + "/data/Containers/Bundle/Application/X/App.app/App\n"
                    "40 1 S 00:01 /bin/bash\n",
                ),
                ("xcrun", "simctl", "list", "devices", "booted", "-j"): mod.CommandResult(
                    0,
                    json.dumps(
                        {
                            "devices": {
                                "runtime": [
                                    {"name": "iPhone 17", "udid": udid, "state": "Booted"}
                                ]
                            }
                        }
                    ),
                ),
                ("xcrun", "simctl", "listapps", udid): mod.CommandResult(
                    0,
                    '{\n    "com.apple.Preferences" = {\n    };\n'
                    '    "dev.example.app" = {\n'
                    '        CFBundleExecutable = App;\n'
                    '    };\n}\n',
                ),
                (
                    "xcrun",
                    "simctl",
                    "spawn",
                    udid,
                    "ps",
                    "-axo",
                    "pid=,ppid=,state=,etime=,comm=",
                ): mod.CommandResult(
                    0,
                    "30 1 S 00:02 /private/var/containers/Bundle/Application/X/App.app/App\n"
                    "31 1 S 00:01 SpringBoard\n",
                ),
                mod._vm_service_log_command(udid, 30): mod.CommandResult(
                    0,
                    "2026-09-29 App[30] flutter: The Dart VM service is listening on "
                    "http://127.0.0.1:53123/SECRET-AUTH-CODE=/\n",
                ),
                ("lsof", "-nP", "-iTCP", "-sTCP:LISTEN"): mod.CommandResult(
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
                    "App 30 runner 9u IPv4 0 0t0 TCP 127.0.0.1:53123 (LISTEN)\n"
                    "bash 40 runner 9u IPv4 0 0t0 TCP 127.0.0.1:9999 (LISTEN)\n",
                ),
            }
        )
        sample = mod.collect_sample(sequence=7, runner=runner, observed_at="2026-09-29T12:00:00Z")
        self.assertEqual(sample["sequence"], 7)
        self.assertEqual(sample["tool_processes"], ["dart", "xcodebuild"])
        self.assertEqual(sample["simulator_app_processes"], ["App"])
        self.assertEqual(sample["third_party_apps"][udid], ["dev.example.app"])
        self.assertEqual(sample["listeners"], [{"pid": 30, "comm": "App", "endpoint": "127.0.0.1:53123"}])
        self.assertTrue(sample["vm_service_log_inventory_observed"])
        self.assertEqual(sample["vm_service_publication_ports"], [53123])
        self.assertTrue(sample["vm_service_listener_match"])
        encoded = mod._encode_sample(sample)
        self.assertNotIn(b"--dart-define", encoded)
        self.assertNotIn(b"SECRET-AUTH-CODE", encoded)
        self.assertLessEqual(len(encoded), mod.MAX_LINE_BYTES)

    def test_summary_reports_observed_launch_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "probe.txt"
            path.write_text("", encoding="utf-8")
            samples = [
                {
                    "schema_version": 1,
                    "sequence": 1,
                    "observed_at": "2026-09-29T12:00:00Z",
                    "host_os": "macos",
                    "simulators": [],
                    "app_inventory_observed": False,
                    "third_party_apps": {},
                    "tool_processes": ["flutter", "xcodebuild"],
                    "simulator_app_processes": [],
                    "listener_inventory_observed": True,
                    "listeners": [],
                    "errors": [],
                },
                {
                    "schema_version": 1,
                    "sequence": 2,
                    "observed_at": "2026-09-29T12:01:00Z",
                    "host_os": "macos",
                    "simulators": [{"name": "iPhone", "udid": "U"}],
                    "app_inventory_observed": True,
                    "third_party_apps": {"U": ["dev.example.app"]},
                    "tool_processes": ["flutter"],
                    "simulator_app_processes": ["App"],
                    "listener_inventory_observed": True,
                    "listeners": [{"pid": 30, "comm": "App", "endpoint": "127.0.0.1:53000"}],
                    "vm_service_log_inventory_observed": True,
                    "vm_service_publication_ports": [53000],
                    "vm_service_listener_match": True,
                    "errors": [],
                },
            ]
            for sample in samples:
                mod.append_sample(path, sample)
            lines = mod.summary_lines(path)
            self.assertIn("simulator_booted=1", lines[0])
            self.assertIn("third_party_app_seen=1", lines[0])
            self.assertIn("simulator_app_process_seen=1", lines[0])
            self.assertIn("candidate_listener_seen=1", lines[0])
            self.assertIn("xcodebuild_seen=1", lines[0])
            self.assertIn("vm_service_log_inventory_observed=1", lines[0])
            self.assertIn("vm_service_publication_seen=1", lines[0])
            self.assertIn("vm_service_listener_match=1", lines[0])

    def test_collect_sample_distinguishes_listener_without_vm_service_publication(self) -> None:
        udid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        runner = FakeRunner(
            {
                ("ps", "-axo", "pid=,ppid=,state=,etime=,comm="): mod.CommandResult(
                    0,
                    "10 1 S 00:10 /opt/flutter/bin/flutter\n",
                ),
                ("xcrun", "simctl", "list", "devices", "booted", "-j"): mod.CommandResult(
                    0,
                    json.dumps(
                        {
                            "devices": {
                                "runtime": [
                                    {"name": "iPhone 17", "udid": udid, "state": "Booted"}
                                ]
                            }
                        }
                    ),
                ),
                ("xcrun", "simctl", "listapps", udid): mod.CommandResult(
                    0,
                    '{\n    "dev.example.app" = {\n'
                    '        CFBundleExecutable = App;\n'
                    '    };\n}\n',
                ),
                (
                    "xcrun",
                    "simctl",
                    "spawn",
                    udid,
                    "ps",
                    "-axo",
                    "pid=,ppid=,state=,etime=,comm=",
                ): mod.CommandResult(
                    0,
                    "30 1 S 00:02 /private/var/containers/Bundle/Application/X/App.app/App\n",
                ),
                mod._vm_service_log_command(udid, 30): mod.CommandResult(
                    0,
                    "2026-09-29 App[30] flutter: first frame rendered\n",
                ),
                ("lsof", "-nP", "-iTCP", "-sTCP:LISTEN"): mod.CommandResult(
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
                    "App 30 runner 9u IPv4 0 0t0 TCP 127.0.0.1:53123 (LISTEN)\n",
                ),
            }
        )
        sample = mod.collect_sample(sequence=1, runner=runner)
        self.assertTrue(sample["vm_service_log_inventory_observed"])
        self.assertEqual(sample["vm_service_publication_ports"], [])
        self.assertFalse(sample["vm_service_listener_match"])
        self.assertEqual(sample["listeners"][0]["endpoint"], "127.0.0.1:53123")

    def test_append_summary_keeps_probe_out_of_process_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            probe = root / "probe.txt"
            log = root / "log.txt"
            probe.write_text("", encoding="utf-8")
            log.write_text("prefix\n", encoding="utf-8")
            mod.append_sample(
                probe,
                {
                    "schema_version": 1,
                    "sequence": 1,
                    "observed_at": "2026-09-29T12:00:00Z",
                    "host_os": "macos",
                    "simulators": [],
                    "app_inventory_observed": False,
                    "third_party_apps": {},
                    "tool_processes": ["flutter"],
                    "simulator_app_processes": [],
                    "listener_inventory_observed": False,
                    "listeners": [],
                    "errors": ["listener_inventory_unavailable"],
                },
            )
            mod.append_summary(probe, log)
            value = log.read_text(encoding="utf-8")
            self.assertIn("CENTRAL runtime-probe summary", value)
            self.assertIn("listener_inventory_observed=0", value)

    def test_probe_output_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "probe.txt"
            path.write_bytes(b"x" * mod.MAX_PROBE_BYTES)
            with self.assertRaises(mod.ProbeError):
                mod.append_sample(path, {"schema_version": 1})


if __name__ == "__main__":
    unittest.main()
