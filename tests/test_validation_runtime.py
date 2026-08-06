from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ci_workflows.validation_runtime import (
    ValidationRuntimeError,
    install_locked_wheel,
    select_wheel,
)

RUNTIME = "cp312-manylinux-x86_64"
FILENAME = (
    "pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64."
    "manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"
)
URL = f"https://files.pythonhosted.org/packages/example/{FILENAME}"


def wheel_bytes(*, unsafe: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("yaml/__init__.py", '__version__ = "6.0.3"\n')
        archive.writestr(
            "PyYAML-6.0.3.dist-info/METADATA",
            "Name: PyYAML\nVersion: 6.0.3\n",
        )
        if unsafe:
            archive.writestr("../escape.py", "raise SystemExit\n")
    return buffer.getvalue()


def write_lock(path: Path, payload: bytes, *, digest: str | None = None) -> None:
    value = {
        "python": {
            "packages": [
                {
                    "name": "PyYAML",
                    "version": "6.0.3",
                    "wheels": [
                        {
                            "runtime": RUNTIME,
                            "filename": FILENAME,
                            "url": URL,
                            "sha256": digest or hashlib.sha256(payload).hexdigest(),
                        }
                    ],
                }
            ]
        }
    }
    path.write_text(json.dumps(value), encoding="utf-8")


class ValidationRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = self.root / "lock.json"
        self.target = self.root / "python"

    def test_selects_exact_runtime_wheel(self) -> None:
        payload = wheel_bytes()
        write_lock(self.lock, payload)
        artifact = select_wheel(self.lock, RUNTIME)
        self.assertEqual(RUNTIME, artifact.runtime)
        self.assertEqual(FILENAME, artifact.filename)

    def test_installs_verified_wheel_without_pip(self) -> None:
        payload = wheel_bytes()
        write_lock(self.lock, payload)
        artifact = install_locked_wheel(
            self.lock,
            self.target,
            runtime=RUNTIME,
            downloader=lambda url: payload,
        )
        self.assertEqual("PyYAML", artifact.package)
        self.assertTrue((self.target / "yaml/__init__.py").is_file())
        self.assertFalse(any(self.target.rglob("*.pyc")))

    def test_rejects_digest_mismatch(self) -> None:
        payload = wheel_bytes()
        write_lock(self.lock, payload, digest="0" * 64)
        with self.assertRaisesRegex(ValidationRuntimeError, "digest mismatch"):
            install_locked_wheel(
                self.lock,
                self.target,
                runtime=RUNTIME,
                downloader=lambda url: payload,
            )

    def test_rejects_unsafe_archive_member(self) -> None:
        payload = wheel_bytes(unsafe=True)
        write_lock(self.lock, payload)
        with self.assertRaisesRegex(ValidationRuntimeError, "unsafe wheel member"):
            install_locked_wheel(
                self.lock,
                self.target,
                runtime=RUNTIME,
                downloader=lambda url: payload,
            )
        self.assertFalse((self.root / "escape.py").exists())

    def test_rejects_non_official_download_host(self) -> None:
        payload = wheel_bytes()
        write_lock(self.lock, payload)
        value = json.loads(self.lock.read_text())
        value["python"]["packages"][0]["wheels"][0]["url"] = (
            f"https://example.invalid/{FILENAME}"
        )
        self.lock.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValidationRuntimeError, "files.pythonhosted.org"):
            select_wheel(self.lock, RUNTIME)


if __name__ == "__main__":
    unittest.main()
