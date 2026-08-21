from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/general/Dockerfile"


def test_general_runner_removes_build_phase_npm_state_before_final_image() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    smoke_index = source.index("RUN /usr/local/bin/runner-image-smoke")
    cleanup_block = (
        "USER root\n"
        "RUN rm -rf /home/runner/.npm /home/runner/.cache; \\\n"
        "    chown -R 1001:1001 /home/runner"
    )
    cleanup_index = source.index(cleanup_block)
    final_runner_index = source.rindex("USER 1001:1001")

    assert smoke_index < cleanup_index < final_runner_index
    assert source.rstrip().endswith("USER 1001:1001\nCMD []")
    assert source.count("RUN /usr/local/bin/runner-image-smoke") == 1
    assert "sudo chown" not in source
