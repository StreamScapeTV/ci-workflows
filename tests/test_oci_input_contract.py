from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.oci_input_contract import (  # noqa: E402
    INPUT_FAILURE_CODES,
    OciBaseEvidence,
    OciExternalInputEvidence,
    OciInputContractError,
    OciTargetInputEvidence,
    canonical_lock_digest,
    load_input_lock_contract,
    validate_target_dockerfile_lock,
)

ROOT_DIGEST = "sha256:" + "a" * 64
MANIFEST_DIGEST = "sha256:" + "b" * 64
CONFIG_DIGEST = "sha256:" + "c" * 64
CONTENT_SHA256 = "d" * 64
PLATFORMS = ("linux/amd64",)


def external_base(
    *,
    stage_id: str = "stage-1",
    ordinal: int = 1,
    marker: str = "final",
) -> dict[str, object]:
    return {
        "stage_id": stage_id,
        "from_ordinal": ordinal,
        "stage_marker": marker,
        "kind": "external",
        "declared_reference": f"registry.example.com/library/base@{ROOT_DIGEST}",
        "dockerfile_platform": None,
        "platforms": list(PLATFORMS),
        "platform_identities": [
            {
                "platform": "linux/amd64",
                "manifest_digest": MANIFEST_DIGEST,
                "config_digest": CONFIG_DIGEST,
            }
        ],
    }


def external_input(
    *, input_id: str = "dependency", destination: str = ".ciw-build-inputs/dependency.bin"
) -> dict[str, object]:
    return {
        "input_id": input_id,
        "url": "https://downloads.example.com/releases/dependency.bin",
        "sha256": CONTENT_SHA256,
        "maximum_bytes": 4096,
        "destination": destination,
    }


def lock_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "lock_version": "1.0.0",
        "product_id": "ciw-oci-input-smoke",
        "target_id": "immutable-input-smoke",
        "input_policy_id": "oci-inputs-public-v1",
        "platforms": list(PLATFORMS),
        "bases": [external_base()],
        "external_inputs": [external_input()],
    }


class OciInputContractTests(unittest.TestCase):
    maxDiff = None

    def _load(self, root: Path, payload: dict[str, object]):
        lock = root / "locks/input-lock.json"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps(payload), encoding="utf-8")
        return load_input_lock_contract(
            root,
            "locks/input-lock.json",
            product_id="ciw-oci-input-smoke",
            target_id="immutable-input-smoke",
            input_policy_id="oci-inputs-public-v1",
            expected_platforms=PLATFORMS,
        )

    def test_schema_is_closed_target_lock_and_source_cannot_define_acquisition_policy(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/oci-build-input-lock.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual("oci-inputs-public-v1", lock_payload()["input_policy_id"])
        encoded = json.dumps(schema, sort_keys=True)
        for forbidden in (
            "acquisition_profiles",
            "allowed_hosts",
            "redirect_policy",
            "ambient_auth",
            "credential",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertIn("platform_identities", schema["$defs"]["baseLock"]["required"])
        self.assertEqual(
            {
                "dockerfile_parser_ambiguous",
                "input_base_mutable",
                "input_destination_unsafe",
                "input_digest_invalid",
                "input_host_forbidden",
                "input_lock_duplicate",
                "input_lock_incomplete",
                "input_lock_invalid",
                "input_lock_mismatch",
                "input_lock_path_invalid",
                "input_platform_invalid",
                "input_policy_mismatch",
                "input_size_invalid",
                "input_url_invalid",
            },
            INPUT_FAILURE_CODES,
        )

    def test_exact_target_lock_loads_and_digest_is_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self._load(root, lock_payload())
            (root / "locks/input-lock.json").write_text(
                json.dumps(lock_payload(), indent=4, sort_keys=False), encoding="utf-8"
            )
            second = load_input_lock_contract(
                root,
                "locks/input-lock.json",
                product_id=first.product_id,
                target_id=first.target_id,
                input_policy_id=first.input_policy_id,
                expected_platforms=first.platforms,
            )
        self.assertEqual(first, second)
        self.assertEqual(canonical_lock_digest(first), first.lock_digest)
        self.assertRegex(first.lock_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual("external", first.bases[0].kind)
        self.assertEqual("linux/amd64", first.bases[0].platform_identities[0].platform)

    def test_lock_binds_central_product_target_policy_and_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = root / "lock.json"
            lock.write_text(json.dumps(lock_payload()), encoding="utf-8")
            kwargs = {
                "product_id": "ciw-oci-input-smoke",
                "target_id": "immutable-input-smoke",
                "input_policy_id": "oci-inputs-public-v1",
                "expected_platforms": PLATFORMS,
            }
            for field, value, code in (
                ("product_id", "another-product", "input_policy_mismatch"),
                ("target_id", "another-target", "input_policy_mismatch"),
                ("input_policy_id", "another-policy", "input_policy_mismatch"),
                ("expected_platforms", ("linux/arm64/v8",), "input_platform_invalid"),
            ):
                with self.subTest(field=field):
                    changed = {**kwargs, field: value}
                    with self.assertRaisesRegex(OciInputContractError, code):
                        load_input_lock_contract(root, "lock.json", **changed)

    def test_duplicate_json_keys_and_boolean_schema_version_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = root / "lock.json"
            lock.write_text(
                json.dumps(lock_payload()).replace(
                    '"schema_version": 1,',
                    '"schema_version": 1, "schema_version": 1,',
                ),
                encoding="utf-8",
            )
            kwargs = {
                "product_id": "ciw-oci-input-smoke",
                "target_id": "immutable-input-smoke",
                "input_policy_id": "oci-inputs-public-v1",
                "expected_platforms": PLATFORMS,
            }
            with self.assertRaisesRegex(OciInputContractError, "input_lock_duplicate"):
                load_input_lock_contract(root, "lock.json", **kwargs)
            invalid = lock_payload()
            invalid["schema_version"] = True
            lock.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(OciInputContractError, "input_lock_invalid"):
                load_input_lock_contract(root, "lock.json", **kwargs)

    def test_exact_dockerfile_stage_agreement_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            dockerfile.write_text(
                f"FROM registry.example.com/library/base@{ROOT_DIGEST}\n",
                encoding="utf-8",
            )
            self.assertEqual(
                lock.bases,
                validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS),
            )
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            with self.assertRaisesRegex(OciInputContractError, "input_lock_mismatch"):
                validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_digest_pinned_reference_may_retain_a_non_authoritative_tag(self) -> None:
        payload = lock_payload()
        tagged = f"registry.example.com/library/base:1.2.3@{ROOT_DIGEST}"
        payload["bases"][0]["declared_reference"] = tagged
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, payload)
            dockerfile = root / "Containerfile"
            dockerfile.write_text(f"FROM {tagged}\n", encoding="utf-8")
            validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_multistage_repeats_literal_digest_and_may_reference_prior_alias_in_copy_and_run(self) -> None:
        payload = lock_payload()
        payload["bases"] = [
            external_base(stage_id="builder", marker="intermediate"),
            external_base(stage_id="final", ordinal=2),
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, payload)
            dockerfile = root / "Containerfile"
            dockerfile.write_text(
                f"FROM registry.example.com/library/base@{ROOT_DIGEST} AS builder\n"
                f"FROM registry.example.com/library/base@{ROOT_DIGEST} AS final\n"
                "COPY --from=builder /bin/tool /tool\n"
                "RUN --mount=type=bind,from=builder,source=/,target=/src,ro true\n",
                encoding="utf-8",
            )
            validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)
        self.assertEqual(("external", "external"), tuple(base.kind for base in lock.bases))

    def test_from_prior_alias_is_rejected_even_when_alias_is_declared(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            dockerfile.write_text(
                f"FROM registry.example.com/library/base@{ROOT_DIGEST} AS builder\n"
                "FROM builder\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(OciInputContractError, "input_base_mutable"):
                validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_platform_child_manifest_and_config_locks_are_complete_and_exact(self) -> None:
        for change, code in (
            ({"platform_identities": []}, "input_lock_incomplete"),
            (
                {
                    "platform_identities": [
                        {
                            "platform": "linux/arm64/v8",
                            "manifest_digest": MANIFEST_DIGEST,
                            "config_digest": CONFIG_DIGEST,
                        }
                    ]
                },
                "input_lock_incomplete",
            ),
            (
                {
                    "platform_identities": [
                        {
                            "platform": "linux/amd64",
                            "manifest_digest": "sha256:bad",
                            "config_digest": CONFIG_DIGEST,
                        }
                    ]
                },
                "input_digest_invalid",
            ),
        ):
            with self.subTest(change=change):
                payload = lock_payload()
                payload["bases"][0].update(change)
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaisesRegex(OciInputContractError, code):
                        self._load(Path(temp), payload)

    def test_scratch_lock_has_no_platform_child_acquisition_identities(self) -> None:
        payload = lock_payload()
        payload["bases"] = [
            {
                "stage_id": "stage-1",
                "from_ordinal": 1,
                "stage_marker": "final",
                "kind": "scratch",
                "declared_reference": "scratch",
                "dockerfile_platform": None,
                "platforms": list(PLATFORMS),
                "platform_identities": [],
            }
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, payload)
            dockerfile = root / "Containerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_mutable_variable_and_undeclared_bases_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            for source, code in (
                ("FROM python:3.12\n", "input_base_mutable"),
                ("ARG BASE\nFROM $BASE\n", "input_base_mutable"),
                (f"FROM registry.example.com/library/base@{ROOT_DIGEST}\nFROM scratch\n", "input_lock_incomplete"),
            ):
                with self.subTest(source=source):
                    dockerfile.write_text(source, encoding="utf-8")
                    with self.assertRaisesRegex(OciInputContractError, code):
                        validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_parser_ambiguities_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            for source in (
                f"\ufeffFROM registry.example.com/library/base@{ROOT_DIGEST}\n",
                f"# syntax=docker/dockerfile:1\nFROM registry.example.com/library/base@{ROOT_DIGEST}\n",
                f"# escape=`\nFROM registry.example.com/library/base@{ROOT_DIGEST}\n",
                f"# check=error=true\nFROM registry.example.com/library/base@{ROOT_DIGEST}\n",
                f"FROM \\\n+# hidden\nregistry.example.com/library/base@{ROOT_DIGEST}\n",
                f"FROM --mount=type=secret registry.example.com/library/base@{ROOT_DIGEST}\n",
                f"FROM registry.example.com/library/base@{ROOT_DIGEST} AS Bad_Alias\n",
                f"FROM registry.example.com/library/base@{ROOT_DIGEST} AS scratch\n",
                f"FROM registry.example.com/library/base@{ROOT_DIGEST}\nRUN <<EOF\ntrue\nEOF\n",
            ):
                with self.subTest(source=source):
                    dockerfile.write_text(source, encoding="utf-8")
                    with self.assertRaisesRegex(OciInputContractError, "dockerfile_parser_ambiguous"):
                        validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_copy_and_run_sources_must_name_an_exact_prior_stage_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            for instruction in (
                "COPY --from=0 /bin/tool /tool",
                "COPY --from=missing /bin/tool /tool",
                "COPY --from=docker.io/library/busybox@sha256:" + "e" * 64 + " /bin/tool /tool",
                "COPY --from=$SOURCE /bin/tool /tool",
                "COPY --from missing /bin/tool /tool",
                "COPY --from=missing --from=other /bin/tool /tool",
                "RUN --mount=type=bind,from=0,target=/src true",
                "RUN --mount=type=bind,from=missing,target=/src true",
                "RUN --mount=type=bind,from=docker.io/library/base,target=/src true",
                "RUN --mount=type=bind,from=$SOURCE,target=/src true",
                "RUN --mount type=bind,from=missing,target=/src true",
                "RUN --mount=type=bind,from=missing,from=other,target=/src true",
                "RUN --mount=type=bind,target=/ctx,rw true",
                "RUN --mount=type=bind,target=/ctx true",
                "RUN --mount=type=cache,target=/ctx true",
                "RUN --mount=type=secret,id=credential true",
            ):
                with self.subTest(instruction=instruction):
                    dockerfile.write_text(
                        f"FROM registry.example.com/library/base@{ROOT_DIGEST}\n"
                        + instruction
                        + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaises(OciInputContractError):
                        validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_prior_stage_mount_must_be_explicitly_read_only(self) -> None:
        payload = lock_payload()
        payload["bases"] = [
            external_base(stage_id="builder", marker="intermediate"),
            external_base(stage_id="final", ordinal=2),
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, payload)
            dockerfile = root / "Containerfile"
            prefix = (
                f"FROM registry.example.com/library/base@{ROOT_DIGEST} AS builder\n"
                f"FROM registry.example.com/library/base@{ROOT_DIGEST} AS final\n"
            )
            for instruction in (
                "RUN --mount=type=bind,from=builder,target=/ctx true",
                "RUN --mount=type=bind,from=builder,target=/ctx,rw true",
                "RUN --mount=type=bind,from=builder,target=/ctx,readwrite true",
                "RUN --mount=type=bind,from=builder,target=/ctx,readonly true",
                "RUN --mount=type=bind,from=builder,target=/ctx,ro=false true",
                "RUN --mount=type=bind,from=builder,source=../escape,target=/ctx,ro true",
                "RUN --mount=type=bind,from=builder,target=relative,ro true",
            ):
                with self.subTest(instruction=instruction):
                    dockerfile.write_text(prefix + instruction + "\n", encoding="utf-8")
                    with self.assertRaises(OciInputContractError):
                        validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_run_rejects_every_non_mount_leading_option(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            for instruction in (
                "RUN --network=host true",
                "RUN --network=default true",
                "RUN --network=$NETWORK true",
                "RUN --network host true",
                "RUN --security=insecure true",
                "RUN --device=/dev/fuse true",
                "RUN --privileged true",
                "RUN --mount=type=bind,target=/ctx,rw --network=host true",
            ):
                with self.subTest(instruction=instruction):
                    dockerfile.write_text(
                        f"FROM registry.example.com/library/base@{ROOT_DIGEST}\n"
                        + instruction
                        + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaises(OciInputContractError):
                        validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_non_space_whitespace_and_controls_cannot_hide_copy_or_run_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            for instruction in (
                "COPY --chown=0:0\t--from=missing /source /destination",
                "COPY --chown=0:0\v--from=missing /source /destination",
                "COPY --chown=0:0\u00a0--from=missing /source /destination",
                "RUN --mount=type=bind,target=/ctx,rw\t--network=host true",
                "RUN --mount=type=bind,target=/ctx,rw\f--network=host true",
                "RUN --network=host\u2003true",
                "RUN true\x00--network=host",
            ):
                with self.subTest(instruction=instruction):
                    dockerfile.write_text(
                        f"FROM registry.example.com/library/base@{ROOT_DIGEST}\n"
                        + instruction
                        + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        OciInputContractError, "dockerfile_parser_ambiguous"
                    ):
                        validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_add_onbuild_and_remote_local_copy_sources_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            for instruction in (
                "ADD local-file /destination",
                "ADD https://downloads.example.com/file /destination",
                "ADD git@github.com:example/repository.git /destination",
                "ONBUILD ADD https://downloads.example.com/file /destination",
                "ONBUILD COPY --from=missing /source /destination",
                "ONBUILD RUN --mount=type=bind,from=missing,target=/src true",
                "COPY https://downloads.example.com/file /destination",
                "COPY git://github.com/example/repository.git /destination",
                "COPY ../outside /destination",
                "COPY $SOURCE /destination",
            ):
                with self.subTest(instruction=instruction):
                    dockerfile.write_text(
                        f"FROM registry.example.com/library/base@{ROOT_DIGEST}\n"
                        + instruction
                        + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(OciInputContractError, "input_lock_incomplete"):
                        validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_local_and_reserved_copy_sources_remain_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self._load(root, lock_payload())
            dockerfile = root / "Containerfile"
            dockerfile.write_text(
                f"FROM registry.example.com/library/base@{ROOT_DIGEST}\n"
                "COPY --chmod=0444 .ciw-build-inputs/dependency.bin /dependency.bin\n"
                "COPY [\"tracked/file\", \"/tracked-file\"]\n",
                encoding="utf-8",
            )
            validate_target_dockerfile_lock(dockerfile, lock, PLATFORMS)

    def test_external_url_host_size_digest_and_reserved_destination_are_closed(self) -> None:
        cases = (
            ("url", "http://downloads.example.com/file", "input_url_invalid"),
            ("url", "https://user:secret@downloads.example.com/file", "input_url_invalid"),
            ("url", "https://127.0.0.1/file", "input_host_forbidden"),
            ("url", "https://metadata.google.internal/file", "input_host_forbidden"),
            ("url", "https://DOWNLOADS.example.com/file", "input_url_invalid"),
            ("url", "https://downloads.example.com:444/file", "input_url_invalid"),
            ("url", "https://downloads.example.com/file?mutable=1", "input_url_invalid"),
            ("sha256", "bad", "input_digest_invalid"),
            ("maximum_bytes", 0, "input_size_invalid"),
            ("maximum_bytes", 1_073_741_825, "input_size_invalid"),
            ("destination", "../escape", "input_destination_unsafe"),
            ("destination", "/absolute", "input_destination_unsafe"),
            ("destination", ".ciw-build-inputs/../escape", "input_destination_unsafe"),
            ("destination", ".ciw-build-inputs/a//b", "input_destination_unsafe"),
            ("destination", ".ciw-build-inputs/a b", "input_destination_unsafe"),
            ("destination", ".ciw-build-inputs/.hidden", "input_destination_unsafe"),
            ("destination", ".ciw-build-inputs/a/.ciw-build-inputs/b", "input_destination_unsafe"),
            ("destination", "ordinary/file", "input_destination_unsafe"),
        )
        for field, value, code in cases:
            with self.subTest(field=field, value=value):
                payload = lock_payload()
                payload["external_inputs"][0][field] = value
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaisesRegex(OciInputContractError, code):
                        self._load(Path(temp), payload)

    def test_duplicate_and_incomplete_locks_fail_closed(self) -> None:
        changes = []
        empty = lock_payload()
        empty["bases"] = []
        changes.append((empty, "input_lock_incomplete"))
        duplicate_stage = lock_payload()
        second = external_base(ordinal=2)
        second["stage_marker"] = "final"
        duplicate_stage["bases"][0]["stage_marker"] = "intermediate"
        duplicate_stage["bases"].append(second)
        changes.append((duplicate_stage, "input_lock_duplicate"))
        duplicate_input = lock_payload()
        duplicate_input["external_inputs"].append(deepcopy(duplicate_input["external_inputs"][0]))
        changes.append((duplicate_input, "input_lock_duplicate"))
        for payload, code in changes:
            with self.subTest(code=code):
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaisesRegex(OciInputContractError, code):
                        self._load(Path(temp), payload)

    def test_lock_path_traversal_and_symlinks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root.parent / f"{root.name}-outside.json"
            outside.write_text(json.dumps(lock_payload()), encoding="utf-8")
            try:
                with self.assertRaisesRegex(OciInputContractError, "input_lock_path_invalid"):
                    load_input_lock_contract(
                        root,
                        f"../{outside.name}",
                        product_id="ciw-oci-input-smoke",
                        target_id="immutable-input-smoke",
                        input_policy_id="oci-inputs-public-v1",
                        expected_platforms=PLATFORMS,
                    )
                alias = root / "lock.json"
                alias.symlink_to(outside)
                with self.assertRaisesRegex(OciInputContractError, "input_lock_path_invalid"):
                    load_input_lock_contract(
                        root,
                        "lock.json",
                        product_id="ciw-oci-input-smoke",
                        target_id="immutable-input-smoke",
                        input_policy_id="oci-inputs-public-v1",
                        expected_platforms=PLATFORMS,
                    )
            finally:
                outside.unlink(missing_ok=True)

    def test_evidence_is_redacted_and_contains_no_url_destination_or_host_path(self) -> None:
        evidence = OciTargetInputEvidence(
            target_id="immutable-input-smoke",
            input_policy_id="oci-inputs-public-v1",
            lock_digest=ROOT_DIGEST,
            bases=(
                OciBaseEvidence(
                    target_id="immutable-input-smoke",
                    stage_id="stage-1",
                    from_ordinal=1,
                    declared_reference=f"registry.example.com/library/base@{ROOT_DIGEST}",
                    platform="linux/amd64",
                    root_digest=ROOT_DIGEST,
                    manifest_digest=MANIFEST_DIGEST,
                    config_digest=CONFIG_DIGEST,
                    acquisition_policy_id="oci-inputs-public-v1",
                ),
            ),
            external_inputs=(
                OciExternalInputEvidence(
                    target_id="immutable-input-smoke",
                    input_id="dependency",
                    sha256=CONTENT_SHA256,
                    size_bytes=32,
                    acquisition_policy_id="oci-inputs-public-v1",
                ),
            ),
        )
        payload = evidence.to_dict()
        encoded = json.dumps(payload, sort_keys=True)
        self.assertTrue(payload["redacted"])
        self.assertNotIn("https://", encoded)
        self.assertNotIn(".ciw-build-inputs", encoded)
        self.assertNotIn("token", encoded.lower())


if __name__ == "__main__":
    unittest.main()
