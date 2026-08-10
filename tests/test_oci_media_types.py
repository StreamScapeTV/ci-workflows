from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.oci_execution_safe import _inspect_layout_oci  # noqa: E402
from ci_workflows.oci_types import OciBuildError, OciTarget  # noqa: E402


def write_blob(layout: Path, payload: bytes) -> tuple[str, int]:
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    path = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return digest, len(payload)


def make_layout(root: Path, labels: dict[str, str]) -> Path:
    layout = root / "layout"
    layout.mkdir()
    (layout / "oci-layout").write_text(
        '{"imageLayoutVersion":"1.0.0"}', encoding="utf-8"
    )
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {
                "User": "65532:65532",
                "Entrypoint": ["/hello"],
                "Labels": labels,
            },
            "rootfs": {"type": "layers", "diff_ids": []},
            "history": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    config_digest, config_size = write_blob(layout, config)
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w") as archive:
        content = b"hello\n"
        member = tarfile.TarInfo("hello")
        member.size = len(content)
        member.mode = 0o755
        archive.addfile(member, io.BytesIO(content))
    layer = layer_buffer.getvalue()
    layer_digest, layer_size = write_blob(layout, layer)
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": config_size,
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": layer_digest,
                    "size": layer_size,
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest_digest, manifest_size = write_blob(layout, manifest)
    index = {
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": manifest_digest,
                "size": manifest_size,
                "platform": {"os": "linux", "architecture": "amd64"},
            }
        ],
    }
    (layout / "index.json").write_text(
        json.dumps(index, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return layout


class OciMediaTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.labels = {
            "dev.streamscape.product": "fixture",
            "org.opencontainers.image.created": "1970-01-01T00:00:01Z",
            "org.opencontainers.image.description": "fixture",
            "org.opencontainers.image.licenses": "MIT",
            "org.opencontainers.image.revision": "a" * 40,
            "org.opencontainers.image.source": "https://github.com/StreamScapeTV/ci-workflows",
            "org.opencontainers.image.title": "fixture",
            "org.opencontainers.image.version": "1.0.0",
        }
        self.target = OciTarget(
            "fixture",
            ".",
            "Containerfile",
            None,
            ("linux/amd64",),
            None,
            "65532:65532",
            ("/hello",),
            (),
            (),
            ("/hello",),
            (),
            (),
            {},
            (),
        )

    def test_omitted_redundant_top_level_media_types_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = _inspect_layout_oci(make_layout(Path(temp), self.labels), self.target, self.labels)
        self.assertEqual("linux/amd64", result.platform_results[0].platform)

    def test_non_oci_top_level_media_type_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp), self.labels)
            index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
            index["mediaType"] = "application/vnd.docker.distribution.manifest.list.v2+json"
            (layout / "index.json").write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaisesRegex(OciBuildError, "oci_layout_malformed"):
                _inspect_layout_oci(layout, self.target, self.labels)


if __name__ == "__main__":
    unittest.main()
