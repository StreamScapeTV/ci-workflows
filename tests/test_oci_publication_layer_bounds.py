from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import oci_execution as build_execution
from ci_workflows import oci_execution_safe
from ci_workflows.oci_publish_assertions import (
    _LayerInventoryLimits,
    _layer_inventory,
)
from ci_workflows.oci_types import OciBuildError


def _tar(
    members: tuple[tuple[str, str | None], ...],
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, linkname in members:
            member = tarfile.TarInfo(name)
            if linkname is not None:
                member.type = tarfile.SYMTYPE
                member.linkname = linkname
            archive.addfile(member)
    return buffer.getvalue()


def _blob(layout: Path, payload: bytes) -> str:
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    path = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return digest


def _pax_record(key: str, value: str) -> bytes:
    size = len(key.encode()) + len(value.encode()) + 4
    while True:
        record = f"{size} {key}={value}\n".encode()
        if len(record) == size:
            return record
        size = len(record)


def _pax_layer(count: int, *, key: str = "comment", value: str = "safe") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for index in range(count):
            payload = _pax_record(key, f"{value}{index}")
            member = tarfile.TarInfo(f"pax-{index}")
            member.type = tarfile.XGLTYPE
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        archive.addfile(tarfile.TarInfo("real"))
    return buffer.getvalue()


def _limits(*, members: int, path_bytes: int, work_bytes: int) -> _LayerInventoryLimits:
    return _LayerInventoryLimits(
        maximum_members=members,
        maximum_path_bytes=path_bytes,
        maximum_decompressed_bytes=work_bytes,
        maximum_overlay_scan_work=64 * 1024,
    )


class PublicationLayerInventoryBoundTests(unittest.TestCase):
    def test_member_count_limit_accepts_exact_bound_and_rejects_plus_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary)
            first = _blob(layout, _tar((("one", None),)))
            second = _blob(layout, _tar((("two", None),)))
            third = _blob(layout, _tar((("three", None),)))
            limits = _limits(members=2, path_bytes=64, work_bytes=64 * 1024)

            self.assertEqual(
                {"/one", "/two"},
                set(_layer_inventory(layout, (first, second), _limits=limits)),
            )
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                _layer_inventory(
                    layout,
                    (first, second, third),
                    _limits=limits,
                )

    def test_path_byte_limit_counts_names_and_links_across_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary)
            first = _blob(layout, _tar((("aa", None),)))
            exact = _blob(layout, _tar((("b", "cc"),)))
            plus_one = _blob(layout, _tar((("b", "ccc"),)))
            limits = _limits(members=2, path_bytes=5, work_bytes=64 * 1024)

            self.assertEqual(
                {"/aa", "/b"},
                set(_layer_inventory(layout, (first, exact), _limits=limits)),
            )
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                _layer_inventory(
                    layout,
                    (first, plus_one),
                    _limits=limits,
                )

    def test_global_pax_metadata_is_rejected_before_persistent_state_grows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary)
            digest = _blob(layout, _pax_layer(1))
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                _layer_inventory(layout, (digest,))

    def test_sparse_pax_metadata_maps_to_stable_layout_failure(self) -> None:
        payload = b"".join(
            (
                _pax_record("GNU.sparse.major", "0"),
                _pax_record("GNU.sparse.minor", "1"),
                _pax_record("GNU.sparse.map", "0,1"),
            )
        )
        buffer = io.BytesIO()
        with tarfile.open(
            fileobj=buffer,
            mode="w",
            format=tarfile.USTAR_FORMAT,
        ) as archive:
            member = tarfile.TarInfo("pax")
            member.type = tarfile.XHDTYPE
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
            archive.addfile(tarfile.TarInfo("real"))
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary)
            digest = _blob(layout, buffer.getvalue())
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                _layer_inventory(layout, (digest,))

    def test_compressed_work_limit_accepts_exact_and_rejects_plus_one(
        self,
    ) -> None:
        archive = _tar((("usr/bin/tool", None),))
        compressed = gzip.compress(archive, mtime=0)
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary)
            digest = _blob(layout, compressed)

            with patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("layer blobs must remain streamed"),
            ):
                self.assertEqual(
                    {"/usr/bin/tool"},
                    set(
                        _layer_inventory(
                            layout,
                            (digest,),
                            _limits=_limits(
                                members=1,
                                path_bytes=len("usr/bin/tool"),
                                work_bytes=len(archive),
                            ),
                        )
                    ),
                )
                with self.assertRaisesRegex(
                    OciBuildError, "oci_layout_malformed"
                ):
                    _layer_inventory(
                        layout,
                        (digest,),
                        _limits=_limits(
                            members=1,
                            path_bytes=len("usr/bin/tool"),
                            work_bytes=len(archive) - 1,
                        ),
                    )

    def test_bounds_preserve_symlink_and_whiteout_overlay_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary)
            lower_payload = _tar(
                (("old/tool", None), ("bin/actual", None), ("bin/tool", "actual"))
            )
            upper_payload = _tar((("old/.wh.tool", None),))
            lower = _blob(layout, gzip.compress(lower_payload, mtime=0))
            upper = _blob(layout, gzip.compress(upper_payload, mtime=0))
            result = _layer_inventory(
                layout,
                (lower, upper),
                _limits=_limits(
                    members=4,
                    path_bytes=(
                        len("old/tool")
                        + len("bin/actual")
                        + len("bin/tool")
                        + len("actual")
                        + len("old/.wh.tool")
                    ),
                    work_bytes=len(lower_payload) + len(upper_payload),
                ),
            )

            self.assertEqual({"/bin/actual", "/bin/tool"}, set(result))
            self.assertEqual("/bin/actual", result["/bin/tool"].symlink_target)

    def test_descriptor_size_is_rejected_before_blob_hashing(self) -> None:
        descriptor = {
            "mediaType": "application/vnd.oci.image.layer.v1.tar",
            "digest": "sha256:" + "1" * 64,
            "size": oci_execution_safe._MAXIMUM_LAYER_BYTES + 1,  # noqa: SLF001
        }
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "layers": [descriptor],
        }
        with patch.object(
            build_execution,
            "_read_json",
            side_effect=[{"schemaVersion": 2}, manifest],
        ), patch.object(
            build_execution,
            "_image_manifest_descriptors",
            return_value=({"digest": "sha256:" + "2" * 64},),
        ), patch.object(
            build_execution,
            "_descriptor_blob",
            side_effect=[(Path("manifest"), "sha256:" + "2" * 64)],
        ) as descriptor_blob, self.assertRaisesRegex(
            OciBuildError, "oci_layout_malformed"
        ):
            oci_execution_safe._manifest_layer_sets(Path("layout"))  # noqa: SLF001
        descriptor_blob.assert_called_once()

    def test_overlay_scan_work_accepts_exact_bound_and_rejects_plus_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary)
            lower = _blob(layout, _tar((("one", None), ("two", None))))
            upper = _blob(layout, _tar(((".wh.one", None),)))
            common = {
                "maximum_members": 3,
                "maximum_path_bytes": 32,
                "maximum_decompressed_bytes": 64 * 1024,
            }

            self.assertEqual(
                {"/two"},
                set(
                    _layer_inventory(
                        layout,
                        (lower, upper),
                        _limits=_LayerInventoryLimits(
                            **common,
                            maximum_overlay_scan_work=2,
                        ),
                    )
                ),
            )
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                _layer_inventory(
                    layout,
                    (lower, upper),
                    _limits=_LayerInventoryLimits(
                        **common,
                        maximum_overlay_scan_work=1,
                    ),
                )


if __name__ == "__main__":
    unittest.main()
