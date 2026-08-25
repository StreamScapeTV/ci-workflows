# Central CI normalized diagnostics

Central CI keeps full raw execution output in GitHub Actions. Agent State never stores
log bodies. The shared `persist-ci-diagnostics` action writes only a bounded normalized
diagnostic record to the fixed Cloudflare D1 database and returns a small receipt that
can be attached to the terminal Agent State transition.

## Stored identity

One D1 record is keyed by `ci/<ci_run_id>/<run_attempt>` and records the Agent State CI
run UUID, GitHub run id/attempt, project key, full `owner/name` source repository,
human branch/tag `ref`, explicit `is_tag`, workflow key, profile, intended terminal
status, normalized diagnostics, and their SHA-256 digest. Commit SHA is not request or
D1 identity; Agent State retains the actually observed checkout SHA separately as
evidence.

The record is visible for 24 hours, matching the recent-CI discovery window. Each write
also opportunistically removes expired records.

## Diagnostic payload

The payload is an array of at most 64 records and at most 64 KiB after canonical
normalization. Each record contains only:

- `severity`: `warning` or `error`;
- `code`: one stable bounded machine code;
- optional `stage`: one bounded stage identity;
- `message`: one normalized single-line message, at most 2048 bytes after sanitization.

`debug` and `info` rows are rejected rather than copied into the secondary diagnostic
store. Secret assignments, bearer credentials, GitHub token-shaped values, and URL
userinfo are redacted again at the persistence boundary. Product workflows should
still normalize diagnostics before calling the action; this sink is a final bounded
safety boundary, not a raw-log parser.

## D1 transport

The action has no credential inputs and no caller-selectable endpoint. The trusted
workflow supplies fixed environment values `CIW_D1_ACCOUNT_ID`, `CIW_D1_DATABASE_ID`,
and `CIW_D1_API_TOKEN`. The implementation calls Cloudflare's D1 query endpoint with
fixed SQL and parameter arrays only. It never executes caller-provided SQL.

One transactional batch creates the fixed table/index when absent, removes expired
rows, inserts or idempotently updates the exact diagnostic identity, and reads the row
back. `diagnostic_status=uploaded` is emitted only after the read-back identity,
digest, count, status, and canonical JSON all match.

The action outputs only `diagnostic_key`, `diagnostic_status`, `diagnostic_sha256`, and
`diagnostic_count`. Diagnostic JSON, Cloudflare credentials, API responses, and raw
GitHub logs are never written to GitHub outputs or Agent State.

## Lifecycle ordering

For a terminal Central CI path the required ordering is:

1. finish build/test and cleanup;
2. normalize warning/error diagnostics;
3. persist and verify the D1 record;
4. transition Agent State to `succeeded`, `failed`, `cancelled`, or `timed_out` with
   only the bounded diagnostic key/status and stable error summary.

A terminal Agent State row therefore never points at a diagnostic record that has not
already been persisted successfully.
