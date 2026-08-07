from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

LINUX_RUNTIME = "cp312-manylinux-x86_64"
MAC_ARM_RUNTIME = "cp313-macos-arm64"
MAC_X64_RUNTIME = "cp313-macos-x86_64"
WHEEL_FILENAME = (
    "pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64."
    "manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"
)
WHEEL_URL = f"https://files.pythonhosted.org/packages/example/{WHEEL_FILENAME}"
SOURCE_FILENAME = "pyyaml-6.0.3.tar.gz"
SOURCE_URL = f"https://files.pythonhosted.org/packages/example/{SOURCE_FILENAME}"


def regular_member(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.size = len(data)
    member.mode = 0o644
    return member, data


def typed_member(
    name: str,
    member_type: bytes,
    *,
    linkname: str = "",
) -> tuple[tarfile.TarInfo, bytes | None]:
    member = tarfile.TarInfo(name)
    member.type = member_type
    member.linkname = linkname
    member.mode = 0o644
    return member, None


def source_bytes(
    *,
    root_version: str = "6.0.3",
    metadata_version: str | None = None,
    include_yaml: bool = True,
    extra_members: list[tuple[tarfile.TarInfo, bytes | None]] | None = None,
) -> bytes:
    root = f"pyyaml-{root_version}"
    metadata_version = metadata_version or root_version
    members: list[tuple[tarfile.TarInfo, bytes | None]] = [
        regular_member(
            f"{root}/PKG-INFO",
            (
                "Metadata-Version: 2.4\n"
                "Name: PyYAML\n"
                f"Version: {metadata_version}\n"
            ).encode(),
        ),
        regular_member(
            f"{root}/setup.py",
            b"raise SystemExit('must not install')\n",
        ),
    ]
    if include_yaml:
        members.extend(
            [
                regular_member(
                    f"{root}/lib/yaml/__init__.py",
                    f'__version__ = "{root_version}"\n'.encode(),
                ),
                regular_member(
                    f"{root}/lib/yaml/loader.py",
                    b"class Loader: pass\n",
                ),
            ]
        )
    members.extend(extra_members or [])
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for member, data in members:
            archive.addfile(
                member,
                io.BytesIO(data) if data is not None else None,
            )
    return buffer.getvalue()


def write_lock(
    path: Path,
    *,
    wheel_payload: bytes,
    source_payload: bytes,
    wheel_digest: str | None = None,
    source_digest: str | None = None,
    package_version: str = "6.0.3",
    source_filename: str = SOURCE_FILENAME,
    source_url: str = SOURCE_URL,
) -> None:
    source_hash = source_digest or hashlib.sha256(source_payload).hexdigest()
    value = {
        "python": {
            "packages": [
                {
                    "name": "PyYAML",
                    "version": package_version,
                    "sha256": source_hash,
                    "source": {
                        "format": "sdist-tar-gz",
                        "filename": source_filename,
                        "url": source_url,
                        "sha256": source_hash,
                        "runtimes": [MAC_ARM_RUNTIME, MAC_X64_RUNTIME],
                    },
                    "wheels": [
                        {
                            "runtime": LINUX_RUNTIME,
                            "filename": WHEEL_FILENAME,
                            "url": WHEEL_URL,
                            "sha256": (
                                wheel_digest
                                or hashlib.sha256(wheel_payload).hexdigest()
                            ),
                        }
                    ],
                }
            ]
        }
    }
    path.write_text(json.dumps(value), encoding="utf-8")
