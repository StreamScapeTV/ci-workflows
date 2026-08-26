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
checkout output is redirected there. Canonical validation implementations capture
subprocess stdout/stderr rather than streaming private product output; the opaque
executor redirects their Python stdout/stderr to the same private log and appends
bounded implementation state logs before cleanup.

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
the R2 write secret plus the exact CI identity and compressed digest. The
derivation is deterministic for the same exact object, so upload/read-back and
recovery remain idempotent.

The compatibility Agent State receipt is:

```text
r2:ci-diagnostics/<ci_run_id>/<128-bit-capability>/<github_run_id>-<run_attempt>.log.gz#sha256=<compressed_sha256>
```

with `diagnostic_status=uploaded`. If upload/read-back fails, the workflow fails
closed and records a stable upload failure instead of inventing a log pointer.
R2 object retention is owned by the bucket lifecycle policy; Agent State remains
a short-lived recent-CI index.

## Receipt-bound retrieval

The former public diagnostics HTTP reader is withdrawn and must not be deployed,
routed, or exposed. The broker image and chart contain only the webhook broker;
there is no `ci-broker-diagnostics` Deployment, Service, route, hostname, or
second HTTP process.

An authorized agent retrieves the exact private object through the lowercase
custom Cloudflare MCP using the receipt already stored in Agent State. Retrieval
must remain exact and receipt-bound:

1. read the exact R2 object key from the Agent State receipt;
2. fetch that exact object directly from private R2 through the lowercase
   Cloudflare MCP;
3. compute SHA-256 over the compressed object and compare it with the receipt
   before trusting or decompressing it;
4. only after the digest matches, decompress locally and read the minimum private
   log content required for the acceptance proof.

The full private log must not be copied into GitHub issues, pull requests,
Actions logs, summaries, artifacts, or Agent State. Cloudflare D1 is not part of
this private-log path.

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

GitHub Actions is execution/orchestration evidence, Agent State is the
short-lived status/discovery index, and R2 is the detailed private log authority.
The transport-only broker is not a log reader or build executor.
