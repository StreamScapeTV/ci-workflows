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


def test_general_runner_materializes_compiler_frontends_for_final_smoke() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    # The package-copy pass preserves Debian's compiler symlinks. The final
    # Python image does not carry the intermediate version alias in that chain,
    # so materialize the three compiler frontends as real executable files.
    assert "for executable in /usr/bin/gcc /usr/bin/g++ /usr/bin/cpp; do" in source
    assert 'native_entrypoint="/native-root${executable}"' in source
    assert 'test -e "${native_entrypoint}" || test -L "${native_entrypoint}"' in source
    assert 'rm -f "${native_entrypoint}"' in source
    assert 'cp -pL "${executable}" "${native_entrypoint}"' in source
    assert source.count('test -x "${native_entrypoint}"') == 1


def test_general_runner_collects_gcc_helper_runtime_dependencies() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    # cc1/cc1plus and related GCC helpers live under libexec and have their own
    # shared-library dependencies. Scan those helpers generically rather than
    # pinning individual transitive libraries such as libisl into the image.
    assert "find /usr/lib/gcc/x86_64-linux-gnu/14 -type f -perm /111 -print0" in source
    assert "find /usr/libexec/gcc/x86_64-linux-gnu/14 -type f -perm /111 -print0" in source
    assert "libisl.so" not in source


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
