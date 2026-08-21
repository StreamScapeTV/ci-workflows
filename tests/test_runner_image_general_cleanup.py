from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/general/Dockerfile"
SMOKE = ROOT / "runner-images/general/smoke.sh"


def test_general_runner_uses_writable_work_state_for_npm_cache() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    smoke = SMOKE.read_text(encoding="utf-8")

    assert "NPM_CONFIG_CACHE=/home/runner/_work/.npm-cache" in source
    for token in (
        'test "${NPM_CONFIG_CACHE}" = /home/runner/_work/.npm-cache',
        "test -w /home/runner/_work",
        'mkdir -p "${NPM_CONFIG_CACHE}"',
        'test -w "${NPM_CONFIG_CACHE}"',
    ):
        assert token in smoke


def test_general_runner_removes_build_phase_npm_state_before_final_image() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    smoke_index = source.index("RUN /usr/local/bin/runner-image-smoke")
    cleanup_user_index = source.index("USER root", smoke_index)
    cleanup_index = source.index(
        'RUN rm -rf /home/runner/.npm /home/runner/.cache "${NPM_CONFIG_CACHE}";',
        cleanup_user_index,
    )
    ownership_index = source.index(
        "chown -R 1001:1001 /home/runner",
        cleanup_index,
    )
    final_runner_index = source.rindex("USER 1001:1001")

    assert smoke_index < cleanup_user_index < cleanup_index < ownership_index < final_runner_index
    assert source.rstrip().endswith("USER 1001:1001\nCMD []")
    assert source.count("RUN /usr/local/bin/runner-image-smoke") == 1
    assert "sudo chown" not in source
