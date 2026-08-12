"""Thin command surface for issue-#19 release orchestration."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Sequence

from .release_binding import image_reference_bundle
from .release_contract import resolve_release_plan
from .release_github import GitHubReleaseAPI, desired_release, ensure_github_release
from .release_handoff import flux_handoff_json
from .release_manifest import canonical_json, publication_identity, publication_progress, release_manifest_json
from .release_types import ReleaseError


OUTPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _emit(values: dict[str, str]) -> None:
    for key, value in values.items():
        if OUTPUT_NAME.fullmatch(key) is None or "\n" in value or "\r" in value:
            raise ReleaseError("output_rejected")
    destination = os.environ.get("GITHUB_OUTPUT", "").strip()
    if destination:
        try:
            with Path(destination).open("a", encoding="utf-8") as handle:
                for key, value in values.items():
                    handle.write(f"{key}={value}\n")
        except OSError as error:
            raise ReleaseError("output_write_failed") from error
        return
    print(json.dumps(values, sort_keys=True))


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
    plan = resolve_release_plan(root, args.release_id, args.repository)
    _emit(
        {
            "plan_json": canonical_json(plan.as_dict()),
            "image_product_id": plan.image_product_id,
            "chart_product_id": plan.chart_product_id,
            "chart_requires_image_identity": "true" if plan.chart_requires_image_identity else "false",
            "handoff_kind": plan.handoff_kind,
            "handoff_target_repository": plan.handoff_target_repository,
        }
    )
    return 0


def _bindings(args: argparse.Namespace) -> int:
    digest_references, bundle = image_reference_bundle(
        repositories_json=args.repositories_json,
        manifest_digests_json=args.manifest_digests_json,
        version_references_json=args.version_references_json,
        source_references_json=args.source_references_json,
    )
    _emit(
        {
            "digest_references_json": canonical_json(digest_references),
            "image_references_json": bundle,
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
    _emit(
        {
            "image_digest": image.digest,
            "image_digests_json": canonical_json(image.digests),
            "image_references_json": canonical_json(list(image.immutable_references)),
            "chart_digest": chart.digest,
            "chart_digests_json": canonical_json(chart.digests),
            "chart_references_json": canonical_json(list(chart.immutable_references)),
            "result": "success",
        }
    )
    return 0


def _manifest(args: argparse.Namespace) -> int:
    root = _root(args.root)
    plan = resolve_release_plan(root, args.release_id, args.repository)
    image = _identity("image", args)
    chart = _identity("chart", args)
    rendered, digest = release_manifest_json(
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
    )
    _emit({"flux_handoff_json": rendered, "flux_handoff_sha256": digest})
    return 0


def _progress(args: argparse.Namespace) -> int:
    state = publication_progress(image_result=args.image_result, chart_result=args.chart_result)
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
    plan.add_argument("--release-id", required=True)
    plan.add_argument("--repository", required=True)
    plan.set_defaults(handler=_plan)

    bindings = subparsers.add_parser("image-bindings")
    bindings.add_argument("--repositories-json", required=True)
    bindings.add_argument("--manifest-digests-json", required=True)
    bindings.add_argument("--version-references-json", required=True)
    bindings.add_argument("--source-references-json", required=True)
    bindings.set_defaults(handler=_bindings)

    verify = subparsers.add_parser("verify-publications")
    verify.add_argument("--release-id", required=True)
    verify.add_argument("--repository", required=True)
    _add_identity(verify, "image")
    _add_identity(verify, "chart")
    verify.set_defaults(handler=_verify)

    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--release-id", required=True)
    manifest.add_argument("--repository", required=True)
    manifest.add_argument("--release-version", required=True)
    manifest.add_argument("--source-sha", required=True)
    manifest.add_argument("--tag-object-sha", required=True)
    manifest.add_argument("--tag-commit-sha", required=True)
    manifest.add_argument("--source-timestamp", required=True)
    manifest.add_argument("--workflow-sha", required=True)
    _add_identity(manifest, "image")
    _add_identity(manifest, "chart")
    manifest.set_defaults(handler=_manifest)

    release = subparsers.add_parser("github-release")
    release.add_argument("--release-id", required=True)
    release.add_argument("--repository", required=True)
    release.add_argument("--release-version", required=True)
    release.add_argument("--source-sha", required=True)
    release.add_argument("--manifest-json", required=True)
    release.add_argument("--manifest-sha256", required=True)
    release.set_defaults(handler=_github_release)

    handoff = subparsers.add_parser("handoff")
    handoff.add_argument("--release-id", required=True)
    handoff.add_argument("--repository", required=True)
    handoff.add_argument("--release-version", required=True)
    handoff.add_argument("--source-sha", required=True)
    handoff.add_argument("--manifest-sha256", required=True)
    handoff.add_argument("--github-release-url", required=True)
    _add_identity(handoff, "image")
    _add_identity(handoff, "chart")
    handoff.set_defaults(handler=_handoff)

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
