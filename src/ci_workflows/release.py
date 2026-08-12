"""Thin command surface for issue-#19 release orchestration."""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from .release_binding import image_reference_bundle
from .release_contract import resolve_public_release, resolve_release_plan, validate_request_id
from .release_evidence import (
    chart_publication_evidence,
    evidence_json,
    image_publication_evidence,
)
from .release_github import GitHubReleaseAPI, desired_release, ensure_github_release
from .release_handoff import flux_handoff_json, validate_flux_handoff_payload
from .release_manifest import (
    canonical_json,
    publication_identity,
    publication_progress,
    release_manifest_json,
    sha256_text,
)
from .release_types import ReleaseError, ReleasePlan


OUTPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BUILD_TIER = {"tiny": 0, "small": 1, "medium": 2, "high": 3}
FLUX_REPOSITORY = "StreamScapeTV/flux"
FLUX_HANDOFF_EVENT = "release-selection-review"


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def resolve(
    root: Path,
    *,
    release_contract: str,
    repository: str,
    admitted_sha: str,
    release_tag: str,
    release_version: str,
    request_id: str,
    target_id: str = "",
) -> tuple[ReleasePlan, dict[str, str]]:
    """Resolve one fixed registered public release request."""
    return resolve_public_release(
        root,
        release_contract=release_contract,
        repository=repository,
        admitted_sha=admitted_sha,
        release_tag=release_tag,
        release_version=release_version,
        request_id=request_id,
        target_id=target_id,
    )


def manifest(
    *,
    root: Path,
    plan: ReleasePlan,
    release_version: str,
    source_sha: str,
    tag_object_sha: str,
    tag_commit_sha: str,
    source_timestamp: str,
    workflow_sha: str,
    image: Any,
    chart: Any,
) -> tuple[str, str]:
    """Render the deterministic immutable release manifest."""
    return release_manifest_json(
        root=root,
        plan=plan,
        release_version=release_version,
        source_sha=source_sha,
        tag_object_sha=tag_object_sha,
        tag_commit_sha=tag_commit_sha,
        source_timestamp=source_timestamp,
        workflow_sha=workflow_sha,
        image=image,
        chart=chart,
    )


def _emit(values: Mapping[str, str]) -> None:
    normalized = dict(values)
    for key, value in normalized.items():
        if OUTPUT_NAME.fullmatch(key) is None or "\n" in value or "\r" in value:
            raise ReleaseError("output_rejected")
    destination = os.environ.get("GITHUB_OUTPUT", "").strip()
    if destination:
        try:
            with Path(destination).open("a", encoding="utf-8") as handle:
                for key, value in normalized.items():
                    handle.write(f"{key}={value}\n")
        except OSError as error:
            raise ReleaseError("output_write_failed") from error
        return
    print(json.dumps(normalized, sort_keys=True))


def _root(value: str) -> Path:
    root = Path(value).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ReleaseError("root_rejected")
    return root


def _identity(prefix: str, args: argparse.Namespace):
    return publication_identity(
        product_id=getattr(args, f"{prefix}_product_id"),
        kind="oci-image" if prefix == "image" else "helm-chart",
        digest=getattr(args, f"{prefix}_digest"),
        digests_json=getattr(args, f"{prefix}_digests_json"),
        immutable_references_json=getattr(args, f"{prefix}_references_json"),
        evidence_json=getattr(args, f"{prefix}_evidence_json"),
    )


def _plan(args: argparse.Namespace) -> int:
    root = _root(args.root)
    plan, request = resolve(
        root,
        release_contract=args.release_contract,
        repository=args.repository,
        admitted_sha=args.admitted_sha,
        release_tag=args.release_tag,
        release_version=args.release_version,
        request_id=args.request_id,
        target_id=args.target_id,
    )
    _emit(
        {
            "plan_json": canonical_json(plan.as_dict()),
            "release_id": plan.release_id,
            "release_contract": request["release_contract"],
            "release_tag": request["release_tag"],
            "release_version": request["release_version"],
            "admitted_sha": request["admitted_sha"],
            "request_id": request["request_id"],
            "target_id": request["target_id"],
            "image_product_id": plan.image_product_id,
            "chart_product_id": plan.chart_product_id,
            "chart_requires_image_identity": (
                "true" if plan.chart_requires_image_identity else "false"
            ),
            "handoff_kind": plan.handoff_kind,
            "handoff_target_repository": plan.handoff_target_repository,
        }
    )
    return 0


def _runner(value: str) -> tuple[str, ...]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ReleaseError("runner_plan_rejected") from error
    require(
        isinstance(payload, list)
        and 3 <= len(payload) <= 4
        and all(isinstance(item, str) and item for item in payload),
        "runner_plan_rejected",
    )
    labels = tuple(payload)
    require(labels[:2] == ("linux", "amd64"), "runner_plan_rejected")
    if labels == ("linux", "amd64", "general"):
        return labels
    require(
        len(labels) == 4
        and labels[2] == "buildah"
        and labels[3] in BUILD_TIER,
        "runner_plan_rejected",
    )
    return labels


def _runner_plan(args: argparse.Namespace) -> int:
    image = _runner(args.image_runs_on_json)
    chart = _runner(args.chart_runs_on_json)
    candidates = [value for value in (image, chart) if value[2] == "buildah"]
    if not candidates:
        selected = ("linux", "amd64", "general")
    else:
        tier = max(candidates, key=lambda value: BUILD_TIER[value[3]])[3]
        selected = ("linux", "amd64", "buildah", tier)
    _emit({"runs_on_json": canonical_json(list(selected))})
    return 0


def _bindings(args: argparse.Namespace) -> int:
    digests, digest_references, bundle, selection = image_reference_bundle(
        image_digest_json=args.image_digest_json,
        immutable_references_json=args.immutable_references_json,
        expected_source_sha=args.expected_source_sha,
        expected_release_version=args.expected_release_version,
    )
    selection = selection or {}
    required_image_references = [
        digest_references[target] for target in sorted(digest_references)
    ]
    _emit(
        {
            "image_digests_json": canonical_json(digests),
            "digest_references_json": canonical_json(digest_references),
            "required_image_references_json": canonical_json(
                required_image_references
            ),
            "image_references_json": bundle,
            "canary_id": selection.get("canary_id", ""),
            "previous_known_good": selection.get("previous_known_good", ""),
            "rollback_id": selection.get("rollback_id", ""),
        }
    )
    return 0


def _evidence(args: argparse.Namespace) -> int:
    image = image_publication_evidence(
        result=args.image_result,
        image_digest_json=args.image_digest_json,
        platform_digests_json=args.platform_digests_json,
        immutable_references_json=args.immutable_references_json,
    )
    chart = chart_publication_evidence(
        result=args.chart_result,
        immutable_references_json=args.chart_references_json,
    )
    _emit(
        {
            "image_evidence_json": evidence_json(image),
            "chart_evidence_json": evidence_json(chart),
        }
    )
    return 0


def _verify(args: argparse.Namespace) -> int:
    root = _root(args.root)
    plan = resolve_release_plan(root, args.release_id, args.repository)
    image = _identity("image", args)
    chart = _identity("chart", args)
    if image.product_id != plan.image_product_id or image.kind != "oci-image":
        raise ReleaseError("image_identity_mismatch")
    if chart.product_id != plan.chart_product_id or chart.kind != "helm-chart":
        raise ReleaseError("chart_identity_mismatch")
    combined = {
        "image": {
            "product_id": image.product_id,
            "digests": image.digests,
            "immutable_references": list(image.immutable_references),
        },
        "chart": {
            "product_id": chart.product_id,
            "digests": chart.digests,
            "immutable_references": list(chart.immutable_references),
        },
    }
    _emit(
        {
            "image_digest": image.digest,
            "image_digests_json": canonical_json(image.digests),
            "image_references_json": canonical_json(
                list(image.immutable_references)
            ),
            "image_evidence_json": canonical_json(image.evidence),
            "chart_digest": chart.digest,
            "chart_digests_json": canonical_json(chart.digests),
            "chart_references_json": canonical_json(
                list(chart.immutable_references)
            ),
            "chart_evidence_json": canonical_json(chart.evidence),
            "immutable_references_json": canonical_json(combined),
            "result": "success",
        }
    )
    return 0


def _manifest(args: argparse.Namespace) -> int:
    root = _root(args.root)
    plan = resolve_release_plan(root, args.release_id, args.repository)
    image = _identity("image", args)
    chart = _identity("chart", args)
    rendered, digest = manifest(
        root=root,
        plan=plan,
        release_version=args.release_version,
        source_sha=args.source_sha,
        tag_object_sha=args.tag_object_sha,
        tag_commit_sha=args.tag_commit_sha,
        source_timestamp=args.source_timestamp,
        workflow_sha=args.workflow_sha,
        image=image,
        chart=chart,
    )
    _emit({"manifest_json": rendered, "manifest_sha256": digest})
    return 0


def _github_release(args: argparse.Namespace) -> int:
    root = _root(args.root)
    plan = resolve_release_plan(root, args.release_id, args.repository)
    desired = desired_release(
        plan=plan,
        release_tag=args.release_tag,
        release_version=args.release_version,
        source_sha=args.source_sha,
        manifest_json=args.manifest_json,
        manifest_sha256=args.manifest_sha256,
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    api = GitHubReleaseAPI(args.repository, token)
    url, state = ensure_github_release(api, desired)
    _emit({"github_release_url": url, "github_release_state": state})
    return 0


def _handoff(args: argparse.Namespace) -> int:
    root = _root(args.root)
    plan = resolve_release_plan(root, args.release_id, args.repository)
    image = _identity("image", args)
    chart = _identity("chart", args)
    rendered, digest = flux_handoff_json(
        plan=plan,
        release_version=args.release_version,
        source_sha=args.source_sha,
        release_manifest_sha256=args.manifest_sha256,
        github_release_url=args.github_release_url,
        image=image,
        chart=chart,
        canary_id=args.canary_id,
        previous_known_good=args.previous_known_good,
        rollback_id=args.rollback_id,
    )
    _emit({"flux_handoff_json": rendered, "flux_handoff_sha256": digest})
    return 0


def _dispatch_handoff(args: argparse.Namespace) -> int:
    root = _root(args.root)
    request_id = validate_request_id(args.request_id)
    try:
        payload = json.loads(args.flux_handoff_json)
    except json.JSONDecodeError as error:
        raise ReleaseError("handoff_json_rejected") from error
    require(isinstance(payload, dict), "handoff_json_rejected")
    canonical = canonical_json(payload)
    require(
        sha256_text(canonical) == args.flux_handoff_sha256,
        "handoff_digest_mismatch",
    )
    payload = validate_flux_handoff_payload(payload)
    plan = resolve_release_plan(
        root,
        str(payload["release_id"]),
        str(payload["producer_repository"]),
    )
    product_ids = {
        str(product["kind"]): str(product["product_id"])
        for product in payload["products"]
    }
    require(
        product_ids.get("oci-image") == plan.image_product_id
        and product_ids.get("helm-chart") == plan.chart_product_id,
        "handoff_product_mismatch",
    )
    token = os.environ.get("FLUX_HANDOFF_TOKEN", "")
    require(bool(token), "flux_handoff_token_missing")
    body = canonical_json(
        {
            "event_type": FLUX_HANDOFF_EVENT,
            "client_payload": {
                "request_id": request_id,
                "handoff_sha256": args.flux_handoff_sha256,
                "handoff": payload,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{FLUX_REPOSITORY}/dispatches",
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "StreamScapeTV-ci-workflows-release-handoff",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            require(response.status == 204, "flux_handoff_dispatch_failed")
            response.read(1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ReleaseError("flux_handoff_dispatch_failed") from error
    _emit({"handoff_state": "review-requested", "request_id": request_id})
    return 0


def _progress(args: argparse.Namespace) -> int:
    state = publication_progress(
        image_result=args.image_result, chart_result=args.chart_result
    )
    _emit({"publication_progress": state})
    return 0


def _add_identity(parser: argparse.ArgumentParser, prefix: str) -> None:
    parser.add_argument(f"--{prefix}-product-id", required=True)
    parser.add_argument(f"--{prefix}-digest", default="")
    parser.add_argument(f"--{prefix}-digests-json", default="")
    parser.add_argument(f"--{prefix}-references-json", required=True)
    parser.add_argument(f"--{prefix}-evidence-json", default="{}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release")
    parser.add_argument("--root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--release-contract", required=True)
    plan.add_argument("--repository", required=True)
    plan.add_argument("--admitted-sha", required=True)
    plan.add_argument("--release-tag", required=True)
    plan.add_argument("--release-version", required=True)
    plan.add_argument("--request-id", required=True)
    plan.add_argument("--target-id", default="")
    plan.set_defaults(handler=_plan)

    runner = subparsers.add_parser("runner-plan")
    runner.add_argument("--image-runs-on-json", required=True)
    runner.add_argument("--chart-runs-on-json", required=True)
    runner.set_defaults(handler=_runner_plan)

    bindings = subparsers.add_parser("image-bindings")
    bindings.add_argument("--image-digest-json", required=True)
    bindings.add_argument("--immutable-references-json", required=True)
    bindings.add_argument("--expected-source-sha", required=True)
    bindings.add_argument("--expected-release-version", required=True)
    bindings.set_defaults(handler=_bindings)

    evidence = subparsers.add_parser("evidence")
    evidence.add_argument("--image-result", required=True)
    evidence.add_argument("--image-digest-json", required=True)
    evidence.add_argument("--platform-digests-json", required=True)
    evidence.add_argument("--immutable-references-json", required=True)
    evidence.add_argument("--chart-result", required=True)
    evidence.add_argument("--chart-references-json", required=True)
    evidence.set_defaults(handler=_evidence)

    verify = subparsers.add_parser("verify-publications")
    verify.add_argument("--release-id", required=True)
    verify.add_argument("--repository", required=True)
    _add_identity(verify, "image")
    _add_identity(verify, "chart")
    verify.set_defaults(handler=_verify)

    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("--release-id", required=True)
    manifest_parser.add_argument("--repository", required=True)
    manifest_parser.add_argument("--release-version", required=True)
    manifest_parser.add_argument("--source-sha", required=True)
    manifest_parser.add_argument("--tag-object-sha", required=True)
    manifest_parser.add_argument("--tag-commit-sha", required=True)
    manifest_parser.add_argument("--source-timestamp", required=True)
    manifest_parser.add_argument("--workflow-sha", required=True)
    _add_identity(manifest_parser, "image")
    _add_identity(manifest_parser, "chart")
    manifest_parser.set_defaults(handler=_manifest)

    github_release = subparsers.add_parser("github-release")
    github_release.add_argument("--release-id", required=True)
    github_release.add_argument("--repository", required=True)
    github_release.add_argument("--release-tag", required=True)
    github_release.add_argument("--release-version", required=True)
    github_release.add_argument("--source-sha", required=True)
    github_release.add_argument("--manifest-json", required=True)
    github_release.add_argument("--manifest-sha256", required=True)
    github_release.set_defaults(handler=_github_release)

    handoff = subparsers.add_parser("handoff")
    handoff.add_argument("--release-id", required=True)
    handoff.add_argument("--repository", required=True)
    handoff.add_argument("--release-version", required=True)
    handoff.add_argument("--source-sha", required=True)
    handoff.add_argument("--manifest-sha256", required=True)
    handoff.add_argument("--github-release-url", required=True)
    handoff.add_argument("--canary-id", default="")
    handoff.add_argument("--previous-known-good", default="")
    handoff.add_argument("--rollback-id", default="")
    _add_identity(handoff, "image")
    _add_identity(handoff, "chart")
    handoff.set_defaults(handler=_handoff)

    dispatch = subparsers.add_parser("dispatch-handoff")
    dispatch.add_argument("--request-id", required=True)
    dispatch.add_argument("--flux-handoff-json", required=True)
    dispatch.add_argument("--flux-handoff-sha256", required=True)
    dispatch.set_defaults(handler=_dispatch_handoff)

    progress = subparsers.add_parser("progress")
    progress.add_argument("--image-result", required=True)
    progress.add_argument("--chart-result", required=True)
    progress.set_defaults(handler=_progress)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ReleaseError as error:
        print(error.code, file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
