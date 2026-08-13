from pathlib import Path

from ci_workflows.python_execution import podman_command


def test_runtime_state_command_avoids_runroot_override() -> None:
    result = podman_command(Path("/workspace/validation-state"))
    assert result == [
        "podman",
        "--storage-driver",
        "vfs",
        "--root",
        "/workspace/validation-state/python-validation/podman-storage",
    ]
