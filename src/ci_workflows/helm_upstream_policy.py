"""Central stable-origin policy for mirrored Helm upstream assets."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .helm_contract import NAME, PRODUCT_MANIFEST_PATH, SEMVER, bounded_path, require
from .helm_types import HelmValidationError


UPSTREAM_POLICY_PATH = Path("contracts/helm-upstream-policy.json")
CENTRAL_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_IDS = {
    "iptv-backend-chart",
    "agent-state-chart",
    "flux-github-actions-runner-chart",
}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_EXPECTED_KEYS = {
    "name",
    "upstream_repository",
    "upstream_tag",
    "upstream_commit",
    "license",
    "mirror_repository",
}


def _asset_rows(value: Any) -> tuple[tuple[str, str, str, str, str, str], ...]:
    require(isinstance(value, list), "invalid_upstream_policy")
    rows: list[tuple[str, str, str, str, str, str]] = []
    for item in value:
        require(
            isinstance(item, Mapping) and set(item) == _EXPECTED_KEYS,
            "invalid_upstream_policy",
        )
        name = item.get("name")
        upstream_repository = item.get("upstream_repository")
        upstream_tag = item.get("upstream_tag")
        upstream_commit = item.get("upstream_commit")
        license_id = item.get("license")
        mirror_repository = item.get("mirror_repository")
        require(
            isinstance(name, str) and NAME.fullmatch(name) is not None,
            "invalid_upstream_policy",
        )
        require(
            upstream_repository == "https://github.com/actions/actions-runner-controller",
            "invalid_upstream_policy",
        )
        require(
            isinstance(upstream_tag, str) and SEMVER.fullmatch(upstream_tag) is not None,
            "invalid_upstream_policy",
        )
        require(
            isinstance(upstream_commit, str)
            and FULL_SHA.fullmatch(upstream_commit) is not None,
            "invalid_upstream_policy",
        )
        require(license_id == "Apache-2.0", "invalid_upstream_policy")
        require(
            isinstance(mirror_repository, str)
            and mirror_repository.startswith("oci://git.faruqi.dev/")
            and "@" not in mirror_repository,
            "invalid_upstream_policy",
        )
        rows.append(
            (
                name,
                upstream_repository,
                upstream_tag,
                upstream_commit,
                license_id,
                mirror_repository,
            )
        )
    require(rows == sorted(set(rows)), "invalid_upstream_policy")
    return tuple(rows)


def load_upstream_policy(root: Path = CENTRAL_ROOT) -> Mapping[str, Any]:
    try:
        payload = json.loads((root / UPSTREAM_POLICY_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError("invalid_upstream_policy") from error
    require(
        isinstance(payload, Mapping)
        and set(payload) == {"schema_version", "products"}
        and payload.get("schema_version") == 1,
        "invalid_upstream_policy",
    )
    products = payload.get("products")
    require(
        isinstance(products, Mapping) and set(products) == PRODUCT_IDS,
        "invalid_upstream_policy",
    )
    for product_id in sorted(PRODUCT_IDS):
        rows = _asset_rows(products[product_id])
        if product_id == "flux-github-actions-runner-chart":
            require(
                tuple(row[0] for row in rows)
                == ("gha-runner-scale-set", "gha-runner-scale-set-controller"),
                "invalid_upstream_policy",
            )
            require(
                all(row[2] == "0.14.2" for row in rows)
                and len({row[3] for row in rows}) == 1,
                "invalid_upstream_policy",
            )
        else:
            require(not rows, "invalid_upstream_policy")
    return payload


def enforce_upstream_policy(
    source_root: Path,
    product_id: str,
    policy: Mapping[str, Any],
) -> None:
    products = policy.get("products")
    require(isinstance(products, Mapping), "invalid_upstream_policy")
    require(product_id in products, "unsupported_product")
    expected = _asset_rows(products[product_id])

    manifest_path = bounded_path(
        source_root,
        PRODUCT_MANIFEST_PATH.as_posix(),
        "upstream_provenance_invalid",
    )
    require(
        manifest_path.is_file() and not manifest_path.is_symlink(),
        "upstream_provenance_invalid",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError("upstream_provenance_invalid") from error
    require(isinstance(manifest, Mapping), "upstream_provenance_invalid")
    raw_assets = manifest.get("upstream_assets")
    require(isinstance(raw_assets, list), "upstream_provenance_invalid")
    actual: list[tuple[str, str, str, str, str, str]] = []
    for item in raw_assets:
        require(isinstance(item, Mapping), "upstream_provenance_invalid")
        actual.append(
            (
                str(item.get("name", "")),
                str(item.get("upstream_repository", "")),
                str(item.get("upstream_tag", "")),
                str(item.get("upstream_commit", "")),
                str(item.get("license", "")),
                str(item.get("mirror_repository", "")),
            )
        )
    require(tuple(actual) == expected, "upstream_policy_mismatch")


__all__ = [
    "UPSTREAM_POLICY_PATH",
    "enforce_upstream_policy",
    "load_upstream_policy",
]
