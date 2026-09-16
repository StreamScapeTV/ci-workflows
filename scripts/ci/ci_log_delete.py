#!/usr/bin/env python3
"""Delete deterministic private CI logs for one expired Agent State CI run."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Protocol

FOLDER_MIME = "application/vnd.google-apps.folder"
MAX_EXACT_FOLDER_MATCHES = 20
MAX_EXACT_FILE_MATCHES = 20

APPLE_LANES: dict[str, tuple[str, ...]] = {
    "screenshot-review": ("screenshot-ios", "screenshot-tvos"),
    "build": ("hosted-build",),
    "native-component": ("native-component",),
    "test": ("hosted-test",),
    "simulator": ("hosted-simulator",),
    "host": ("macos-test",),
    # test_platform is intentionally not copied into the maintenance request.
    # Delete all exact possible lane names for the same source run; two will be absent.
    "targeted-tests": ("macos-targeted-tests", "ios-targeted-tests", "tvos-targeted-tests"),
    "candidate": ("ios-build", "tvos-build", "macos-targeted-tests"),
    "full": ("ios-build", "tvos-build", "macos-test"),
    "release-build": ("release-build",),
    "testflight": ("testflight",),
    "swift-package": ("swift-package",),
}
ANDROID_STANDARD_PROFILES = frozenset(
    {
        "smoke",
        "compile",
        "unit",
        "targeted-unit",
        "targeted-tests",
        "lint",
        "assemble",
        "full",
        "search-performance",
        "release",
        "build",
        "test",
        "emulator",
        "play",
    }
)
ANDROID_SCREENSHOT_PROFILES = (
    "phone-portrait",
    "phone-landscape",
    "tablet-portrait",
    "tablet-landscape",
    "tv",
)
PYTHON_PROFILES = frozenset(
    {
        "compile",
        "unit",
        "valkey",
        "release-gates",
        "backend-full",
        "backend-postgres",
        "agent-state-issue-reconcile",
    }
)
NODE_PROFILES = frozenset({"frontend-full", "static-web", "static-web-lock-refresh"})
FLUTTER_PROFILES = frozenset({"quality"})

NO_PRIVATE_LOG = frozenset(
    {
        ("maintenance.branch-delete", "delete"),
        ("source.checkpoint-publish", "publish"),
        ("source.snapshot", "snapshot"),
        ("maintenance.ci-log-delete", "delete"),
    }
)


def _standard(run_id: int, attempt: int) -> tuple[str, ...]:
    return (f"{run_id}-{attempt}.txt",)


def _lane_names(run_id: int, attempt: int, lanes: Iterable[str]) -> tuple[str, ...]:
    return tuple(f"{run_id}-{attempt}-{lane}.txt" for lane in lanes)


def expected_log_filenames(
    workflow_key: str,
    test_profile: str,
    external_run_id: int,
    external_run_attempt: int,
) -> tuple[str, ...]:
    """Return the exact current private-log filenames for one Central execution."""
    if external_run_id <= 0 or external_run_attempt <= 0:
        raise ValueError("external run identity must be positive")
    semantic = (workflow_key, test_profile)
    if semantic in NO_PRIVATE_LOG:
        return ()

    if workflow_key == "validation.apple":
        lanes = APPLE_LANES.get(test_profile)
        if lanes is None:
            raise ValueError("unsupported validation.apple profile")
        return _lane_names(external_run_id, external_run_attempt, lanes)
    if workflow_key == "release.apple":
        if test_profile == "testflight":
            return _lane_names(external_run_id, external_run_attempt, ("testflight",))
        if test_profile == "binary-package":
            return _standard(external_run_id, external_run_attempt)
        if test_profile == "swiftpm-package":
            return (f"{external_run_id}-{external_run_attempt}-apple-swiftpm.txt",)
        raise ValueError("unsupported release.apple profile")

    if workflow_key == "validation.android":
        if test_profile == "screenshot-review":
            return (
                f"{external_run_id}-{external_run_attempt}-screenshot-cache.txt",
                *_lane_names(
                    external_run_id,
                    external_run_attempt,
                    (f"screenshot-{profile}" for profile in ANDROID_SCREENSHOT_PROFILES),
                ),
            )
        if test_profile in ANDROID_STANDARD_PROFILES:
            return _standard(external_run_id, external_run_attempt)
        raise ValueError("unsupported validation.android profile")
    if workflow_key == "release.android":
        if test_profile != "play":
            raise ValueError("unsupported release.android profile")
        return _standard(external_run_id, external_run_attempt)

    if workflow_key == "validation.python":
        if test_profile not in PYTHON_PROFILES:
            raise ValueError("unsupported validation.python profile")
        return _standard(external_run_id, external_run_attempt)
    if workflow_key == "validation.node":
        if test_profile not in NODE_PROFILES:
            raise ValueError("unsupported validation.node profile")
        return _standard(external_run_id, external_run_attempt)
    if workflow_key == "validation.flutter":
        if test_profile not in FLUTTER_PROFILES:
            raise ValueError("unsupported validation.flutter profile")
        return _standard(external_run_id, external_run_attempt)
    if workflow_key == "release.maven":
        if test_profile != "publish":
            raise ValueError("unsupported release.maven profile")
        return _standard(external_run_id, external_run_attempt)
    if workflow_key == "validation.container-service":
        if test_profile != "conformance":
            raise ValueError("unsupported validation.container-service profile")
        return _standard(external_run_id, external_run_attempt)
    if workflow_key == "release.public-native-image-chart":
        if test_profile != "public-native-image-chart":
            raise ValueError("unsupported release.public-native-image-chart profile")
        return _standard(external_run_id, external_run_attempt)
    if workflow_key == "oci.reproducibility":
        if test_profile != "reproducibility":
            raise ValueError("unsupported oci.reproducibility profile")
        return _standard(external_run_id, external_run_attempt)

    raise ValueError("unsupported CI workflow/profile private-log semantic")


def _drive_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveLike(Protocol):
    def exact_children(self, parent_id: str, name: str, *, folders: bool) -> tuple[str, ...]: ...
    def has_any_child(self, parent_id: str) -> bool: ...
    def delete_file(self, file_id: str) -> None: ...


@dataclass
class DriveApi:
    root_folder_id: str
    client_id: str
    client_secret: str
    refresh_token: str
    opener: object = urllib.request
    _access_token: str | None = None

    def _request_json(self, request: urllib.request.Request) -> dict[str, object]:
        with self.opener.urlopen(request, timeout=30) as response:  # type: ignore[attr-defined]
            data = response.read()
        if not data:
            return {}
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("Google Drive API returned non-object JSON")
        return value

    def access_token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        encoded = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()
        value = self._request_json(
            urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=encoded,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        )
        token = value.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Google OAuth token response has no access token")
        self._access_token = token
        return token

    def _drive_request(self, url: str, *, method: str = "GET") -> urllib.request.Request:
        return urllib.request.Request(
            url,
            method=method,
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )

    def exact_children(self, parent_id: str, name: str, *, folders: bool) -> tuple[str, ...]:
        clauses = [
            f"'{_drive_literal(parent_id)}' in parents",
            f"name = '{_drive_literal(name)}'",
            "trashed = false",
            (f"mimeType = '{FOLDER_MIME}'" if folders else f"mimeType != '{FOLDER_MIME}'"),
        ]
        token = ""
        ids: list[str] = []
        pages = 0
        while True:
            pages += 1
            if pages > 100:
                raise RuntimeError("Google Drive exact-name lookup exceeded pagination bound")
            params = {
                "q": " and ".join(clauses),
                "spaces": "drive",
                "fields": "nextPageToken,files(id)",
                "pageSize": "100",
            }
            if token:
                params["pageToken"] = token
            value = self._request_json(
                self._drive_request("https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params))
            )
            files = value.get("files", [])
            if not isinstance(files, list):
                raise RuntimeError("Google Drive exact-name lookup returned invalid files")
            for entry in files:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                    raise RuntimeError("Google Drive exact-name lookup returned invalid file id")
                ids.append(entry["id"])
            token_value = value.get("nextPageToken", "")
            if not isinstance(token_value, str):
                raise RuntimeError("Google Drive exact-name lookup returned invalid pagination token")
            token = token_value
            if not token:
                break
        limit = MAX_EXACT_FOLDER_MATCHES if folders else MAX_EXACT_FILE_MATCHES
        unique = tuple(dict.fromkeys(ids))
        if len(unique) > limit:
            raise RuntimeError("Google Drive exact-name lookup exceeded duplicate bound")
        return unique

    def has_any_child(self, parent_id: str) -> bool:
        params = {
            "q": f"'{_drive_literal(parent_id)}' in parents and trashed = false",
            "spaces": "drive",
            "fields": "files(id)",
            "pageSize": "1",
        }
        value = self._request_json(
            self._drive_request("https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params))
        )
        files = value.get("files", [])
        if not isinstance(files, list):
            raise RuntimeError("Google Drive child lookup returned invalid files")
        return bool(files)

    def delete_file(self, file_id: str) -> None:
        request = self._drive_request(f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}", method="DELETE")
        try:
            with self.opener.urlopen(request, timeout=30):  # type: ignore[attr-defined]
                pass
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return
            raise


def delete_ci_logs(
    drive: DriveLike,
    *,
    root_folder_id: str,
    repository: str,
    ref: str,
    filenames: tuple[str, ...],
) -> dict[str, int]:
    repository_name = repository.rsplit("/", 1)[-1]
    repository_folders = drive.exact_children(root_folder_id, repository_name, folders=True)
    deleted_files = 0
    deleted_ref_folders = 0
    deleted_repository_folders = 0
    for repository_folder_id in repository_folders:
        ref_folders = drive.exact_children(repository_folder_id, ref, folders=True)
        for ref_folder_id in ref_folders:
            for filename in filenames:
                file_ids = drive.exact_children(ref_folder_id, filename, folders=False)
                for file_id in file_ids:
                    drive.delete_file(file_id)
                    deleted_files += 1
            if not drive.has_any_child(ref_folder_id):
                drive.delete_file(ref_folder_id)
                deleted_ref_folders += 1
        if repository_folder_id != root_folder_id and not drive.has_any_child(repository_folder_id):
            drive.delete_file(repository_folder_id)
            deleted_repository_folders += 1
    return {
        "deleted_files": deleted_files,
        "deleted_ref_folders": deleted_ref_folders,
        "deleted_repository_folders": deleted_repository_folders,
    }


def _bounded_identity(value: str, name: str, pattern: str, max_length: int) -> str:
    if not value or len(value) > max_length or re.fullmatch(pattern, value) is None:
        raise ValueError(f"invalid {name}")
    return value


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--source-workflow-key", required=True)
    parser.add_argument("--source-test-profile", required=True)
    parser.add_argument("--source-external-run-id", required=True, type=int)
    parser.add_argument("--source-external-run-attempt", required=True, type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repository = _bounded_identity(args.repository, "repository", r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", 255)
    ref = _bounded_identity(args.ref, "ref", r"[^\x00\r\n]{1,255}", 255)
    workflow_key = _bounded_identity(args.source_workflow_key, "source workflow key", r"[a-z][a-z0-9.-]{0,127}", 128)
    test_profile = _bounded_identity(args.source_test_profile, "source test profile", r"[a-z][a-z0-9-]{0,63}", 64)
    filenames = expected_log_filenames(
        workflow_key,
        test_profile,
        args.source_external_run_id,
        args.source_external_run_attempt,
    )
    root = os.environ.get("GOOGLE_DRIVE_CI_LOGS_FOLDER_ID", "")
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", "")
    if not all((root, client_id, client_secret, refresh_token)):
        raise SystemExit("private CI-log Google Drive configuration is required")
    drive = DriveApi(root, client_id, client_secret, refresh_token)
    result = delete_ci_logs(
        drive,
        root_folder_id=root,
        repository=repository,
        ref=ref,
        filenames=filenames,
    )
    print(json.dumps({"filenames": list(filenames), **result}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
