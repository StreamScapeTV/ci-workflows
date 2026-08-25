# Central CI private logs

Central CI runs in the public `StreamScapeTV/ci-workflows` repository, so the
GitHub Actions log is **not** a storage location for private product output. For
private-source execution, GitHub receives only generic orchestration/pass-fail
messages. Detailed private checkout, build, test, compiler, package-resolution,
and cleanup output is captured into a runner-local file and exported to the
private Cloudflare R2 bucket.

Agent State remains the short-lived CI metadata index. It stores no log body.
After R2 upload and read-back verification, the terminal CI row records only a
small R2 receipt in the existing `diagnostic_key`/`diagnostic_status`
compatibility fields.

## Public GitHub boundary

Webhook dispatch into `central-ci-dispatch.yml` contains only:

- an opaque Agent State `ci_run_id` UUID;
- an opaque SHA-256 active-identity key used only for workflow concurrency.

Repository, project key, branch/tag ref, workflow/profile, product configuration,
source SHA, dependency identity, and private command output are not workflow
inputs. The trusted private executor re-claims the canonical row from Agent
State by UUID and keeps those values inside the Python process.

The GitHub Actions log may show public Central checkout information, fixed secret
*names*, the opaque UUID/hash, and generic results such as `private_ci_succeeded`
or `private_ci_failed`. Private source values and detailed command stdout/stderr
must not be emitted through workflow `with:`, `env:`, step outputs, summaries, or
shell tracing.

## Runner-local capture

The private executor creates one mode-0600 log below `RUNNER_TEMP`. Private Git
checkout output is redirected there. Canonical Apple validation already captures
subprocess stdout/stderr rather than streaming it; the executor redirects its
Python stdout/stderr to the same private log and appends the canonical per-stage
Apple state logs before cleanup.

Secret-bearing command files (`GITHUB_OUTPUT`, `GITHUB_ENV`, and
`GITHUB_STEP_SUMMARY`) are removed from the private product execution environment
so product details cannot escape through workflow commands. The source checkout,
workspace state, dependency checkout, and temporary private log are removed from
the runner after terminal handling/recovery.

## R2 transport

The existing R2 implementation uses the S3-compatible endpoint with fixed trusted
environment variables:

- `R2_ACCOUNT_ID`
- `R2_BUCKET`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

There are no caller-selected bucket, host, secret-name, SQL, or storage inputs.
The private log is bounded, deterministically gzip-compressed, uploaded to R2,
then downloaded again and SHA-256 verified before it is considered available.

New objects include an unguessable 128-bit capability segment:

```text
ci-diagnostics/<ci_run_id>/<128-bit-capability>/<github_run_id>-<run_attempt>.log.gz
```

The write path derives that capability with domain-separated HMAC-SHA-256 from
the R2 **write-only** secret plus the exact CI identity and compressed digest.
The derivation is deterministic for the same exact object, so upload/read-back
and recovery remain idempotent. The read service never receives the write secret
and therefore cannot mint another valid capability-bearing object path.

The compatibility Agent State receipt is:

```text
r2:ci-diagnostics/<ci_run_id>/<128-bit-capability>/<github_run_id>-<run_attempt>.log.gz#sha256=<compressed_sha256>
```

with `diagnostic_status=uploaded`. If upload/read-back fails, the workflow fails
closed and records a stable upload failure instead of inventing a log pointer.
R2 object retention is owned by the bucket lifecycle policy; Agent State remains
a 24-hour recent-CI index. Legacy deterministic object paths may still be read
internally for historical read-back compatibility, but they are not accepted by
the externally reachable diagnostics reader.

## Authorized receipt-capability retrieval

Detailed logs are retrieved by a separate read-only service, not by the thin
Agent State relay. The reader receives only `R2_ACCOUNT_ID`, `R2_BUCKET`,
`R2_READ_ACCESS_KEY_ID`, and `R2_READ_SECRET_ACCESS_KEY`. It has no Agent State,
GitHub App, source, workflow-dispatch, lifecycle, R2 write, or capability-minting
credential. The thin relay therefore remains unable to inspect or return build
logs.

The exact capability-bearing Agent State receipt is the authorization material.
An agent that is authorized to read the CI row obtains `diagnostic_key`, UTF-8
encodes the exact receipt, applies URL-safe base64 without `=` padding, and
requests:

```text
/diagnostics/<receipt-capability>
```

The receipt-capability is a bearer capability and must be handled accordingly:
do not paste it into GitHub issues, public Actions logs, workflow inputs, or
other durable shared text. The service suppresses application request logging
and returns `Cache-Control: private, no-store` plus no-referrer/nosniff headers.
Access remains possible only while the R2 object exists, so the bucket lifecycle
is also the capability lifetime boundary.

The reader decodes one exact receipt, requires the 128-bit capability-bearing
object shape, downloads that exact object with the read-only R2 key, and verifies
the receipt's compressed SHA-256 **before** bounded gzip decompression. A missing
object or digest mismatch is reported as the same generic not-found result.
Successful retrieval returns the bounded decompressed UTF-8 log as `text/plain`;
it does not create a GitHub Actions artifact, copy the log into a GitHub job, or
write any body back into Agent State.

The service is intentionally receipt-capability based rather than Agent State
aware. Agent State continues to be RPC-only and metadata-only; the reader does
not receive a database credential or acquire ownership/lifecycle authority.

## Terminal ordering

For both success and failure the intended order is:

1. register/attach the real GitHub run identity in Agent State;
2. resolve and check out the requested private branch/tag internally;
3. record the observed checkout SHA as evidence only;
4. execute private validation while detailed output stays runner-local;
5. perform deterministic cleanup/residue checks;
6. gzip, mint the R2 object capability, upload, read back, and digest-verify the
   private log in R2;
7. update terminal Agent State status with only the stable result/error and R2
   receipt/status;
8. remove local private state.

GitHub Actions is therefore execution/orchestration evidence, Agent State is the
short-lived status/discovery index, and R2 is the detailed private log authority.
Cloudflare D1 is not part of this path.
