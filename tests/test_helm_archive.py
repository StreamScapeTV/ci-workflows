from __future__ import annotations

import gzip
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_archive import (
    canonicalize_chart_archive,
    canonicalize_chart_archive_bytes,
    finalize_validation_archive,
)
from ci_workflows.helm_types import HelmValidationError, HelmValidationResult


def chart_bytes(
    root_name: str,
    files: dict[str, bytes],
    *,
    gzip_mtime: int,
    tar_mtime: int,
    uid: int = 1000,
) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=gzip_mtime, filename=f"unstable-{gzip_mtime}") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            directory = tarfile.TarInfo(root_name + "/")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o775
            directory.mtime = tar_mtime
            directory.uid = uid
            directory.gid = uid
            archive.addfile(directory)
            for relative, content in files.items():
                info = tarfile.TarInfo(f"{root_name}/{relative}")
                info.mode = 0o664
                info.mtime = tar_mtime
                info.uid = uid
                info.gid = uid
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return raw.getvalue()


def outer_with_dependency(dependency: bytes, *, gzip_mtime: int, tar_mtime: int) -> bytes:
    return chart_bytes(
        "backend",
        {
            "Chart.yaml": b"apiVersion: v2\nname: backend\nversion: 1.2.3\n",
            "values.yaml": b"replicas: 1\n",
            "charts/dep-1.0.0.tgz": dependency,
        },
        gzip_mtime=gzip_mtime,
        tar_mtime=tar_mtime,
    )


class HelmArchiveTests(unittest.TestCase):
    def test_nested_dependency_metadata_is_recursively_deterministic(self) -> None:
        dependency_one = chart_bytes(
            "dep",
            {"Chart.yaml": b"apiVersion: v2\nname: dep\nversion: 1.0.0\n", "templates/configmap.yaml": b"kind: ConfigMap\n"},
            gzip_mtime=11,
            tar_mtime=111,
            uid=123,
        )
        dependency_two = chart_bytes(
            "dep",
            {"Chart.yaml": b"apiVersion: v2\nname: dep\nversion: 1.0.0\n", "templates/configmap.yaml": b"kind: ConfigMap\n"},
            gzip_mtime=22,
            tar_mtime=222,
            uid=456,
        )
        self.assertNotEqual(dependency_one, dependency_two)
        first = canonicalize_chart_archive_bytes(
            outer_with_dependency(dependency_one, gzip_mtime=33, tar_mtime=333),
            expected_root="backend",
        )
        second = canonicalize_chart_archive_bytes(
            outer_with_dependency(dependency_two, gzip_mtime=44, tar_mtime=444),
            expected_root="backend",
        )
        self.assertEqual(first, second)
        with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as outer:
            nested = outer.extractfile(outer.getmember("backend/charts/dep-1.0.0.tgz"))
            self.assertIsNotNone(nested)
            nested_bytes = nested.read()
        with tarfile.open(fileobj=io.BytesIO(nested_bytes), mode="r:gz") as archive:
            self.assertTrue(all(member.mtime == 0 for member in archive.getmembers()))
            self.assertTrue(all(member.uid == 0 and member.gid == 0 for member in archive.getmembers()))

    def test_recursive_archives_share_one_total_member_budget(self) -> None:
        leaf = chart_bytes(
            "leaf",
            {"Chart.yaml": b"apiVersion: v2\nname: leaf\nversion: 1.0.0\n"},
            gzip_mtime=1,
            tar_mtime=1,
        )
        middle = chart_bytes(
            "dep",
            {"Chart.yaml": b"apiVersion: v2\nname: dep\nversion: 1.0.0\n", "charts/leaf-1.0.0.tgz": leaf},
            gzip_mtime=1,
            tar_mtime=1,
        )
        outer = outer_with_dependency(middle, gzip_mtime=1, tar_mtime=1)
        with patch("ci_workflows.helm_archive._MAX_MEMBERS", 5):
            with self.assertRaisesRegex(HelmValidationError, "archive_invalid"):
                canonicalize_chart_archive_bytes(outer, expected_root="backend")

    def test_secret_inside_packaged_dependency_is_rejected(self) -> None:
        dependency = chart_bytes(
            "dep",
            {"Chart.yaml": b"apiVersion: v2\nname: dep\nversion: 1.0.0\n", "templates/secret.yaml": b"token: ghp_abcdefghijklmnopqrstuv\n"},
            gzip_mtime=1,
            tar_mtime=1,
        )
        outer = outer_with_dependency(dependency, gzip_mtime=2, tar_mtime=2)
        with self.assertRaisesRegex(HelmValidationError, "archive_secret_detected"):
            canonicalize_chart_archive_bytes(outer, expected_root="backend")

    def test_symlink_inside_packaged_dependency_is_rejected(self) -> None:
        raw = io.BytesIO()
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                chart = b"apiVersion: v2\nname: dep\nversion: 1.0.0\n"
                info = tarfile.TarInfo("dep/Chart.yaml")
                info.size = len(chart)
                archive.addfile(info, io.BytesIO(chart))
                link = tarfile.TarInfo("dep/templates/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                archive.addfile(link)
        outer = outer_with_dependency(raw.getvalue(), gzip_mtime=1, tar_mtime=1)
        with self.assertRaisesRegex(HelmValidationError, "archive_invalid"):
            canonicalize_chart_archive_bytes(outer, expected_root="backend")

    def test_finalize_updates_digest_summary_and_replaces_preliminary_archive(self) -> None:
        dependency = chart_bytes(
            "dep",
            {"Chart.yaml": b"apiVersion: v2\nname: dep\nversion: 1.0.0\n"},
            gzip_mtime=9,
            tar_mtime=9,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preliminary = root / "normalized.tgz"
            preliminary.write_bytes(outer_with_dependency(dependency, gzip_mtime=10, tar_mtime=10))
            validation = HelmValidationResult(
                chart_digest="sha256:" + "a" * 64,
                package_sha256="a" * 64,
                summary=json.dumps({"chart_name": "backend", "package_sha256": "a" * 64, "status": "success"}),
                archive_path=preliminary,
            )
            result = finalize_validation_archive(validation, "backend")
            self.assertFalse(preliminary.exists())
            self.assertTrue(result.archive_path.is_file())
            self.assertEqual(result.archive_path.name, "canonical.tgz")
            self.assertEqual(result.chart_digest, "sha256:" + result.package_sha256)
            self.assertEqual(json.loads(result.summary)["package_sha256"], result.package_sha256)
            second = root / "second.tgz"
            digest = canonicalize_chart_archive(result.archive_path, second, "backend")
            self.assertEqual(digest, result.package_sha256)
            self.assertEqual(second.read_bytes(), result.archive_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
