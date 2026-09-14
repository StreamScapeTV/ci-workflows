#!/usr/bin/env python3
"""Central validation/publication helpers for GitHub-tagged Swift binary packages."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
from urllib.parse import unquote, urlsplit

SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
SHA40_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
REPOSITORY_RE = re.compile(r"StreamScapeTV/[A-Za-z0-9_.-]+")
PACKAGE_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
SAFE_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,255}")
FIXED_WRAPPER = Path("scripts/ci/run-swiftpm-binary.sh")
REGISTRY_HOST = "git.faruqi.dev"
REGISTRY_PREFIX = ("api", "packages", "mimranfaruqi", "generic")


class ContractError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ContractError(message)


def read_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read JSON {path}: {exc}")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def normalize_version(value: str) -> str:
    if value.startswith("refs/tags/"):
        value = value[len("refs/tags/") :]
    if not SEMVER_RE.fullmatch(value):
        fail("Swift binary publication requires a plain semantic Git tag")
    return value


def github_package_url(repository: str) -> str:
    if not REPOSITORY_RE.fullmatch(repository):
        fail("Swift binary publication requires one StreamScapeTV repository")
    return f"https://github.com/{repository}.git"


def require_regular_tracked(root: Path, relative: Path, description: str) -> Path:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        fail(f"{description} must be a regular non-symlink file: {relative.as_posix()}")
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative.as_posix()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        fail(f"{description} must be tracked by the tagged GitHub source")
    return path


def validate_artifact_url(url: str, version: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != REGISTRY_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        fail("binary target URL escaped the approved private Generic Package host")
    segments = tuple(unquote(part) for part in parsed.path.split("/") if part)
    if len(segments) != 7 or segments[:4] != REGISTRY_PREFIX:
        fail("binary target URL must use the approved Generic Package path")
    package_name, url_version, filename = segments[4:]
    if not PACKAGE_SLUG_RE.fullmatch(package_name):
        fail("binary target URL contains an invalid Generic Package name")
    if url_version != version:
        fail("binary target URL version must exactly match VERSION and the Git tag")
    if not SAFE_FILE_RE.fullmatch(filename) or filename in {".", ".."}:
        fail("binary target URL contains an unsafe filename")
    return package_name, filename


def dump_package(root: Path) -> dict:
    result = subprocess.run(
        ["swift", "package", "dump-package", "--package-path", str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "swift package dump-package failed"
        fail(message)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"swift package dump-package returned invalid JSON: {exc}")
    if not isinstance(value, dict):
        fail("swift package dump-package must return one JSON object")
    return value


def binary_targets_from_dump(value: dict, version: str) -> list[dict[str, str]]:
    targets = value.get("targets")
    if not isinstance(targets, list):
        fail("Package.swift dump is missing targets")
    result: list[dict[str, str]] = []
    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    for target in targets:
        if not isinstance(target, dict) or target.get("type") != "binary":
            continue
        name = target.get("name")
        url = target.get("url")
        checksum = target.get("checksum")
        if not isinstance(name, str) or not name or name in seen_names:
            fail("binary target names must be unique non-empty strings")
        if not isinstance(url, str) or not url:
            fail(f"binary target {name} must use a remote URL, not a local path")
        if not isinstance(checksum, str) or not SHA256_RE.fullmatch(checksum):
            fail(f"binary target {name} must have one lowercase SHA-256 checksum")
        _, filename = validate_artifact_url(url, version)
        if url in seen_urls:
            fail("binary target URLs must be unique")
        seen_names.add(name)
        seen_urls.add(url)
        result.append({"target": name, "url": url, "checksum": checksum, "filename": filename})
    if not result:
        fail("Package.swift must declare at least one remote binary target")
    result.sort(key=lambda item: item["target"])
    return result


def library_products_from_dump(value: dict) -> list[str]:
    products = value.get("products")
    if not isinstance(products, list):
        fail("Package.swift dump is missing products")
    names: list[str] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        name = product.get("name")
        product_type = product.get("type")
        is_library = isinstance(product_type, dict) and "library" in product_type
        if isinstance(name, str) and name and is_library:
            names.append(name)
    if not names:
        fail("Swift binary package must expose at least one library product")
    if len(names) != len(set(names)):
        fail("Swift package product names must be unique")
    return sorted(names)


def command_source_contract(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    if not root.is_dir():
        fail("tagged source root is missing")
    version = normalize_version(args.version)
    package_url = github_package_url(args.repository)
    if not SHA40_RE.fullmatch(args.source_sha):
        fail("source SHA must be one lowercase 40-character Git commit")

    version_path = require_regular_tracked(root, Path("VERSION"), "VERSION")
    package_path = require_regular_tracked(root, Path("Package.swift"), "Package.swift")
    require_regular_tracked(root, FIXED_WRAPPER, "Swift binary pipeline wrapper")

    raw_version = version_path.read_text(encoding="utf-8")
    if raw_version.strip() != version:
        fail("VERSION must exactly match the requested GitHub semantic tag")
    if not raw_version.strip() or len(raw_version.splitlines()) > 1:
        fail("VERSION must contain one semantic version value")

    manifest_text = package_path.read_text(encoding="utf-8")
    lower_manifest = manifest_text.lower()
    for forbidden in ("password=", "token=", "authorization:", "x-access-token", "@git.faruqi.dev"):
        if forbidden in lower_manifest:
            fail("Package.swift appears to embed credentials")

    package_dump = dump_package(root)
    targets = binary_targets_from_dump(package_dump, version)
    products = library_products_from_dump(package_dump)
    contract = {
        "schemaVersion": 1,
        "repository": args.repository,
        "packageURL": package_url,
        "version": version,
        "packageRevision": args.source_sha,
        "binaryTargets": targets,
        "libraryProducts": products,
    }
    write_json(Path(args.output), contract)
    if args.dump_output:
        write_json(Path(args.dump_output), package_dump)


def safe_evidence_file(evidence_root: Path, relative_value: str) -> Path:
    relative = Path(relative_value)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        fail("publication plan artifact file must be a safe relative path")
    if relative.parts[0] != "artifacts":
        fail("publication plan artifact files must live under artifacts/")
    candidate = evidence_root / relative
    current = evidence_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            fail("publication artifact path must not traverse symlinks")
    resolved_root = evidence_root.resolve()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        fail(f"publication artifact file is missing: {relative_value}: {exc}")
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        fail("publication artifact escaped the evidence directory")
    if not resolved.is_file() or resolved.is_symlink():
        fail("publication artifacts must be regular non-symlink files")
    return resolved


def command_validate_plan(args: argparse.Namespace) -> None:
    contract = read_json(Path(args.contract))
    plan = read_json(Path(args.plan))
    if not isinstance(contract, dict) or contract.get("schemaVersion") != 1:
        fail("invalid Swift binary source contract")
    if not isinstance(plan, dict):
        fail("Swift binary prepare result must be one JSON object")
    if set(plan) != {"schemaVersion", "packageURL", "version", "artifacts"}:
        fail("Swift binary prepare result has an unexpected schema")
    if plan.get("schemaVersion") != 1:
        fail("unsupported Swift binary prepare result schema")
    if plan.get("packageURL") != contract.get("packageURL") or plan.get("version") != contract.get("version"):
        fail("Swift binary prepare result does not match the GitHub tag identity")

    version = contract.get("version")
    if not isinstance(version, str):
        fail("invalid source contract version")
    expected_list = contract.get("binaryTargets")
    if not isinstance(expected_list, list) or not expected_list:
        fail("source contract contains no binary targets")
    expected = {item["target"]: item for item in expected_list if isinstance(item, dict) and isinstance(item.get("target"), str)}
    if len(expected) != len(expected_list):
        fail("source contract binary targets are malformed")

    artifacts = plan.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        fail("Swift binary prepare result contains no artifacts")
    evidence_root = Path(args.evidence_dir).resolve()
    if not evidence_root.is_dir():
        fail("Swift binary evidence directory is missing")

    normalized: list[dict[str, object]] = []
    seen_targets: set[str] = set()
    seen_files: set[str] = set()
    seen_urls: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"target", "file", "url", "checksum"}:
            fail("each Swift binary artifact must contain target, file, url and checksum")
        target = item.get("target")
        file_value = item.get("file")
        url = item.get("url")
        checksum = item.get("checksum")
        if not all(isinstance(value, str) and value for value in (target, file_value, url, checksum)):
            fail("Swift binary artifact fields must be non-empty strings")
        if target in seen_targets or file_value in seen_files or url in seen_urls:
            fail("Swift binary publication plan contains duplicate identities")
        seen_targets.add(target)
        seen_files.add(file_value)
        seen_urls.add(url)
        if target not in expected:
            fail(f"publication plan contains unknown binary target {target}")
        expected_item = expected[target]
        if url != expected_item.get("url") or checksum != expected_item.get("checksum"):
            fail(f"publication plan does not match Package.swift for target {target}")
        _, url_filename = validate_artifact_url(url, version)
        artifact_path = safe_evidence_file(evidence_root, file_value)
        if artifact_path.name != url_filename:
            fail(f"artifact filename does not match its Package.swift URL for target {target}")
        actual_checksum = sha256_file(artifact_path)
        if actual_checksum != checksum:
            fail(f"artifact checksum does not match Package.swift for target {target}")
        size = artifact_path.stat().st_size
        if size <= 0:
            fail(f"artifact for target {target} is empty")
        normalized.append(
            {
                "target": target,
                "file": file_value,
                "url": url,
                "checksum": checksum,
                "size": size,
            }
        )

    if seen_targets != set(expected):
        missing = sorted(set(expected) - seen_targets)
        fail(f"publication plan is missing Package.swift binary targets: {', '.join(missing)}")
    normalized.sort(key=lambda item: str(item["target"]))
    write_json(
        Path(args.output),
        {
            "schemaVersion": 1,
            "packageURL": contract["packageURL"],
            "version": version,
            "packageRevision": contract["packageRevision"],
            "evidenceRoot": str(evidence_root),
            "artifacts": normalized,
        },
    )


def basic_auth(username: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{token}".encode()).decode()


def read_remote_identity(url: str, username: str, read_token: str) -> tuple[int, int, str]:
    parsed = urlsplit(url)
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=120, context=ssl.create_default_context())
    try:
        connection.request("GET", parsed.path, headers={"Authorization": basic_auth(username, read_token)})
        response = connection.getresponse()
        if response.status == 404:
            response.read()
            return 404, 0, ""
        if response.status != 200:
            response.read()
            fail(f"private Generic Package readback failed with HTTP {response.status}")
        size = 0
        value = hashlib.sha256()
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            value.update(block)
        return 200, size, value.hexdigest()
    finally:
        connection.close()


def upload_remote(url: str, path: Path, username: str, publish_token: str) -> int:
    parsed = urlsplit(url)
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=300, context=ssl.create_default_context())
    try:
        connection.putrequest("PUT", parsed.path)
        connection.putheader("Authorization", basic_auth(username, publish_token))
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(path.stat().st_size))
        connection.endheaders()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                connection.send(block)
        response = connection.getresponse()
        status = response.status
        response.read()
        return status
    finally:
        connection.close()


def require_remote_exact(item: dict, username: str, read_token: str) -> bool:
    status, size, checksum = read_remote_identity(str(item["url"]), username, read_token)
    if status == 404:
        return False
    if size != item["size"] or checksum != item["checksum"]:
        fail(f"immutable Generic Package artifact conflict for target {item['target']}")
    return True


def command_publish(args: argparse.Namespace) -> None:
    plan = read_json(Path(args.plan))
    if not isinstance(plan, dict) or plan.get("schemaVersion") != 1:
        fail("invalid normalized Swift binary publication plan")
    version = plan.get("version")
    revision = plan.get("packageRevision")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        fail("invalid publication plan version")
    if not isinstance(revision, str) or not SHA40_RE.fullmatch(revision):
        fail("invalid publication plan GitHub revision")
    artifacts = plan.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        fail("normalized publication plan contains no artifacts")
    evidence_root_value = plan.get("evidenceRoot")
    if not isinstance(evidence_root_value, str):
        fail("normalized publication plan is missing its evidence root")
    evidence_root = Path(evidence_root_value).resolve()

    username = os.environ.get("CI_SWIFTPM_BINARY_PACKAGE_USERNAME", "")
    publish_token = os.environ.get("CI_SWIFTPM_BINARY_PACKAGE_TOKEN", "")
    read_token = os.environ.get("CI_SWIFTPM_BINARY_PACKAGE_READ_TOKEN", "")
    if not username or not publish_token or not read_token:
        fail("fixed private Generic Package credentials are missing")

    initially_present = 0
    for item in artifacts:
        if not isinstance(item, dict):
            fail("normalized publication artifact is malformed")
        validate_artifact_url(str(item.get("url", "")), version)
        path = safe_evidence_file(evidence_root, str(item.get("file", "")))
        if path.stat().st_size != item.get("size") or sha256_file(path) != item.get("checksum"):
            fail(f"local artifact changed after validation for target {item.get('target')}")
        if require_remote_exact(item, username, read_token):
            initially_present += 1
            continue
        status = upload_remote(str(item["url"]), path, username, publish_token)
        if status not in (200, 201) and not require_remote_exact(item, username, read_token):
            fail(f"private Generic Package create failed with HTTP {status} for target {item['target']}")
        if not require_remote_exact(item, username, read_token):
            fail(f"private Generic Package artifact missing after create for target {item['target']}")

    for item in artifacts:
        if not require_remote_exact(item, username, read_token):
            fail(f"complete Generic Package readback failed for target {item['target']}")

    write_json(
        Path(args.output),
        {
            "schemaVersion": 1,
            "packageURL": plan["packageURL"],
            "version": version,
            "packageRevision": revision,
            "artifactCount": len(artifacts),
            "replayed": initially_present == len(artifacts),
            "artifacts": [
                {
                    "target": item["target"],
                    "url": item["url"],
                    "checksum": item["checksum"],
                    "size": item["size"],
                }
                for item in artifacts
            ],
        },
    )


def command_validate_consumer(args: argparse.Namespace) -> None:
    contract = read_json(Path(args.contract))
    if not isinstance(contract, dict) or contract.get("schemaVersion") != 1:
        fail("invalid Swift binary source contract")
    expected = {
        "packageURL": contract.get("packageURL"),
        "version": contract.get("version"),
        "packageRevision": contract.get("packageRevision"),
    }
    for result_path in (Path(args.result_one), Path(args.result_two)):
        result = read_json(result_path)
        if not isinstance(result, dict) or result.get("schemaVersion") != 1:
            fail("Swift binary consumer result must be a schemaVersion 1 JSON object")
        if result.get("accepted") is not True:
            fail("Swift binary consumer did not report accepted=true")
        for key, value in expected.items():
            if result.get(key) != value:
                fail(f"Swift binary consumer result has unexpected {key}")
    first = read_json(Path(args.result_one))
    second = read_json(Path(args.result_two))
    if not isinstance(first, dict) or not isinstance(second, dict):
        fail("Swift binary consumer results are malformed")
    for key in ("packageURL", "version", "packageRevision"):
        if first.get(key) != second.get(key):
            fail("fresh Swift binary consumer identities disagree")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    source = subparsers.add_parser("source-contract")
    source.add_argument("--root", required=True)
    source.add_argument("--repository", required=True)
    source.add_argument("--version", required=True)
    source.add_argument("--source-sha", required=True)
    source.add_argument("--output", required=True)
    source.add_argument("--dump-output")
    source.set_defaults(func=command_source_contract)

    plan = subparsers.add_parser("validate-plan")
    plan.add_argument("--contract", required=True)
    plan.add_argument("--plan", required=True)
    plan.add_argument("--evidence-dir", required=True)
    plan.add_argument("--output", required=True)
    plan.set_defaults(func=command_validate_plan)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--plan", required=True)
    publish.add_argument("--output", required=True)
    publish.set_defaults(func=command_publish)

    consumer = subparsers.add_parser("validate-consumer")
    consumer.add_argument("--contract", required=True)
    consumer.add_argument("--result-one", required=True)
    consumer.add_argument("--result-two", required=True)
    consumer.set_defaults(func=command_validate_consumer)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
