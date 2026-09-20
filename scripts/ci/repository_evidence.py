#!/usr/bin/env python3
"""Shared scrub/package mechanics for generic repository CI and its fixed acceptance canary."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import zipfile

TEXT_FILE_MAX = 16 * 1024 * 1024
ARTIFACT_FILE_MAX = 16 * 1024 * 1024
TOTAL_MAX = 64 * 1024 * 1024
ENTRY_MAX = 256
SYMLINK_TARGET_MAX = 4096


def _paths() -> tuple[Path, Path, Path, Path]:
    return (
        Path(os.environ["CI_LOG"]),
        Path(os.environ["CI_PROGRESS_FILE"]),
        Path(os.environ["CI_LOG_DIR"]),
        Path(os.environ["CI_ARTIFACT_DIR"]),
    )


def _configured_secrets() -> list[bytes]:
    return [
        value.encode()
        for name, value in os.environ.items()
        if name.startswith("CI_SECRET_") and value
    ]


def scrub() -> None:
    ci_log, progress, log_root, artifact_root = _paths()
    paths = [ci_log, progress]
    for name, root in (("CI_LOG_DIR", log_root), ("CI_ARTIFACT_DIR", artifact_root)):
        if root.is_symlink() or not root.is_dir():
            raise SystemExit(f"repository CI {name} must remain one real directory before scrub")
    resolved_log_root = log_root.resolve(strict=True)
    for path in sorted(log_root.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"repository CI text evidence must not contain symlinks: {path.name}")
        if not path.is_file():
            continue
        resolved = path.resolve(strict=True)
        if os.path.commonpath((str(resolved), str(resolved_log_root))) != str(resolved_log_root):
            raise SystemExit(f"repository CI text evidence escapes its Central-owned root: {path.name}")
        paths.append(path)

    secrets = _configured_secrets()
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"repository CI text evidence must remain one regular non-symlink file: {path.name}")
        if path.stat().st_size > TEXT_FILE_MAX:
            raise SystemExit(f"repository CI text evidence file exceeds 16 MiB before scrub: {path.name}")
        data = path.read_bytes()
        for raw in secrets:
            data = data.replace(raw, b"[REDACTED]")
            escaped = raw.replace(b"\n", b"\\n")
            if escaped != raw:
                data = data.replace(escaped, b"[REDACTED]")
        path.write_bytes(data)

    resolved_artifact_root = artifact_root.resolve(strict=True)
    for path in sorted(artifact_root.rglob("*")):
        if path.is_symlink():
            data = os.fsencode(os.readlink(path))
        elif path.is_file():
            resolved = path.resolve(strict=True)
            if os.path.commonpath((str(resolved), str(resolved_artifact_root))) != str(resolved_artifact_root):
                raise SystemExit(f"repository CI artifact evidence escapes its Central-owned root: {path.name}")
            if path.stat().st_size > ARTIFACT_FILE_MAX:
                continue
            data = path.read_bytes()
        else:
            continue
        for raw in secrets:
            if raw in data:
                raise SystemExit(f"repository CI artifact evidence contains configured secret bytes: {path.name}")
            escaped = raw.replace(b"\n", b"\\n")
            if escaped != raw and escaped in data:
                raise SystemExit(f"repository CI artifact evidence contains escaped configured secret bytes: {path.name}")


def package(archive: Path) -> None:
    progress = Path(os.environ["CI_PROGRESS_FILE"])
    log_root = Path(os.environ["CI_LOG_DIR"])
    artifact_root = Path(os.environ["CI_ARTIFACT_DIR"])
    roots = (("logs", log_root), ("artifacts", artifact_root))
    entries: list[tuple[str, Path, bytes | None]] = []
    total = 0
    for prefix, root in roots:
        if root.is_symlink() or not root.is_dir():
            raise SystemExit(f"repository CI {prefix} root must remain one real directory")
        resolved_root = root.resolve(strict=True)
        for path in sorted(root.rglob("*")):
            archive_name = f"{prefix}/{path.relative_to(root).as_posix()}"
            if path.is_symlink():
                if prefix != "artifacts":
                    raise SystemExit(f"repository CI {prefix} evidence must not contain symlinks")
                raw_target = os.readlink(path)
                target_bytes = os.fsencode(raw_target)
                if not target_bytes or len(target_bytes) > SYMLINK_TARGET_MAX:
                    raise SystemExit("repository CI artifact evidence symlink target is outside the reviewed bound")
                lexical_target = Path(raw_target)
                if not lexical_target.is_absolute():
                    lexical_target = path.parent / lexical_target
                lexical_target = Path(os.path.abspath(lexical_target))
                if os.path.commonpath((str(lexical_target), str(resolved_root))) != str(resolved_root):
                    raise SystemExit("repository CI artifact evidence symlink escapes its Central-owned root")
                resolved_target = path.resolve(strict=False)
                if os.path.commonpath((str(resolved_target), str(resolved_root))) != str(resolved_root):
                    raise SystemExit("repository CI artifact evidence symlink escapes its Central-owned root")
                total += len(target_bytes)
                entries.append((archive_name, path, target_bytes))
                continue
            if not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if os.path.commonpath((str(resolved), str(resolved_root))) != str(resolved_root):
                raise SystemExit(f"repository CI {prefix} evidence escapes its Central-owned root")
            size = path.stat().st_size
            if size > ARTIFACT_FILE_MAX:
                if prefix == "artifacts":
                    continue
                raise SystemExit(f"repository CI {prefix} evidence file exceeds 16 MiB")
            total += size
            entries.append((archive_name, path, None))

    if progress.is_symlink() or not progress.is_file():
        raise SystemExit("repository CI progress evidence must remain one regular file")
    total += progress.stat().st_size
    entries.append(("progress.txt", progress, None))
    if len(entries) > ENTRY_MAX:
        raise SystemExit("repository CI evidence exceeds 256 files")
    if total > TOTAL_MAX:
        raise SystemExit("repository CI evidence exceeds 64 MiB")

    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name, path, symlink_target in entries:
            if symlink_target is None:
                output.write(path, arcname=name)
                continue
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            info.compress_type = zipfile.ZIP_STORED
            output.writestr(info, symlink_target)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scrub")
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "scrub":
        scrub()
    else:
        package(args.archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
