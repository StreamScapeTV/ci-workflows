# Per-ecosystem Central CI workflows (OpenProject 7160)

Status: PR 1 of the split. Gradle and Xcode/Swift ecosystem workflows plus the shared composites,
**added next to the legacy route**. No legacy workflow is changed. Design v3 (owner, 20 Nov 2026):
no ci-broker, no Agent State CI lifecycle, no `ci_run_id`; the agent runner dispatches a workflow
directly and finds the run by the request id in `run-name`.

## Contract for the agent runner (`ci` module, backend `github-actions`)

Dispatch `.github/workflows/ci-<ecosystem>.yml` on `main` with exactly these inputs (an undeclared
input is rejected by GitHub):

| Input | Meaning |
| --- | --- |
| `request_id` | UUID/id chosen by the runner; appears in `run-name` (`CI gradle <request_id>`) so the run can be found |
| `repository` | `StreamScapeTV/<name>` (anything else fails in the `plan` job before a credential is used) |
| `ref`, `is_tag` | branch or tag name, `true`/`false` |
| `operation` | the TOP operation: `lint`, `test` or `build`. Operations form a hierarchy lint < test < build (< full/ui-test < release in later workflows); the workflow runs every operation below the requested one first, as stage jobs of the same run (`needs:` chain), so a build or release never depends on another run |
| `options` | JSON object of reviewed semantic options (default `{}`) |

Options are validated against the semantic fields of `contracts/repository-ci-v1.json`
(`product_target`, `build_configuration`, `test_selectors`, `ui_mode`, ...) plus `scheme` for Xcode.
No commands, environment maps, runner labels or secret names are representable.
Status, logs and cancel are plain GitHub run operations done by the runner backend.

## Layers

1. **Composite actions** (`actions/`): `ci-setup` (read-only GitHub App token, exact checkout,
   Tailscale, ephemeral Git/registry auth), `ci-cache` (restore/save with the integration-branch
   rule), `setup-tailscale` (thin wrapper over `actions/private-git`, the one place to change the
   mechanism). Helpers in `scripts/ci/`: `ci_validate_inputs.py`, `ci_stage.py` (command
   resolution), `ci_run_stage.py` (run + scrub + numbered log chunks), `ci_cache.py`,
   `ci_registry_auth.py`, `ci_scrub_log.py`.
2. **Reusable stage workflows**: `ci-gradle-stage.yml`, `ci-xcode-stage.yml` (`workflow_call`, explicit
   secrets only, never `secrets: inherit`).
3. **Entry workflows**: `ci-gradle.yml`, `ci-xcode.yml` (`workflow_dispatch`): job `plan`
   (validate), then separate jobs `lint`, `test`, `build` (named after the operation, `needs:` chain) so GitHub itself shows stage and time. Small tests are expected locally by default; the remote workflows are mainly for build/full/ui-test/release.
   Concurrency: a safety-net group per repository + ref with `cancel-in-progress: false` (one pipeline per project and branch; the agent runner enforces this and cancels by request id, workflows never cancel another request). Each stage is its own job with its own checkout and cache restore; Gradle/SwiftPM caches and the Gradle build cache are what avoid repeating work between the `lint`, `test` and `build` jobs. Every later stage runs only when the previous one succeeded.
   There is **no global macOS or device cap**.

Nothing calls Agent State, a mirror, Cloudflare Access or Google Drive. **No workflow has a push, tag, pull_request or schedule trigger** (owner decision, 20 Nov 2026): CI runs only when an agent requests it through the runner (`workflow_dispatch`); the stage workflows are `workflow_call` only. Cache writes therefore happen only in explicitly requested runs on the integration branch (non-tag), and the cache-warm entry per toolchain is likewise an explicit request.

## Convention over per-project scripts

A project that follows the ecosystem standard needs no `.ci` directory. See
[`ci-conventions/gradle.md`](ci-conventions/gradle.md) and [`ci-conventions/xcode.md`](ci-conventions/xcode.md).
Transition: a tracked executable `.ci/<stage>.sh` is used instead of the convention until the
project conforms (iptv-android, iptv-apple), then deleted. The workflows fail with a message that
names what the project must provide.

## Logs (S3, numbered chunks)

`ci_run_stage.py` appends the scrubbed output of a stage to one growing file. The shared
`actions/s3-log-stream` action (PR 1213, referenced at `@main`; **this PR needs 1213 merged first**) is
started before the stage and stopped with `if: always()` after it: it uploads the file every 5 s as
`logs/<project>/<request_id>/<stage>/<seq>.log` (`<project>` = repository name) and writes the merged
`final.log` per stage; the run's prefix is `logs/<project>/<request_id>/`. The S3 key
(`S3_GITHUB_TRANSPORT_ACCESS_KEY` / `S3_GITHUB_TRANSPORT_SECRET_KEY`) lives only in the short start
step that presigns the PUT URLs of this request's own log objects (6 h expiry); the background
uploader and the product code in the same job never see a bucket key (a leaked URL can only overwrite
this request's log objects). The stage runner redacts every `CI_SECRET_*` value before it writes
the log. No new S3 key is needed. A cancelled run still flushes: the stop step runs with
`if: always()`, writes the partial `final.log`, and an S3 outage there never reds the stage.

## DECISION FOR THE OWNER: capabilities fixed per workflow

Each workflow hard-wires the credentials its purpose needs; nothing is resolved from project state.

| Workflow | Secrets (names only) | Tailscale | Not available |
| --- | --- | --- | --- |
| `ci-gradle` (all stages) | `SOURCE_APP_ID`, `SOURCE_APP_PRIVATE_KEY`, `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `FORGEJO_REGISTRY_USERNAME`, `CIW_MAVEN_PACKAGE_READ_TOKEN`, `S3_GITHUB_TRANSPORT_ACCESS_KEY`, `S3_GITHUB_TRANSPORT_SECRET_KEY` | yes (checkout of private deps, Forgejo Maven, S3) | github.com Git credential, Docker Hub, fixtures, signing, registry write, backup S3, protected conformance |
| `ci-xcode` lint, build | same names as Gradle | yes (Forgejo SwiftPM, S3) | Apple signing/App Store Connect, Docker Hub, fixtures, registry write |
| `ci-xcode` test | same plus `STREAMSCAPE_*` / `XTREAM_*` fixtures, **only this job** | yes | same as above |

Git/netrc access to github.com is enabled for Xcode (SwiftPM private packages) and not for Gradle;
Gradle gets the private-Maven properties. Assumptions to confirm: Android needs no github.com Git
credential; Xcode `build`/`test` need no signing (the convention passes `CODE_SIGNING_ALLOWED=NO`).

## Caching (`ci_cache.py`, `actions/ci-cache`)

Old route, what was wrong: Gradle cached only `caches/modules-2`, Linux only and only with a grant;
key == restore key over every build script, so any edit was a cold miss with no fallback; no
`wrapper/dists` (the Gradle distribution downloaded every run) and no `build-cache-1` (the product
sets `org.gradle.caching=true`). Apple cached only SwiftPM binary artifacts, exact key, no
fallback; repositories and DerivedData never. The save gate `github.ref == default_branch` of
ci-workflows is always true (every run executes on Central `main`), so any product branch or tag
could write caches. No action was pinned by SHA.

New rules (owner v2/v3): Gradle (wrapper, dependencies, build cache) and SwiftPM only for now;
Xcode DerivedData and Maven follow in their PRs; no image layer cache. **Only the integration
branch writes**: `develop`, or `main` for a repository without `develop` (checked with
`gh api repos/<r>/git/ref/heads/develop`: only an HTTP 404 means "no develop"; any other error fails closed, restore only); other branches, pull
requests and tags restore only. Keys are narrow for the shared 10 GB budget: repository hash +
OS/arch + Gradle version + dependency fingerprint + day, one entry per such tuple (a per-run key would multiply writes against the 10 GB budget; the daily key refreshes the build cache once a day);
`restore-keys` fall back to the same fingerprint of an older day, then to the same
repository/toolchain. A save needs a successful stage, an exact-key miss (`lookup-only`) and a guard
pass (no symlinks, < 4 GiB / 200000 files, no credential value).

Expected effect (reasoned, nothing was run): no Gradle wrapper download, a dependency bump
downloads only the delta, unchanged modules come from the build cache; SwiftPM clones/binaries
restored on a hit. Metric for the first real runs: the `== gradle-cache: exact_hit=... ==` /
`== swiftpm-cache: ... ==` line at the top of each stage log and Gradle's `N actionable tasks: X
from cache`, compared with the same repository's legacy run time.
A cache-warm entry per toolchain (runs a stage on `develop` to seed the keys) is a follow-up PR.

### Proposal (not applied): DerivedData reuse in iptv-apple

`.ci/lib.sh` refuses a pre-existing derived path, so DerivedData cannot be restored. Proposed diff
(`iptv-apple/.ci/lib.sh`, `ci_prepare_output`):

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

The conventional `xcodebuild` path already uses `-derivedDataPath <work>/derived-<stage>`, so the
Xcode DerivedData cache can key on it directly.

## Not in this PR

Parity table (legacy `(workflow_key, test_profile)` pair -> new ecosystem workflow, with a test that
fails on an unmapped pair), python, node, maven, swiftpm/binary workflows, shared Docker image and
Helm building blocks and their conventions pages, releases, cache-warm entries, DerivedData and
Maven caches, `INVENTORY.yaml` and secret-catalog updates, pinning
`tailscale/github-action` inside `actions/private-git`, the artifact (`.xcresult`) package of the
old executor.

## Validation

`python3 -m unittest tests.test_ci_per_purpose_workflows` (input validation, stage resolution for
Gradle and Xcode conventions and transition scripts, cache keys/fallbacks/integration-branch rule/
guard, scrub, auth files, an end-to-end run of the stage runner proving the secret never reaches the
product process or the log and the exit code is kept; S3 log stream start/stop placement; workflow shape: inputs, `run-name`, lanes, SHA pinning, secret scoping, no Agent State).
`actionlint` is not installed on the authoring Mac and was not run. No workflow was dispatched.
