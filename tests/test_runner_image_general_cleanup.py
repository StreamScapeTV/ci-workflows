from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/general/Dockerfile"
SMOKE = ROOT / "runner-images/general/smoke.sh"


def test_general_smoke_isolates_npm_probe_from_supplied_cache() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    smoke = SMOKE.read_text(encoding="utf-8")

    # Both image-construction and finished-image validation run this smoke. Keep
    # the npm probe inside the smoke-owned temp root so an externally supplied
    # NPM_CONFIG_CACHE is neither used nor destructively cleaned.
    assert 'NPM_CONFIG_CACHE="${venv_root}/npm-cache" npm --version >/dev/null' in smoke
    assert "export NPM_CONFIG_CACHE" not in smoke
    assert "npm cache clean" not in smoke
    assert 'rm -rf "${NPM_CONFIG_CACHE}"' not in smoke
    assert "/home/runner/_work/.npm-cache" not in source
    assert "ENV NPM_CONFIG_CACHE" not in source


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
