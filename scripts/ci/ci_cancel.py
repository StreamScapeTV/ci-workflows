"""Execute one backend-authorized cancellation; the broker remains a relay."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from uuid import UUID

CENTRAL = "StreamScapeTV/ci-workflows"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cancel(control_id, rpc, github):
    control_reply = rpc("claim_ci_run", {"p_ci_run_id": control_id})
    control = control_reply.get("run", {})
    require(control_reply.get("ok") is True and control.get("ci_run_id") == control_id
            and control.get("status") == "running"
            and control.get("workflow_key") == "maintenance.ci-cancel"
            and control.get("test_profile") == "cancel", "invalid cancellation execution")
    inputs = control.get("inputs")
    require(isinstance(inputs, dict) and set(inputs) == {"target_ci_run_id", "requested_at"},
            "invalid cancellation inputs")
    require(isinstance(inputs["requested_at"], str) and 0 < len(inputs["requested_at"]) <= 128,
            "invalid cancellation request timestamp")
    target_id = inputs["target_ci_run_id"]
    require(isinstance(target_id, str) and str(UUID(target_id)) == target_id
            and target_id != control_id, "invalid cancellation target")
    plan = rpc("get_ci_cancellation", {"p_ci_run_id": target_id})
    if plan.get("code") == "already_terminal":
        return {"provider_terminal": True, "cancellation_requested": False}
    target = plan.get("run", {})
    require(plan.get("ok") is True and target.get("ci_run_id") == target_id,
            "cancellation plan refused: " + str(plan.get("code", "invalid_response")))
    require(all(target.get(key) == control.get(key) for key in ("project_key", "repository", "ref"))
            and target.get("cancellation_requested_at") == inputs["requested_at"],
            "cancellation request correlation changed")
    if target.get("cancellation_accepted_at") is not None:
        return {"cancellation_requested": True, "provider_terminal": False}
    require(target.get("external_repository") == CENTRAL, "unexpected provider repository")
    run_id, attempt = target.get("external_run_id"), target.get("external_run_attempt")
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0,
            "invalid provider identity")
    path = f"/repos/{CENTRAL}/actions/runs/{run_id}"

    def observe():
        _, provider = github("GET", path)
        require(isinstance(provider, dict) and type(provider.get("id")) is int
                and type(provider.get("run_attempt")) is int
                and provider["id"] == run_id and provider["run_attempt"] == attempt,
                "provider attempt changed or unavailable")
        return provider

    def terminal(provider):
        if provider.get("status") != "completed":
            return None
        conclusion = provider.get("conclusion")
        if conclusion == "success":
            return "succeeded"
        if conclusion == "cancelled":
            return "cancelled"
        require(conclusion in {"failure", "timed_out", "action_required", "startup_failure",
                               "stale", "neutral", "skipped"}, "unknown provider conclusion")
        return "failed"

    provider = observe()
    outcome = terminal(provider)
    if outcome is None:
        require(provider.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"},
                "unknown provider status")
        status, _ = github("POST", path + "/cancel")
        require(status in {202, 409}, "provider cancellation not accepted")
        outcome = "accepted" if status == 202 else terminal(observe())
        require(outcome is not None, "provider cancellation outcome unknown")
    recorded = rpc("record_ci_cancellation", {"p_ci_run_id": target_id, "p_result": {
        "external_repository": CENTRAL, "external_run_id": run_id,
        "external_run_attempt": attempt, "requested_at": inputs["requested_at"], "outcome": outcome,
    }})
    require(recorded.get("ok") is True, "cancellation recording refused: " + str(recorded.get("code")))
    return {"cancellation_requested": outcome == "accepted", "provider_terminal": outcome != "accepted"}


def http(method, url, headers, payload=None, expected=(200,)):
    request = urllib.request.Request(url, method=method, headers=headers,
                                     data=None if payload is None else json.dumps(payload).encode())
    try:
        response = urllib.request.urlopen(request, timeout=25)
    except urllib.error.HTTPError as error:
        if error.code not in expected:
            raise ValueError(f"{method} returned HTTP {error.code}") from None
        response = error
    with response:
        require(response.code in expected, "unexpected HTTP status")
        body = response.read()
        return response.code, json.loads(body) if body else None


def main():
    require(os.environ["GITHUB_REPOSITORY"] == CENTRAL, "unexpected Central repository")
    base = os.environ["AGENT_STATE_SUPABASE_URL"].rstrip("/")
    headers = {"apikey": os.environ["AGENT_STATE_SUPABASE_SECRET_KEY"],
               "Content-Type": "application/json", "Content-Profile": "agent_api", "Accept-Profile": "agent_api"}

    def rpc(name, payload):
        return http("POST", base + "/rest/v1/rpc/" + name, headers, payload)[1]

    def github(method, path):
        return http(method, "https://api.github.com" + path,
                    {"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                    expected=(202, 409) if method == "POST" else (200,))

    print(json.dumps(cancel(os.environ["CI_RUN_ID"], rpc, github)))


if __name__ == "__main__":
    main()
