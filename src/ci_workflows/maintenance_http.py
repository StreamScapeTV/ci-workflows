"""GitHub REST endpoint adapter for organization maintenance."""
from __future__ import annotations

import base64
import io
import urllib.parse
import zipfile
from typing import Any, Mapping, Sequence

from .maintenance_contract import MaintenanceError
from .maintenance_http_transport import GitHubTransport


class GitHubApi(GitHubTransport):
    def list_artifacts(self, repository: str):
        return self.paginate(
            f"/repos/{self._repo(repository)}/actions/artifacts?per_page=100",
            collection_key="artifacts",
        )

    def get_artifact(self, repository: str, artifact_id: int):
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/actions/artifacts/{artifact_id}",
            allow_404=True,
        )
        return payload if isinstance(payload, Mapping) else None

    def delete_artifact(self, repository: str, artifact_id: int) -> None:
        self.request(
            "DELETE",
            f"/repos/{self._repo(repository)}/actions/artifacts/{artifact_id}",
            expected=(204,),
        )

    def get_run(self, repository: str, run_id: int):
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/actions/runs/{run_id}",
            allow_404=True,
        )
        return payload if isinstance(payload, Mapping) else None

    def get_pull(self, repository: str, number: int):
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/pulls/{number}",
            allow_404=True,
        )
        return payload if isinstance(payload, Mapping) else None

    def list_closed_pulls(self, repository: str, base: str):
        query = urllib.parse.urlencode(
            {"state": "closed", "base": base, "per_page": 100}
        )
        return self.paginate(
            f"/repos/{self._repo(repository)}/pulls?{query}",
            maximum_pages=5,
        )

    def get_branch(self, repository: str, branch: str):
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/branches/"
            f"{urllib.parse.quote(branch, safe='')}",
            allow_404=True,
        )
        return payload if isinstance(payload, Mapping) else None

    def delete_branch(self, repository: str, branch: str) -> None:
        self.request(
            "DELETE",
            f"/repos/{self._repo(repository)}/git/refs/heads/"
            f"{urllib.parse.quote(branch, safe='')}",
            expected=(204,),
        )

    def get_commit(self, repository: str, sha: str):
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/commits/"
            f"{urllib.parse.quote(sha, safe='')}",
            allow_404=True,
        )
        return payload if isinstance(payload, Mapping) else None

    def compare_commits(
        self,
        repository: str,
        base_sha: str,
        head_sha: str,
    ):
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/compare/"
            f"{urllib.parse.quote(base_sha, safe='')}..."
            f"{urllib.parse.quote(head_sha, safe='')}",
        )
        if not isinstance(payload, Mapping):
            raise MaintenanceError("github_response_invalid")
        return payload

    def list_statuses(self, repository: str, sha: str):
        return self.paginate(
            f"/repos/{self._repo(repository)}/commits/"
            f"{urllib.parse.quote(sha, safe='')}/statuses?per_page=100",
            maximum_pages=2,
        )

    def create_status(
        self,
        repository: str,
        sha: str,
        *,
        state: str,
        context: str,
        description: str,
    ):
        payload, _ = self.request(
            "POST",
            f"/repos/{self._repo(repository)}/statuses/"
            f"{urllib.parse.quote(sha, safe='')}",
            payload={
                "state": state,
                "context": context,
                "description": description,
            },
            expected=(201,),
        )
        if not isinstance(payload, Mapping):
            raise MaintenanceError("github_response_invalid")
        return payload

    def list_workflow_files(self, repository: str) -> list[str]:
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/contents/.github/workflows",
            allow_404=True,
        )
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise MaintenanceError("workflow_inventory_unreadable")
        return sorted(
            str(item["path"])
            for item in payload
            if isinstance(item, Mapping)
            and isinstance(item.get("path"), str)
            and str(item["path"]).endswith((".yml", ".yaml"))
        )

    def get_file_text(
        self,
        repository: str,
        path: str,
        ref: str,
    ) -> str | None:
        quoted = "/".join(
            urllib.parse.quote(part, safe="") for part in path.split("/")
        )
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/contents/{quoted}?"
            + urllib.parse.urlencode({"ref": ref}),
            allow_404=True,
        )
        if payload is None:
            return None
        if (
            not isinstance(payload, Mapping)
            or payload.get("type") != "file"
            or payload.get("encoding") != "base64"
            or not isinstance(payload.get("content"), str)
        ):
            raise MaintenanceError("workflow_inventory_unreadable")
        try:
            encoded = "".join(str(payload["content"]).split())
            return base64.b64decode(encoded, validate=True).decode()
        except (ValueError, UnicodeDecodeError) as error:
            raise MaintenanceError("workflow_inventory_unreadable") from error

    def get_issue(self, repository: str, number: int):
        payload, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/issues/{number}",
            allow_404=True,
        )
        return payload if isinstance(payload, Mapping) else None

    def list_open_issues(self, repository: str):
        return self.paginate(
            f"/repos/{self._repo(repository)}/issues?state=open&per_page=100",
            maximum_pages=10,
        )

    def create_issue(self, repository: str, title: str, body: str):
        payload, _ = self.request(
            "POST",
            f"/repos/{self._repo(repository)}/issues",
            payload={"title": title, "body": body},
            expected=(201,),
        )
        if not isinstance(payload, Mapping):
            raise MaintenanceError("github_response_invalid")
        return payload

    def update_issue(
        self,
        repository: str,
        number: int,
        title: str,
        body: str,
    ):
        payload, _ = self.request(
            "PATCH",
            f"/repos/{self._repo(repository)}/issues/{number}",
            payload={"title": title, "body": body},
        )
        if not isinstance(payload, Mapping):
            raise MaintenanceError("github_response_invalid")
        return payload

    def list_issue_comments(self, repository: str, number: int):
        return self.paginate(
            f"/repos/{self._repo(repository)}/issues/{number}/comments?"
            "per_page=100",
            maximum_pages=5,
        )

    def create_issue_comment(
        self,
        repository: str,
        number: int,
        body: str,
    ):
        payload, _ = self.request(
            "POST",
            f"/repos/{self._repo(repository)}/issues/{number}/comments",
            payload={"body": body},
            expected=(201,),
        )
        if not isinstance(payload, Mapping):
            raise MaintenanceError("github_response_invalid")
        return payload

    def update_issue_comment(
        self,
        repository: str,
        comment_id: int,
        body: str,
    ):
        payload, _ = self.request(
            "PATCH",
            f"/repos/{self._repo(repository)}/issues/comments/{comment_id}",
            payload={"body": body},
        )
        if not isinstance(payload, Mapping):
            raise MaintenanceError("github_response_invalid")
        return payload

    def set_issue_labels(
        self,
        repository: str,
        number: int,
        labels: Sequence[str],
    ):
        payload, _ = self.request(
            "PATCH",
            f"/repos/{self._repo(repository)}/issues/{number}",
            payload={"labels": list(labels)},
        )
        if not isinstance(payload, Mapping):
            raise MaintenanceError("github_response_invalid")
        return payload

    def list_attempt_jobs(
        self,
        repository: str,
        run_id: int,
        attempt: int,
    ):
        return self.paginate(
            f"/repos/{self._repo(repository)}/actions/runs/{run_id}/"
            f"attempts/{attempt}/jobs?per_page=100",
            collection_key="jobs",
            maximum_pages=2,
        )

    def download_job_logs(
        self,
        repository: str,
        job_id: int,
        maximum_bytes: int,
    ) -> str:
        raw, _ = self.request(
            "GET",
            f"/repos/{self._repo(repository)}/actions/jobs/{job_id}/logs",
            raw=True,
        )
        data = bytes(raw)
        if len(data) > maximum_bytes:
            raise MaintenanceError("job_logs_too_large")
        if data[:2] == b"PK":
            try:
                chunks: list[bytes] = []
                total = 0
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for name in sorted(archive.namelist()):
                        chunk = archive.read(name)
                        total += len(chunk)
                        if total > maximum_bytes:
                            raise MaintenanceError("job_logs_too_large")
                        chunks.append(chunk)
                data = b"\n".join(chunks)
            except zipfile.BadZipFile as error:
                raise MaintenanceError("job_logs_invalid") from error
        return data.decode(errors="replace")

    def rerun_failed_jobs(self, repository: str, run_id: int) -> None:
        self.request(
            "POST",
            f"/repos/{self._repo(repository)}/actions/runs/{run_id}/"
            "rerun-failed-jobs",
            expected=(201,),
        )
