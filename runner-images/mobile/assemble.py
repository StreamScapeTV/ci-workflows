#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import shutil
import stat
import tarfile
import zipfile

ANDROID = Path("/opt/android-sdk")


def _safe_symlink_target(output: Path, target_root: Path, raw_target: bytes) -> str:
    try:
        link_target = raw_target.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SystemExit(f"invalid symlink target encoding: {output}") from error
    if not link_target or "\x00" in link_target:
        raise SystemExit(f"invalid symlink target: {output}")
    relative_target = Path(link_target)
    if relative_target.is_absolute():
        raise SystemExit(f"unsafe archive symlink: {output} -> {link_target}")
    resolved_target = (output.parent / relative_target).resolve()
    if resolved_target != target_root and target_root not in resolved_target.parents:
        raise SystemExit(f"unsafe archive symlink: {output} -> {link_target}")
    return link_target


def extract_zip(source: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve()
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            relative = Path(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit(f"unsafe archive member: {member.filename}")
            output = target / relative
            resolved_output = output.resolve()
            if resolved_output != target_root and target_root not in resolved_output.parents:
                raise SystemExit(f"unsafe archive member: {member.filename}")
            if member.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue

            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type == stat.S_IFLNK:
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists() or output.is_symlink():
                    raise SystemExit(f"duplicate archive member: {member.filename}")
                link_target = _safe_symlink_target(
                    output,
                    target_root,
                    archive.read(member),
                )
                output.symlink_to(link_target)
                continue
            if file_type not in (0, stat.S_IFREG):
                raise SystemExit(f"unsupported archive member type: {member.filename}")

            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, output.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            permissions = mode & 0o777
            if permissions:
                output.chmod(permissions)
    return target


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"missing directory: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)


def main() -> int:
    ANDROID.mkdir(parents=True, exist_ok=True)
    with tarfile.open("/tmp/flutter.tar.xz", "r:xz") as archive:
        archive.extractall("/opt", filter="data")

    cmdline = extract_zip(Path("/tmp/android-command-line-tools.zip"), Path("/tmp/cmdline"))
    copy_tree(cmdline / "cmdline-tools", ANDROID / "cmdline-tools" / "latest")

    platform_tools = extract_zip(Path("/tmp/android-platform-tools.zip"), Path("/tmp/platform-tools"))
    copy_tree(platform_tools / "platform-tools", ANDROID / "platform-tools")

    for source, api in (
        (Path("/tmp/android-platform-36.zip"), "36"),
        (Path("/tmp/android-platform-37.zip"), "37.0"),
    ):
        unpacked = extract_zip(source, Path(f"/tmp/platform-{api}"))
        jars = list(unpacked.rglob("android.jar"))
        if len(jars) != 1:
            raise SystemExit(f"expected one android.jar for platform {api}, got {len(jars)}")
        copy_tree(jars[0].parent, ANDROID / "platforms" / f"android-{api}")

    for source, version in (
        (Path("/tmp/android-build-tools-36.zip"), "36.0.0"),
        (Path("/tmp/android-build-tools-37.zip"), "37.0.0"),
    ):
        unpacked = extract_zip(source, Path(f"/tmp/build-tools-{version}"))
        candidates = [path.parent for path in unpacked.rglob("aapt2") if path.is_file()]
        if len(candidates) != 1:
            raise SystemExit(
                f"expected one build-tools directory for {version}, got {len(candidates)}"
            )
        copy_tree(candidates[0], ANDROID / "build-tools" / version)

    ndk = extract_zip(Path("/tmp/android-ndk.zip"), Path("/tmp/ndk"))
    roots = [path for path in ndk.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise SystemExit(f"expected one NDK root, got {len(roots)}")
    copy_tree(roots[0], ANDROID / "ndk" / "28.2.13676358")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
