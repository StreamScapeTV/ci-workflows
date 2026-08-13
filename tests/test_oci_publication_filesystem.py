from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Mapping
from unittest.mock import patch

from ci_workflows import oci_publish as runtime
from ci_workflows import oci_publish_guards as guards
from ci_workflows.oci_publish import (
    OciPublishError,
    PublishRequest,
    PublishTarget,
    resolve_plan,
)
from ci_workflows.oci_publish_assertions import assert_filesystem_contract

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
MANIFEST_MEDIA = "application/vnd.oci.image.manifest.v1+json"
CONFIG_MEDIA = "application/vnd.oci.image.config.v1+json"
LAYER_MEDIA = "application/vnd.oci.image.layer.v1.tar"


def _blob(layout: Path, payload: bytes) -> dict[str, object]:
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    path = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"digest": digest, "size": len(payload)}


def _tar(
    paths: tuple[str, ...],
    modes: Mapping[str, int] | None = None,
    symlinks: Mapping[str, str] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in paths:
            data = b"verified\n"
            info = tarfile.TarInfo(path.lstrip("/"))
            info.mode = 0o755 if modes is None else modes.get(path, 0o755)
            if symlinks is not None and path in symlinks:
                info.type = tarfile.SYMTYPE
                info.linkname = symlinks[path]
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _layout(
    root: Path,
    paths: tuple[str, ...],
    *,
    healthcheck: Mapping[str, object] | None = None,
    target: PublishTarget | None = None,
    modes: Mapping[str, int] | None = None,
    symlinks: Mapping[str, str] | None = None,
    additional_layers: tuple[tuple[str, ...], ...] = (),
) -> Path:
    layout = root / "layout"
    layout.mkdir(parents=True)
    (layout / "oci-layout").write_text(
        '{"imageLayoutVersion":"1.0.0"}\n', encoding="utf-8"
    )
    layers: list[dict[str, object]] = []
    for layer_paths, layer_modes, layer_symlinks in (
        (paths, modes, symlinks),
        *((extra_paths, None, None) for extra_paths in additional_layers),
    ):
        layer = _blob(layout, _tar(layer_paths, layer_modes, layer_symlinks))
        layer["mediaType"] = LAYER_MEDIA
        layers.append(layer)
    manifest = {
        "schemaVersion": 2,
        "mediaType": MANIFEST_MEDIA,
        "layers": layers,
    }
    runtime_config: dict[str, object] = {}
    if healthcheck is not None:
        runtime_config["Healthcheck"] = healthcheck
    if target is not None:
        runtime_config.update(
            {
                "Labels": {
                    "dev.streamscape.product": target.target_id,
                    "org.opencontainers.image.created": "1970-01-01T00:00:01Z",
                    "org.opencontainers.image.description": target.metadata[
                        "description"
                    ],
                    "org.opencontainers.image.licenses": target.metadata["licenses"],
                    "org.opencontainers.image.revision": SHA,
                    "org.opencontainers.image.source": (
                        f"https://github.com/{target.source_repository}"
                    ),
                    "org.opencontainers.image.title": target.metadata["title"],
                    "org.opencontainers.image.version": "1.2.3",
                },
                "User": target.required_user or "",
                "Entrypoint": list(target.required_entrypoint) or None,
                "Cmd": list(target.required_command) or None,
                "ExposedPorts": {
                    port: {} for port in target.required_ports
                },
            }
        )
    config = _blob(
        layout,
        json.dumps(
            {
                "architecture": "amd64",
                "os": "linux",
                "config": runtime_config,
                "rootfs": {
                    "type": "layers",
                    "diff_ids": [layer["digest"] for layer in layers],
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    )
    config["mediaType"] = CONFIG_MEDIA
    manifest["config"] = config
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
    _PYTHON = "/usr/local/bin/python3"
    _PYTHON_BACKING = "/usr/local/bin/python3.12"

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
                (
                    "app/docker/start.sh",
                    self._PYTHON,
                    self._PYTHON_BACKING,
                ),
                symlinks={self._PYTHON: "python3.12"},
            )
            assert_filesystem_contract(ROOT, self.plan, self.target, layout)

    def test_real_backend_forbidden_tool_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                (
                    "app/docker/start.sh",
                    self._PYTHON,
                    self._PYTHON_BACKING,
                    "usr/bin/docker",
                ),
                symlinks={self._PYTHON: "python3.12"},
            )
            with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                assert_filesystem_contract(ROOT, self.plan, self.target, layout)

    def test_real_backend_missing_required_file_or_tool_fails_closed(self) -> None:
        for paths, symlinks in (
            (
                (self._PYTHON, self._PYTHON_BACKING),
                {self._PYTHON: "python3.12"},
            ),
            (("app/docker/start.sh",), None),
            (("app/docker/start.sh", "/usr/bin/python3"), None),
        ):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as directory:
                layout = _layout(Path(directory), paths, symlinks=symlinks)
                with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                    assert_filesystem_contract(ROOT, self.plan, self.target, layout)


class FluxPublicationAssertionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = resolve_plan(
            ROOT,
            PublishRequest(
                repository="StreamScapeTV/flux",
                admitted_sha=SHA,
                release_authority_sha=SHA,
                product_id="flux-runner-images",
                release_version="1.2.3",
                source_trust="trusted-exact",
            ),
        )
        self.contract = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        self.raw_targets = {
            target["target_id"]: target
            for target in self.contract["products"]["flux-runner-images"]["targets"]
        }

    def _required_tool_paths(self, target_id: str) -> tuple[str, ...]:
        exact = self.contract["publication_assertions"]["flux-runner-images"][
            target_id
        ]["required_executables"]
        if exact:
            return tuple(exact)
        return tuple(
            f"/usr/bin/{tool}"
            for tool in self.raw_targets[target_id]["assertions"]["required_tools"]
        )

    def _mutated_contract_root(
        self,
        target_id: str,
        healthcheck: Mapping[str, object],
        temporary_root: Path,
    ) -> Path:
        payload = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        payload["publication_assertions"]["flux-runner-images"][target_id][
            "healthcheck"
        ] = dict(healthcheck)
        contract_path = temporary_root / "contracts/oci-products.json"
        contract_path.parent.mkdir(parents=True)
        contract_path.write_text(json.dumps(payload), encoding="utf-8")
        return temporary_root

    def test_real_flux_targets_require_their_checked_in_capability_sets(self) -> None:
        for target in self.plan.targets:
            required = self._required_tool_paths(target.target_id)
            with self.subTest(target=target.target_id), tempfile.TemporaryDirectory() as directory:
                layout = _layout(Path(directory), required)
                assert_filesystem_contract(ROOT, self.plan, target, layout)

                missing = _layout(Path(directory) / "missing", required[:-1])
                with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                    assert_filesystem_contract(ROOT, self.plan, target, missing)

    def test_real_flux_targets_reject_engine_socket_and_credential_residue(self) -> None:
        forbidden = (
            "/usr/bin/docker",
            "/var/run/docker.sock",
            "/home/runner/.config/containers/auth.json",
        )
        for target in self.plan.targets:
            required = self._required_tool_paths(target.target_id)
            for residue in forbidden:
                with (
                    self.subTest(target=target.target_id, residue=residue),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    layout = _layout(Path(directory), (*required, residue))
                    with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                        assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_flux_capabilities_require_exact_regular_executable_paths(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        buildah = "/usr/bin/buildah"
        cases = (
            (
                "same-basename-wrong-path",
                (*(path for path in required if path != buildah), "/tmp/buildah"),
                None,
            ),
            (
                "non-executable",
                required,
                {buildah: 0o644},
            ),
        )
        for name, paths, modes in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                layout = _layout(Path(directory), paths, modes=modes)
                with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                    assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_exact_capability_symlink_to_executable_is_accepted(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        backing = "/opt/streamscapetv/bin/buildah"
        paths = (*required, backing)
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                paths,
                symlinks={"/usr/bin/buildah": backing},
            )
            assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_required_executable_resolves_parent_symlink_components(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        redirected = tuple(
            path.replace("/usr/", "/safe/usr/", 1)
            if path.startswith("/usr/")
            else path
            for path in required
        )
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                (*redirected, "/usr"),
                symlinks={"/usr": "/safe/usr"},
            )
            assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_runtime_command_executable_must_exist_and_be_executable(self) -> None:
        for target in self.plan.targets:
            required = self._required_tool_paths(target.target_id)
            command = target.required_command[0]
            for name, paths, modes in (
                (
                    "missing",
                    tuple(path for path in required if path != command),
                    None,
                ),
                ("non-executable", required, {command: 0o644}),
            ):
                with (
                    self.subTest(target=target.target_id, case=name),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    layout = _layout(Path(directory), paths, modes=modes)
                    with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                        assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_forbidden_credential_path_resolves_parent_symlink_alias(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                (*required, "/root", "/safe/.docker/config.json"),
                symlinks={"/root": "/safe"},
            )
            with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_forbidden_path_symlink_cycle_fails_closed(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                (*required, "/root", "/safe"),
                symlinks={"/root": "/safe", "/safe": "/root"},
            )
            with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_noncanonical_symlink_target_fails_closed(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                (*required, "/root", "/safe/.docker/config.json"),
                symlinks={"/root": "//safe"},
            )
            with self.assertRaisesRegex(OciPublishError, "oci_layout_malformed"):
                assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_root_whiteouts_remove_lower_layer_capabilities(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        for whiteout in (".wh.usr", ".wh..wh..opq"):
            with (
                self.subTest(whiteout=whiteout),
                tempfile.TemporaryDirectory() as directory,
            ):
                layout = _layout(
                    Path(directory),
                    required,
                    additional_layers=((whiteout,),),
                )
                with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                    assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_whiteout_does_not_remove_same_layer_executable_replacement(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                required,
                additional_layers=((".wh.usr", *required),),
            )
            assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_later_non_directory_ancestor_shadows_lower_capabilities(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                required,
                additional_layers=(("usr",),),
            )
            with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_lower_non_directory_ancestor_blocks_later_capabilities(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        required = self._required_tool_paths(target.target_id)
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                ("usr",),
                additional_layers=(required,),
            )
            with self.assertRaisesRegex(OciPublishError, "oci_layout_malformed"):
                assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_flux_layer_inventory_streams_verified_blobs(self) -> None:
        target = self.plan.targets[0]
        required = self._required_tool_paths(target.target_id)
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(Path(directory), required)
            with patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("layer blobs must be streamed"),
            ):
                assert_filesystem_contract(ROOT, self.plan, target, layout)

    def test_null_healthcheck_requires_exact_absence_for_real_products(self) -> None:
        products = (
            ("StreamScapeTV/agent-state", "agent-state-image"),
            ("StreamScapeTV/flux", "flux-runner-images"),
            ("StreamScapeTV/iptv-backend", "iptv-backend-image"),
        )
        unexpected = {"Test": ["NONE"]}
        for repository, product_id in products:
            plan = resolve_plan(
                ROOT,
                PublishRequest(
                    repository=repository,
                    admitted_sha=SHA,
                    release_authority_sha=SHA,
                    product_id=product_id,
                    release_version="1.2.3",
                    source_trust="trusted-exact",
                ),
            )
            raw_targets = {
                row["target_id"]: row
                for row in self.contract["products"][product_id]["targets"]
            }
            publication = self.contract["publication_assertions"][product_id]
            for target in plan.targets:
                raw = raw_targets[target.target_id]
                exact = tuple(
                    publication[target.target_id]["required_executables"]
                )
                generic = tuple(
                    f"/usr/bin/{tool}"
                    for tool in raw["assertions"]["required_tools"]
                    if not exact
                )
                paths = tuple(raw["assertions"]["required_files"]) + exact + generic
                with (
                    self.subTest(product=product_id, target=target.target_id),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    layout = _layout(
                        Path(directory),
                        paths,
                        healthcheck=unexpected,
                    )
                    with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                        assert_filesystem_contract(ROOT, plan, target, layout)

    def test_real_flux_target_enforces_declared_exact_healthcheck(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        declared = {
            "test": ["CMD", "/usr/local/bin/runner-healthcheck"],
            "interval_nanoseconds": 30_000_000_000,
            "timeout_nanoseconds": 5_000_000_000,
            "start_period_nanoseconds": 10_000_000_000,
            "start_interval_nanoseconds": 1_000_000_000,
            "retries": 3,
        }
        image_healthcheck = {
            "Test": declared["test"],
            "Interval": declared["interval_nanoseconds"],
            "Timeout": declared["timeout_nanoseconds"],
            "StartPeriod": declared["start_period_nanoseconds"],
            "StartInterval": declared["start_interval_nanoseconds"],
            "Retries": declared["retries"],
        }
        required = self._required_tool_paths(target.target_id)
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            contract_root = self._mutated_contract_root(
                target.target_id, declared, temporary / "contract"
            )
            matching = _layout(
                temporary / "matching", required, healthcheck=image_healthcheck
            )
            assert_filesystem_contract(contract_root, self.plan, target, matching)

            mismatched_healthcheck = dict(image_healthcheck)
            mismatched_healthcheck["Retries"] = 4
            mismatched = _layout(
                temporary / "mismatched",
                required,
                healthcheck=mismatched_healthcheck,
            )
            with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                assert_filesystem_contract(
                    contract_root, self.plan, target, mismatched
                )

            missing = _layout(temporary / "missing-healthcheck", required)
            with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                assert_filesystem_contract(contract_root, self.plan, target, missing)

    def test_publication_assertion_inventory_fails_closed_on_schema_drift(self) -> None:
        target = self.plan.targets[0]
        required = self._required_tool_paths(target.target_id)
        for mutate in (
            lambda payload: payload["publication_assertions"][
                "flux-runner-images"
            ].pop("runner-mobile"),
            lambda payload: payload["publication_assertions"][
                "agent-state-image"
            ]["agent-state-api"].__setitem__("caller_command", "untrusted"),
            lambda payload: payload["publication_assertions"][
                "flux-runner-images"
            ]["runner-buildah"].__setitem__(
                "required_executables", ["usr/bin/buildah"]
            ),
            lambda payload: payload["publication_assertions"][
                "flux-runner-images"
            ]["runner-buildah"].__setitem__("required_executables", []),
            lambda payload: payload["publication_assertions"][
                "flux-runner-images"
            ]["runner-buildah"].__setitem__(
                "forbidden_paths", ["//var/run/docker.sock"]
            ),
            lambda payload: payload["publication_assertions"][
                "flux-runner-images"
            ]["runner-mobile"].__setitem__("healthcheck", {"test": ["CMD"]}),
        ):
            with tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory)
                payload = json.loads(
                    (ROOT / "contracts/oci-products.json").read_text(
                        encoding="utf-8"
                    )
                )
                mutate(payload)
                contract_path = temporary / "contracts/oci-products.json"
                contract_path.parent.mkdir()
                contract_path.write_text(json.dumps(payload), encoding="utf-8")
                layout = _layout(temporary / "layout", required)
                with self.assertRaisesRegex(OciPublishError, "invalid_contract"):
                    assert_filesystem_contract(temporary, self.plan, target, layout)

    def test_independent_readback_rejects_flux_remote_assertion_drift(self) -> None:
        target = next(
            target
            for target in self.plan.targets
            if target.target_id == "runner-buildah"
        )
        declared = {
            "test": ["CMD", "/usr/local/bin/runner-healthcheck"],
            "interval_nanoseconds": 30_000_000_000,
            "timeout_nanoseconds": 5_000_000_000,
            "start_period_nanoseconds": 10_000_000_000,
            "start_interval_nanoseconds": 1_000_000_000,
            "retries": 3,
        }
        expected_healthcheck = {
            "Test": declared["test"],
            "Interval": declared["interval_nanoseconds"],
            "Timeout": declared["timeout_nanoseconds"],
            "StartPeriod": declared["start_period_nanoseconds"],
            "StartInterval": declared["start_interval_nanoseconds"],
            "Retries": declared["retries"],
        }
        drifted_healthcheck = dict(expected_healthcheck)
        drifted_healthcheck["Retries"] = 4
        cases = (
            ("socket", "/var/run/docker.sock", None, None),
            (
                "credential",
                "/home/runner/.config/containers/auth.json",
                None,
                None,
            ),
            ("healthcheck", None, declared, drifted_healthcheck),
        )
        for name, residue, healthcheck_contract, remote_healthcheck in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory)
                contract_root = ROOT
                if healthcheck_contract is not None:
                    contract_root = self._mutated_contract_root(
                        target.target_id,
                        healthcheck_contract,
                        temporary / "contract",
                    )
                required = self._required_tool_paths(target.target_id)
                local_healthcheck = (
                    expected_healthcheck if healthcheck_contract is not None else None
                )
                local_layout = _layout(
                    temporary / "local",
                    required,
                    healthcheck=local_healthcheck,
                    target=target,
                )
                remote_paths = required + (() if residue is None else (residue,))
                remote_layout = _layout(
                    temporary / "remote",
                    remote_paths,
                    healthcheck=remote_healthcheck,
                    target=target,
                )
                local_summary = guards.inspect_layout(
                    local_layout, target, "validation"
                )
                remote_summary = guards.inspect_layout(
                    remote_layout, target, "readback"
                )
                environment = {
                    "RUNNER_TEMP": str(temporary / "runner-temp"),
                    "GITHUB_RUN_ID": "9917",
                    "GITHUB_RUN_ATTEMPT": "1",
                }
                narrowed_plan = replace(self.plan, targets=(target,))
                publication_root = guards.publication_state_root(environment)
                publication_root.mkdir(parents=True, mode=0o700)
                authfile = publication_root / "registry-auth.json"
                authfile.write_text("{}\n", encoding="utf-8")
                authfile.chmod(0o600)
                (publication_root / "publication.json").write_text(
                    json.dumps(
                        {
                            "build": {
                                "source_sha": narrowed_plan.admitted_sha,
                                "product_id": narrowed_plan.product_id,
                                "release_version": narrowed_plan.release_version,
                                "evidence_id": "b" * 64,
                            },
                            "targets": {
                                target.target_id: {
                                    "local": local_summary,
                                    "resolved_base_references": ["scratch"],
                                    "replayed": False,
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )

                def copy_remote(
                    _source: str, destination: str, _authfile: Path
                ) -> None:
                    destination_text = destination.removeprefix("oci:")
                    destination_path = Path(destination_text.rpartition(":")[0])
                    shutil.copytree(remote_layout, destination_path)

                with patch.object(
                    runtime, "_copy", side_effect=copy_remote
                ), patch.object(
                    guards,
                    "_inspect_remote_digest",
                    return_value=remote_summary["manifest_digest"],
                ):
                    with self.assertRaisesRegex(OciPublishError, "assertion_failed"):
                        guards.read_back(
                            narrowed_plan,
                            environment,
                            repository_root=contract_root,
                        )


if __name__ == "__main__":
    unittest.main()
