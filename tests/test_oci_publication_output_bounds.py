from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ci_workflows import ciw_oci
from ci_workflows.ciw_types import CIWContext
from ci_workflows.oci_publish import (
    OciPublishError,
    PublishTarget,
    public_json_outputs,
)


SHA = "a" * 40
DIGEST = "sha256:" + "1" * 64


def _resolved_inputs() -> dict[str, object]:
    payload: dict[str, object] = {
        "lock_digest": "none",
        "input_policy_id": "scratch-only-v1",
        "bases": [],
        "external_inputs": [],
    }
    return {
        **payload,
        "evidence_id": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _target(target_id: str = "backend") -> PublishTarget:
    repository = "git.faruqi.dev/mimranfaruqi/iptv-backend"
    return PublishTarget(
        target_id=target_id,
        source_repository="StreamScapeTV/iptv-backend",
        platforms=("linux/amd64",),
        registry_repository=repository,
        version_reference=repository + ":1.2.3",
        source_reference=repository + ":sha-" + SHA,
        metadata={
            "title": "IPTV Backend",
            "description": "StreamScapeTV backend application image",
            "licenses": "Proprietary",
        },
        required_user="appuser",
        required_entrypoint=(),
        required_command=("/app/start",),
        required_ports=("8080/tcp",),
    )


def _labels(target: PublishTarget) -> dict[str, str]:
    return {
        "dev.streamscape.product": target.target_id,
        "org.opencontainers.image.created": "2026-08-12T00:00:00Z",
        "org.opencontainers.image.description": target.metadata["description"],
        "org.opencontainers.image.licenses": target.metadata["licenses"],
        "org.opencontainers.image.revision": SHA,
        "org.opencontainers.image.source": (
            "https://github.com/" + target.source_repository
        ),
        "org.opencontainers.image.title": target.metadata["title"],
        "org.opencontainers.image.version": "1.2.3",
    }


def _assertions(target: PublishTarget) -> dict[str, object]:
    return {
        "result": "passed",
        "verified_platforms": list(target.platforms),
        "contract_digest": "sha256:" + "2" * 64,
        "runtime": {
            "user": "appuser",
            "entrypoint": {"count": 0, "digest": "sha256:" + "3" * 64},
            "command": {"count": 1, "digest": "sha256:" + "4" * 64},
            "ports": ["8080/tcp"],
        },
        "filesystem": {
            "required_files": [],
            "required_tools": [],
            "required_executables": [],
            "forbidden_tools": [],
            "forbidden_paths": [],
        },
        "healthcheck": {"mode": "absent"},
    }


def _payload(
    target: PublishTarget,
    *,
    layer_count: int = 1,
    labels: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, object], dict[str, object]]:
    manifests = {target.target_id: DIGEST}
    platforms = {
        target.target_id: {
            "linux/amd64": {
                "manifest_digest": DIGEST,
                "config_digest": "sha256:" + "5" * 64,
                "layer_digests": ["sha256:" + "6" * 64] * layer_count,
                "labels": _labels(target) if labels is None else labels,
            }
        }
    }
    immutable = {
        "registry_write_policy": {
            "policy_id": "iptv-backend-create-only-v1",
            "registry_host": "git.faruqi.dev",
            "required_enforcement": "server-side-create-only-tags-v1",
            "status": "verified",
            "authority_repository": "StreamScapeTV/flux",
            "authority_source_sha": "7" * 40,
            "evidence_id": "sha256:" + "8" * 64,
        },
        "targets": {
            target.target_id: {
                "repository": target.registry_repository,
                "version": target.version_reference,
                "source_reference": target.source_reference,
                "manifest_digest": DIGEST,
                "resolved_inputs": _resolved_inputs(),
                "assertions": _assertions(target),
            }
        },
        "release": {"source_sha": SHA, "version": "1.2.3"},
    }
    return manifests, platforms, immutable


class PublicJsonOutputBoundsTests(unittest.TestCase):
    def test_accepts_256_layers_and_rejects_257(self) -> None:
        target = _target()
        accepted = _payload(target, layer_count=256)
        outputs = public_json_outputs((target,), *accepted)
        self.assertLessEqual(
            len(outputs["platform_digests_json"].encode("utf-8")),
            192 * 1024,
        )

        rejected = _payload(target, layer_count=257)
        with self.assertRaisesRegex(
            OciPublishError, "publication_output_invalid"
        ):
            public_json_outputs((target,), *rejected)

    def test_rejects_missing_extra_and_oversized_metadata_labels(self) -> None:
        target = _target()
        cases: list[dict[str, str]] = []
        missing = _labels(target)
        missing.pop("org.opencontainers.image.title")
        cases.append(missing)
        cases.append({**_labels(target), "attacker.example": "value"})
        oversized = _labels(target)
        oversized["org.opencontainers.image.description"] = "x" * 513
        cases.append(oversized)

        for labels in cases:
            with self.subTest(labels=labels), self.assertRaisesRegex(
                OciPublishError, "publication_output_invalid"
            ):
                public_json_outputs((target,), *_payload(target, labels=labels))

    def test_rejects_third_target_and_mismatched_map_keys(self) -> None:
        targets = (_target("one"), _target("two"), _target("three"))
        with self.assertRaisesRegex(
            OciPublishError, "publication_output_invalid"
        ):
            public_json_outputs(targets, {}, {}, {"targets": {}, "release": {}})

        target = _target()
        manifests, platforms, immutable = _payload(target)
        platforms["unexpected"] = platforms.pop(target.target_id)
        with self.assertRaisesRegex(
            OciPublishError, "publication_output_invalid"
        ):
            public_json_outputs((target,), manifests, platforms, immutable)


class PublicationCommandFileBoundsTests(unittest.TestCase):
    def test_individual_output_line_accepts_limit_and_rejects_limit_plus_one(self) -> None:
        overhead = len("result=\n".encode("utf-8"))
        accepted = "x" * (ciw_oci._MAX_PUBLICATION_OUTPUT_LINE_BYTES - overhead)
        ciw_oci.validate_publication_output_values({"result": accepted})
        with self.assertRaisesRegex(
            OciPublishError, "publication_output_invalid"
        ):
            ciw_oci.validate_publication_output_values(
                {"result": accepted + "x"}
            )

    def test_aggregate_output_accepts_limit_and_rejects_limit_plus_one(self) -> None:
        values = {"one": "123", "two": "456"}
        size = sum(len(f"{key}={value}\n".encode()) for key, value in values.items())
        with patch.object(
            ciw_oci, "_MAX_PUBLICATION_OUTPUT_TOTAL_BYTES", size
        ):
            ciw_oci.validate_publication_output_values(values)
        with patch.object(
            ciw_oci, "_MAX_PUBLICATION_OUTPUT_TOTAL_BYTES", size - 1
        ), self.assertRaisesRegex(
            OciPublishError, "publication_output_invalid"
        ):
            ciw_oci.validate_publication_output_values(values)

    def test_oversized_verify_emits_only_safe_failure_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            context = CIWContext(
                root=Path(temp),
                environment={"GITHUB_OUTPUT": str(output)},
                stdout=SimpleNamespace(write=lambda _value: None),
                stderr=SimpleNamespace(write=lambda _value: None),
            )
            with patch.object(
                ciw_oci.publication, "request_from_environment", return_value=object()
            ), patch.object(
                ciw_oci.publication, "resolve_plan", return_value=object()
            ), patch.object(
                ciw_oci.publication,
                "verify",
                return_value={
                    "immutable_references_json": "x"
                    * ciw_oci._MAX_PUBLICATION_OUTPUT_LINE_BYTES,
                    "evidence_id": "1" * 64,
                },
            ):
                with self.assertRaisesRegex(
                    OciPublishError, "publication_output_invalid"
                ):
                    ciw_oci.execute_oci_publish(
                        SimpleNamespace(phase="verify"), context
                    )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "result=failure\nfailure_code=publication_output_invalid\n",
            )


if __name__ == "__main__":
    unittest.main()
