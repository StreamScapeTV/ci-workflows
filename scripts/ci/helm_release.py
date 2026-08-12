#!/usr/bin/env python3
"""Helm release-only adapter for exact OCI evidence binding and remote digest proof."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from ci_workflows.ciw_helm import _failure_outputs, _source_root, _state_root
from ci_workflows.ciw_types import write_command_file
from ci_workflows.helm_archive import finalize_validation_archive
from ci_workflows.helm_contract import (
    load_helm_contract,
    load_helm_publication_contract,
    request_from_environment,
    require,
)
from ci_workflows.helm_dependency_policy import resolve_validation_plan
from ci_workflows.helm_registry import publish_and_read_back
from ci_workflows.helm_release import (
    load_release_bindings,
    parse_oci_publication_evidence,
    remote_chart_manifest_digest,
    validate_and_package_release,
)
from ci_workflows.helm_types import HelmValidationError


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _release_mode(environment: Mapping[str, str]) -> str:
    mode = environment.get("INPUT_RELEASE_MODE", "").strip()
    require(mode in {"tag-push", "existing-tag"}, "release_mode_invalid")
    return mode


def _inputs(
    root: Path,
    environment: Mapping[str, str],
):
    request = request_from_environment(environment)
    require(request.release_version is not None, "release_version_mismatch")
    release_mode = _release_mode(environment)
    release_contract = load_release_bindings(root)
    references = parse_oci_publication_evidence(
        environment.get("INPUT_IMAGE_DIGEST", ""),
        environment.get("INPUT_IMMUTABLE_REFERENCES_JSON", ""),
        request.product_id,
        release_contract,
        request.admitted_sha,
        request.release_version,
    )
    return request, release_mode, release_contract, references


def _validate_input(
    root: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    request, release_mode, release_contract, references = _inputs(root, environment)
    product = release_contract["products"][request.product_id]
    return {
        "result": "validated",
        "product_id": request.product_id,
        "release_mode": release_mode,
        "oci_product_id": product["oci_product_id"] or "",
        "image_references_json": json.dumps(
            list(references),
            separators=(",", ":"),
        ),
    }


def _execute(
    root: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    load_helm_publication_contract(root)
    request, _, release_contract, references = _inputs(root, environment)
    require(request.source_trust == "trusted-exact", "source_trust_rejected")

    source_root = _source_root(root, environment, "source")
    state_root = _state_root(root, environment)
    plan = resolve_validation_plan(
        source_root,
        load_helm_contract(root),
        request,
        contract_root=root,
    )
    product_release_contract = release_contract["products"][request.product_id]
    validation = validate_and_package_release(
        source_root,
        state_root,
        plan,
        request.admitted_sha,
        references,
        product_release_contract,
        environment,
    )
    validation = finalize_validation_archive(
        validation,
        plan.product.chart_name,
    )
    publication = publish_and_read_back(
        source_root,
        state_root,
        plan,
        validation,
        environment,
    )
    chart_reference = (
        f"{plan.product.registry_repository}/{plan.product.chart_name}"
    )
    chart_digest = remote_chart_manifest_digest(
        source_root,
        state_root,
        chart_reference=chart_reference,
        release_version=request.release_version,
        expected_package_sha256=validation.package_sha256,
        inherited=environment,
    )
    immutable_references_json = json.dumps(
        {
            "admitted_sha": request.admitted_sha,
            "chart": f"{chart_reference}:{request.release_version}",
            "chart_digest": chart_digest,
            "package_sha256": validation.package_sha256,
            "product_id": request.product_id,
            "release_version": request.release_version,
            "required_image_references": list(references),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    values = publication.output_values()
    values.update(
        {
            "artifact_exception_used": "false",
            "chart_digest": chart_digest,
            "immutable_references_json": immutable_references_json,
            "failure_code": "",
            "runner_profile": "buildah-tiny",
            "workspace_profile": "minimal",
            "timeout_minutes": "90",
            "source_trust": request.source_trust,
        }
    )
    return values


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"validate-input", "execute"}:
        sys.stderr.write("usage: helm_release.py validate-input|execute\n")
        return 2
    root = _root()
    try:
        values = (
            _validate_input(root, os.environ)
            if args[0] == "validate-input"
            else _execute(root, os.environ)
        )
        target = os.environ.get("GITHUB_OUTPUT", "")
        if target:
            write_command_file(Path(target), values)
        else:
            sys.stdout.write(json.dumps(values, sort_keys=True) + "\n")
        return 0
    except HelmValidationError as error:
        _failure_outputs(os.environ, error.code, "publish")
        sys.stderr.write(f"helm publication failed: {error.code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
