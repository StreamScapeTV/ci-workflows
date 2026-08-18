"""Internal best-effort Gradle dependency-seed sync transport.

The portable cache-selection/framing contract remains owned by ``gradle_seed``.
This module deliberately adds no GitHub identity or OIDC dependency: ordinary
mobile runners stream their job-private ``caches/modules-*`` delta only to the
fixed cluster-local Flux service.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from .gradle_seed import (
    CONTENT_TYPE,
    FLUX_HOST,
    FLUX_PATH,
    FLUX_PORT,
    HTTP_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    GradleSeedError,
    GradleSeedResult,
    SeedFile,
    UploadResponse,
    collect_seed_files,
    framed_seed_stream,
)

_SHA40 = re.compile(r"^[a-f0-9]{40}$")
_GENERATION = re.compile(r"^sha256-[a-f0-9]{64}$")


class SeedUploader(Protocol):
    def upload(
        self,
        *,
        source_sha: str,
        body: Iterable[bytes],
    ) -> UploadResponse: ...


class InternalFluxSeedUploader:
    """Stream one framed delta only to the fixed cluster-local Flux service."""

    def __init__(self, *, timeout_seconds: int = HTTP_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def upload(
        self,
        *,
        source_sha: str,
        body: Iterable[bytes],
    ) -> UploadResponse:
        connection = http.client.HTTPConnection(
            FLUX_HOST,
            FLUX_PORT,
            timeout=self.timeout_seconds,
        )
        try:
            connection.request(
                "POST",
                FLUX_PATH,
                body=body,
                headers={
                    "Content-Type": CONTENT_TYPE,
                    "X-Gradle-Source-Sha": source_sha,
                },
                encode_chunked=True,
            )
            response = connection.getresponse()
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise GradleSeedError("gradle_seed_response_too_large")
            return UploadResponse(
                status=response.status,
                content_type=response.getheader("content-type", ""),
                body=response_body,
            )
        except GradleSeedError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise GradleSeedError("gradle_seed_upload_failed") from error
        finally:
            connection.close()


def _manifest_sha256(files: tuple[SeedFile, ...]) -> str:
    digest = hashlib.sha256()
    for seed_file in files:
        header = seed_file.header_bytes()
        digest.update(len(header).to_bytes(4, byteorder="big"))
        digest.update(header)
    return digest.hexdigest()


def _validated_response(
    response: UploadResponse,
    *,
    source_sha: str,
    files: tuple[SeedFile, ...],
) -> tuple[str, int]:
    if response.status != 200:
        raise GradleSeedError("gradle_seed_promotion_rejected")
    if response.content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise GradleSeedError("gradle_seed_response_invalid")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GradleSeedError("gradle_seed_response_invalid") from error
    if not isinstance(payload, dict) or payload.get("status") != "promoted":
        raise GradleSeedError("gradle_seed_response_invalid")
    if payload.get("sourceSha") != source_sha:
        raise GradleSeedError("gradle_seed_response_source_mismatch")
    generation = payload.get("generation")
    if not isinstance(generation, str) or _GENERATION.fullmatch(generation) is None:
        raise GradleSeedError("gradle_seed_response_generation_invalid")
    file_count = payload.get("fileCount")
    total_bytes = payload.get("totalBytes")
    expected_bytes = sum(item.size for item in files)
    if type(file_count) is not int or type(total_bytes) is not int:
        raise GradleSeedError("gradle_seed_response_counts_mismatch")
    if file_count != len(files) or total_bytes != expected_bytes:
        raise GradleSeedError("gradle_seed_response_counts_mismatch")
    return generation, expected_bytes


def sync_gradle_seed(
    *,
    source_sha: str,
    environment: Mapping[str, str],
    uploader: SeedUploader | None = None,
) -> GradleSeedResult:
    """Stream one job-private dependency delta without running Gradle again."""

    if _SHA40.fullmatch(source_sha) is None:
        raise GradleSeedError("gradle_seed_source_sha_invalid")
    raw_home = environment.get("GRADLE_USER_HOME", "")
    if not raw_home:
        raise GradleSeedError("gradle_seed_home_required")

    files = collect_seed_files(Path(raw_home))
    manifest_sha256 = _manifest_sha256(files)
    transport = uploader or InternalFluxSeedUploader()
    try:
        response = transport.upload(
            source_sha=source_sha,
            body=framed_seed_stream(files),
        )
    except GradleSeedError:
        raise
    except Exception as error:
        raise GradleSeedError("gradle_seed_upload_failed") from error

    generation, total_bytes = _validated_response(
        response,
        source_sha=source_sha,
        files=files,
    )
    evidence_payload = json.dumps(
        {
            "fileCount": len(files),
            "generation": generation,
            "manifestSha256": manifest_sha256,
            "protocol": "gradle-seed-v1",
            "sourceSha": source_sha,
            "totalBytes": total_bytes,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return GradleSeedResult(
        source_sha=source_sha,
        generation=generation,
        file_count=len(files),
        total_bytes=total_bytes,
        evidence_id=hashlib.sha256(evidence_payload).hexdigest(),
    )
