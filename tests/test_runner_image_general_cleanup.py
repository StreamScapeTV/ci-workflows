from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/general/Dockerfile"
SMOKE = ROOT / "runner-images/general/smoke.sh"


def test_general_runner_uses_image_owned_npm_cache_during_smoke() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    smoke = SMOKE.read_text(encoding="utf-8")

    assert "NPM_CONFIG_CACHE=/home/runner/_work/.npm-cache" not in source
    assert '"${NPM_CONFIG_CACHE}"' not in source
    assert "/home/runner/_work/.npm-cache" not in smoke
    for token in (
        "export NPM_CONFIG_CACHE=/home/runner/.npm",
        'test "${NPM_CONFIG_CACHE}" = /home/runner/.npm',
        'mkdir -p "${NPM_CONFIG_CACHE}"',
        'test ! -L "${NPM_CONFIG_CACHE}"',
        'test -w "${NPM_CONFIG_CACHE}"',
    ):
        assert token in smoke


def test_general_runner_removes_only_image_owned_build_phase_state() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    smoke_index = source.index("RUN /usr/local/bin/runner-image-smoke")
    cleanup_user_index = source.index("USER root", smoke_index)
    cleanup_index = source.index(
        "RUN rm -rf /home/runner/.npm /home/runner/.cache;",
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
    assert "/home/runner/_work/.npm-cache" not in source
    assert "sudo chown" not in source
