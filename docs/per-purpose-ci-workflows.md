# Per-purpose Central CI workflows (OpenProject 7160)

Status: layer 1 (shared pieces) and the first two entry workflows, **added next to the legacy
route**. Nothing here is wired to the broker yet and no legacy workflow was changed.

## Why

`central-ci-dispatch.yml` (1462 lines) routes 18 jobs by `if` on `workflow_key` and `test_profile`
into `repository-plan.yml`, `repository.yml` (2890 lines), `android.yml`, `apple.yml` and others
(11873 workflow lines in all). Tailscale, checkout, registry auth, Agent State calls, log capture
and upload are repeated inside each of them, `secrets: inherit` or one wide secret list is passed
everywhere, and the caches are wrong (below).

## Target (three layers)

1. **Composite actions and helpers** (this PR): `actions/ci-source`, `actions/ci-evidence`,
   `actions/setup-tailscale` (thin wrapper over `actions/private-git`, the one place to change
   later), plus `scripts/ci/ci_cache.py`, `ci_registry_auth.py`, `ci_validate_inputs.py`,
   `ci_lane.py`, `ci_scrub_log.py`. `actions/agent-state` and the Drive/S3 uploader are reused,
   not forked: call sites use `./central-ci/actions/agent-state` and `./central-ci/actions/google-drive`
   with the same inputs, so the mirror/S3 change drops in.
2. **Reusable per-purpose workflows** (`workflow_call`, own secrets only): later PRs. The two
   entry workflows in this PR carry the job inline first so the shared pieces can be proven; the
   job body moves unchanged into `android-build-test.yml` / `apple-build-test.yml` when the next
   entry workflow needs the same body.
3. **Thin entry workflows** (`workflow_dispatch`, input `ci_run_id`, plus `host_class`): claim the
   run, validate it for this purpose, run, always settle (`finish`, or `cancel-if-active` from the
   separate `settle` job when the run job was cancelled/skipped).

| File | Purpose | Operations |
| --- | --- | --- |
| `.github/workflows/ci-android-build.yml` | Android Gradle lint, build, JVM unit test (Linux) | `lint`, `build`, `test` (not `product_target=instrumentation`) |
| `.github/workflows/ci-apple-build.yml` | Xcode/SwiftPM lint, build, test (macOS) | `lint`, `build`, `test` |

## DECISION FOR THE OWNER: capabilities become fixed per workflow

Today Agent State project configuration (`repository_ci.capabilities`) grants capabilities per
repository at run time and Central resolves them. A per-purpose workflow knows its purpose, so it
hard-wires exactly the credentials that purpose needs and nothing is resolved from project state.
Consequences: fewer moving parts and no capability resolution code in the workflow; but a project
can no longer be granted more or less than the workflow gives (for example an Android repository
that needs `github_git` cannot get it without a new workflow or flag). Agent State still decides
what a project may request (`ci_request_policies`). Please confirm or reject this trust-model change.

### What each workflow hard-wires

| Workflow | Secrets (names only) | Capabilities | Not available |
| --- | --- | --- | --- |
| `ci-android-build.yml` | `AGENT_STATE_SUPABASE_URL/SECRET_KEY`, `SOURCE_APP_ID/PRIVATE_KEY`, `TS_OAUTH_CLIENT_ID/SECRET`, `FORGEJO_REGISTRY_USERNAME`, `CIW_MAVEN_PACKAGE_READ_TOKEN`, `GOOGLE_DRIVE_CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN/CI_LOGS_FOLDER_ID` | private_network, registry_netrc, gradle_maven | github_git, Docker Hub, fixtures, signing, registry write, backup S3, protected conformance |
| `ci-apple-build.yml` | same as Android, plus `STREAMSCAPE_*`/`XTREAM_*` fixtures **only when profile is `test`** | private_network, github_git, registry_netrc, integration_test_fixtures (test only) | Apple signing/App Store Connect, Docker Hub, gradle_maven, registry write, device |

The product process step receives no Central secret in its environment (credentials live only in
files under the ephemeral auth home); the scrub step redacts them from the private log.
Assumptions to confirm: Android needs no `github_git`; Apple needs `github_git` and
`registry_netrc` (SwiftPM private packages) and no signing for `build`/`test` (the product scripts
pass `CODE_SIGNING_ALLOWED=NO`).

## What was wrong with the old caching (from `repository.yml`, `apple_dependency_cache.py`)

* **Gradle**: only `caches/modules-2`, Linux only, and only when the `gradle_maven` grant exists.
  Key == restore key (exact), computed over every `*.gradle(.kts)`, `gradle/*`, properties and
  lock file, so *any* build-script or version edit is a complete cold miss with no fallback to the
  previous cache. Not cached at all: `wrapper/dists` (Gradle 9.7.1 downloaded every run),
  `build-cache-1` (the product sets `org.gradle.caching=true`, so task outputs were recomputed
  every run) and transforms.
* **Apple**: only the SwiftPM *binary artifact* cache, exact key on `Package.resolved`/manifests,
  no fallback. SwiftPM git repositories are cloned over the private network every run. DerivedData
  is never cached (see the proposal below).
* **Poisoning**: the save gate was `github.ref == default_branch` of ci-workflows, which is always
  true because every run executes on Central `main`; any product branch or tag could therefore
  write a cache that all later runs restore.
* No third-party action was pinned by SHA (`@v4`, `@v2`, `@main`); the old agent-state/drive
  composites are called by `@main`.

## New caching (`scripts/ci/ci_cache.py`)

* Key = repository hash + OS/arch + toolchain generation (Gradle version) + fingerprint of tracked
  dependency inputs + ISO week. Never the commit, branch, selector or run id.
* `restore-keys`: same fingerprint, older week, then any entry of the same repository/toolchain. A
  dependency change starts from the previous cache and downloads only the difference.
* Gradle paths: `modules-2`, `build-cache-1`, `wrapper/dists`. SwiftPM paths: `artifacts`,
  `repositories` (at the native macOS home, which is where SwiftPM reads them even though the
  product runs with an isolated `HOME`). Hosted runners only for Apple; self-hosted runners keep
  their persistent caches.
* **Only `develop` and `main` (non-tag) may save** (`TRUSTED_REFS`, one constant); other branches
  and tags restore only and never write. Save requires a successful product run, an exact-key miss,
  and a guard pass (no symlinks, under 4 GiB / 200000 files, no configured credential value).
* Weekly key suffix refreshes the build cache from trusted refs once a week without churning every
  run.

### Expected effect and the metric to read (not measured; no workflow was run)

* Android `build`/`test`: removes the Gradle wrapper download and, on a warm cache, reuses task
  outputs (`build-cache-1`) and the previous dependency set after a version bump. Expect the
  dependency/configuration phase to shrink to seconds and unchanged modules to report `FROM-CACHE`.
  Read: the `===== gradle-cache =====` block in the private log (`exact_hit`, `matched_key`,
  `ref_trust`) and Gradle's build summary (`N actionable tasks: X from cache`) for the first real
  runs, against the same repository's legacy `validation.repository/build` run time.
* Apple: removes SwiftPM repository clones on a hit. Read the `===== swiftpm-cache =====` block and
  the `xcodebuild` package-resolution time. Largest remaining cost is compilation (DerivedData).

### Proposal (not applied): reuse DerivedData in iptv-apple

`.ci/lib.sh` refuses a pre-existing derived-data path, so Central cannot restore DerivedData today.
Exact proposed product diff (`iptv-apple/.ci/lib.sh`):

```diff
 ci_prepare_output() {
   local path="$1"
+  # Central restores a keyed DerivedData cache into CI_DERIVED_DATA_PATH and sets CI_DERIVED_DATA_REUSE=1.
+  if [[ "${CI_DERIVED_DATA_REUSE:-}" == 1 && -n "${CI_DERIVED_DATA_PATH:-}" &&
+        "$path" == "$CI_DERIVED_DATA_PATH" && -d "$path" && ! -L "$path" ]]; then
+    return 0
+  fi
   if [[ -e "$path" || -L "$path" ]]; then
```

Central then adds a DerivedData cache (key: repository, Xcode version, `Package.resolved`,
`.xcodeproj` package refs; trusted refs save) and sets those two variables.

## Proposal: the CI run names the entry workflow (for agent-state-supabase; not implemented here)

* `agent_private.ci_request_policies` (the existing allow-list, identity `(workflow_key,
  test_profile)`): add `entry_workflow text not null`, check `entry_workflow ~
  '^ci-[a-z0-9][a-z0-9-]*\.yml$'` and `octet_length <= 64`. This is the allow-list source: set only
  by migration/owner, never by the requester.
* `agent_private.ci_runs`: add `entry_workflow text not null` and `host_class text` (check
  `in ('linux-hosted','linux-high-capacity','macos-hosted','macos-high-capacity')`).
  `request_ci_run(text,text,jsonb)` copies `entry_workflow` from the matching policy row and
  resolves `host_class` once, at request time, with the existing server-side host-class decision
  (`resolve_repository_ci_host_class`), replacing the per-run resolution inside `repository.yml`.
  Existing rows backfill to `central-ci-dispatch.yml` and null.
* ci-broker dispatches `.github/workflows/<entry_workflow>` on `main` with inputs exactly
  `{ci_run_id, host_class}` (an undeclared input makes GitHub reject the dispatch, which is the
  desired fail-closed behaviour) and judges nothing else.
* The entry workflow claims the run and re-checks it: `workflow_key` accepted by this workflow,
  `test_profile` among its operations, semantic inputs valid against
  `contracts/repository-ci-v1.json`, `host_class` among its reviewed classes. A mismatch fails
  before any credential is used.
* Lanes: the key prefix `ci-wf-` differs from the legacy `central-ci-` lanes so old and new routes
  never cancel each other while both exist.

## Not in this PR (follow-ups, in order)

1. Parity table `docs/ci-parity.yaml` plus a test that fails when a legacy `(workflow_key,
   test_profile)` pair of `central-ci-dispatch.yml` or a contract capability has no entry workflow.
2. Reusable layer for build/test, then backend/repository lint-build-test-full (with
   `registry_oci_publish`, `protected_deployed_conformance`, Docker Hub read), helm verify and
   release, image build/publish (buildah layer cache), reproducibility, release workflows
   (Android Play, TestFlight, binary, SwiftPM, library-package, Maven), cluster inspection,
   screenshots, device test, maintenance.
3. Evidence parity: the old executor also packages bounded artifacts (`CI_ARTIFACT_DIR`, e.g.
   `.xcresult`) into a Drive zip, relays `CI_PROGRESS_V1` progress to Agent State and writes a
   phase timeline. These entry workflows keep only the scrubbed private log (bounded capture,
   Drive/issue fallback); progress relay, artifact zip and timeline are not yet ported.
4. Per-workflow: the `ci-secrets-and-capabilities.md` reference in iptv-skills and `INVENTORY.yaml`
   list the new files/secret names (not edited here to keep this change additive and conflict-free).
5. `actions/private-git` still pulls `tailscale/github-action@v4` by tag; pin it when that action
   is next touched.

## Validation

`python3 -m unittest tests.test_ci_per_purpose_workflows` (15 tests: input validation, lanes,
cache keys/fallbacks/save rule/guard, scrub, auth files, SHA pinning, secret scoping, settle) and
`tests.test_bash_portability`. `actionlint` is not installed on the authoring Mac and was not run;
`self-check.yml` lints a fixed list of three workflows and does not include the new files yet
(follow-up). No workflow was dispatched.
