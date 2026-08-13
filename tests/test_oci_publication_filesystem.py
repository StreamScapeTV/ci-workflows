from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from ci_workflows.oci_publish import OciPublishError, PublishRequest, resolve_plan
from ci_workflows.oci_publish_assertions import assert_filesystem_contract

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
MANIFEST_MEDIA = "application/vnd.oci.image.manifest.v1+json"
LAYER_MEDIA = "application/vnd.oci.image.layer.v1.tar"


def _blob(layout: Path, payload: bytes) -> dict[str, object]:
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    path = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"digest": digest, "size": len(payload)}


def _tar(paths: tuple[str, ...]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in paths:
            data = b"verified\n"
            info = tarfile.TarInfo(path.lstrip("/"))
            info.mode = 0o755
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _layout(root: Path, paths: tuple[str, ...]) -> Path:
    layout = root / "layout"
    layout.mkdir(parents=True)
    (layout / "oci-layout").write_text(
        '{"imageLayoutVersion":"1.0.0"}\n', encoding="utf-8"
    )
    layer = _blob(layout, _tar(paths))
    layer["mediaType"] = LAYER_MEDIA
    manifest = {
        "schemaVersion": 2,
        "mediaType": MANIFEST_MEDIA,
        "layers": [layer],
    }
    descriptor = _blob(
        layout,
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
    )
    descriptor["mediaType"] = MANIFEST_MEDIA
    descriptor["platform"] = {"os": "linux", "architecture": "amd64"}
    (layout / "index.json").write_text(
        json.dumps(
            {"schemaVersion": 2, "manifests": [descriptor]},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return layout


class PublicationFilesystemAssertionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = resolve_plan(
            ROOT,
            PublishRequest(
                repository="StreamScapeTV/iptv-backend",
                admitted_sha=SHA,
                release_authority_sha=SHA,
                product_id="iptv-backend-image",
                release_version="1.2.3",
                source_trust="trusted-exact",
            ),
        )
        self.target = self.plan.targets[0]

    def test_real_backend_required_file_and_tool_inventory_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                ("app/docker/start.sh", "usr/bin/python3"),
            )
            assert_filesystem_contract(ROOT, self.plan, self.target, layout)

    def test_real_backend_forbidden_tool_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                ("app/docker/start.sh", "usr/bin/python3", "usr/bin/docker"),
            )
            with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                assert_filesystem_contract(ROOT, self.plan, self.target, layout)

    def test_real_backend_missing_required_file_or_tool_fails_closed(self) -> None:
        for paths in (("usr/bin/python3",), ("app/docker/start.sh",)):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as directory:
                layout = _layout(Path(directory), paths)
                with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                    assert_filesystem_contract(ROOT, self.plan, self.target, layout)


if __name__ == "__main__":
    unittest.main()
