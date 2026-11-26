# Per-ecosystem Central CI workflows (OpenProject 7160)

Status: Gradle, Xcode/Swift, Node and Python ecosystem workflows plus the shared composites,
**added next to the legacy route** (each runs all its requested stages in one job, see below). No legacy workflow is changed. Design v3 (owner, 20 Nov 2026):
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
| `operation` | the TOP operation: `lint`, `test`, `build` or `release`. Operations form a hierarchy lint < test < build < release (full/ui-test in later workflows); the workflow runs every operation below the requested one too, as stages of the same run and the same job, so a build or release never depends on another run |
| `options` | JSON object of reviewed semantic options (default `{}`) |

Options are validated against the semantic fields of `contracts/repository-ci-v1.json`
(`product_target`, `build_configuration`, `test_selectors`, `ui_mode`, `release_kind`, ...) plus `scheme` for Xcode.
`build_identity` is never an option: a Gradle or Xcode release derives it from the tag (section Native release).
No commands, environment maps, runner labels or secret names are representable.
Status, logs and cancel are plain GitHub run operations done by the runner backend.

## Layers

1. **Composite actions** (`actions/`): `ci-setup` (read-only GitHub App token, exact checkout,
   Tailscale, ephemeral Git/registry auth), `ci-cache` (restore/save with the integration-branch
   rule), `setup-tailscale` (thin wrapper over `actions/private-git`, the one place to change the
   mechanism). Helpers in `scripts/ci/`: `ci_validate_inputs.py`, `ci_stage.py` (command
   resolution and the Xcode/Node/Python plans), `ci_run_gradle_job.py`, `ci_run_xcode_job.py` and
   `ci_run_job.py` (Node and Python): each runs every requested stage of its ecosystem in one job and
   writes one scrubbed log file per stage; `ci_run_stage.py` is their shared library (log file, status
   lines, child environment); `ci_cache.py`, `ci_registry_auth.py`, `ci_scrub_log.py`.
2. **Reusable stage workflows**: `ci-gradle-stage.yml`, `ci-xcode-stage.yml`, `ci-node-stage.yml`,
   `ci-python-stage.yml` (`workflow_call`, explicit secrets only, never `secrets: inherit`); each holds ONE
   job named `stages`. Release stages: `ci-oci-release-stage.yml` (Node, Python), `ci-xcode-release-stage.yml`
   and `ci-gradle-release-stage.yml` (one job named `release` each).
3. **Entry workflows**: `ci-gradle.yml`, `ci-xcode.yml`, `ci-node.yml`, `ci-python.yml` (`workflow_dispatch`): job `plan`
   (validate), then ONE job named after the ecosystem (`gradle`, `xcode`, `node`, `python`; GitHub shows `<ecosystem> / stages`) that runs every requested stage; every ecosystem adds a `release` job after it (operation `release` only). There is no `lint -> test -> build` job chain; the stage states are static steps of that job (below). Small tests are expected locally by default; the remote workflows are mainly for build/full/ui-test/release.
   Concurrency: a safety-net group per repository + ref with `cancel-in-progress: false` (one pipeline per project and branch; the agent runner enforces this and cancels by request id, workflows never cancel another request). All ecosystems: all requested stages run in ONE job (`<ecosystem> / stages`): Gradle in one Gradle invocation (compilation, daemon and build cache shared), Xcode with `build-for-testing` once, `test-without-building` and SwiftLint alongside on one macOS runner, Node and Python with one install and then parallel processes (Node starts lint after the build when both are requested); each stage has its own S3 log stream and the job carries the stage states as static steps (`stage <op> requested`, `stage <op> passed|failed`, plus `skipped` for Xcode, Node and Python) that ci-broker reads. A failed lint is a warning and never fails a build/test request; it fails the request only when lint is the requested top operation.
   There is **no global macOS or device cap**.

Nothing calls Agent State, a mirror, Cloudflare Access or Google Drive. **No workflow has a push, tag, pull_request or schedule trigger** (owner decision, 20 Nov 2026): CI runs only when an agent requests it through the runner (`workflow_dispatch`); the stage workflows are `workflow_call` only. Cache writes therefore happen only in explicitly requested runs on the integration branch (non-tag), and the cache-warm entry per toolchain is likewise an explicit request.

## Convention over per-project scripts

A project that follows the ecosystem standard needs no `.ci` directory. See
[`ci-conventions/gradle.md`](ci-conventions/gradle.md), [`ci-conventions/xcode.md`](ci-conventions/xcode.md), [`ci-conventions/node.md`](ci-conventions/node.md) and [`ci-conventions/python.md`](ci-conventions/python.md).
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
`if: always()`, writes the partial `final.log`. A log that cannot reach S3 fails the job (logs live only in S3, never in the Actions log).

## DECISION FOR THE OWNER: capabilities fixed per workflow

Each workflow hard-wires the credentials its purpose needs; nothing is resolved from project state.

| Workflow | Secrets (names only) | Tailscale | Not available |
| --- | --- | --- | --- |
| `ci-gradle`, `ci-node`, `ci-python` (all stages) | `SOURCE_APP_ID`, `SOURCE_APP_PRIVATE_KEY`, `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `FORGEJO_REGISTRY_USERNAME`, `CIW_MAVEN_PACKAGE_READ_TOKEN`, `S3_GITHUB_TRANSPORT_ACCESS_KEY`, `S3_GITHUB_TRANSPORT_SECRET_KEY` | yes (checkout of private deps, Forgejo Maven, S3) | github.com Git credential, Docker Hub, fixtures, signing, registry write, backup S3, protected conformance |
| `ci-xcode` (all stages) | same names as Gradle plus `STREAMSCAPE_*` / `XTREAM_*` fixtures, materialized **only when the test stage runs** and visible to the test stage only (`CI_TEST_FIXTURE_FILE`) | yes (Forgejo SwiftPM, S3) | Apple signing/App Store Connect, Docker Hub, registry write |
| `ci-xcode` `release` job only | the Gradle names plus `APPLE_TEAM_ID`, `APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_P8_BASE64` (to `.ci/release.sh` as `CI_APPLE_TEAM_ID`, `CI_APP_STORE_CONNECT_*`) | yes | fixtures, Play, Docker Hub, registry write |
| `ci-gradle` `release` job only | the Gradle names plus `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64`, `ANDROID_PLAY_UPLOAD_KEYSTORE_BASE64` (written as 0600 files, paths in `CI_ANDROID_PLAY_SERVICE_ACCOUNT_PATH` / `CI_ANDROID_PLAY_UPLOAD_KEYSTORE_PATH`), `ANDROID_PLAY_UPLOAD_KEYSTORE_PASSWORD`, `ANDROID_PLAY_UPLOAD_KEY_ALIAS`, `ANDROID_PLAY_UPLOAD_KEY_PASSWORD` (as `CI_ANDROID_PLAY_*`) | yes | fixtures, Apple, Docker Hub, registry write |

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

## What the owner must provide

No new secret is invented by these workflows; they use names that already exist in the repository.

| Item | Needed by | Note |
| --- | --- | --- |
| `S3_GITHUB_TRANSPORT_ACCESS_KEY` / `S3_GITHUB_TRANSPORT_SECRET_KEY` | every stage workflow (live logs) | Present only in the short presign step of `actions/s3-log-stream`; the key can be prefix-scoped to `logs/*` (put-only) because uploads use presigned URLs. A prefix-scoped key is recommended, not required. |
| `S3_ENDPOINT` / `S3_BUCKET` | every stage workflow (live logs), legacy uploads | GitHub Actions secrets, mapped explicitly; also scrubbed from logs. A repository variable of the same name is only a fallback. |
| `SOURCE_APP_ID` / `SOURCE_APP_PRIVATE_KEY` | every workflow | Token is scoped to the one source repository. |
| `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` | every workflow | Private network for the registry and S3 endpoint. |
| `FORGEJO_REGISTRY_USERNAME` / `FORGEJO_REGISTRY_TOKEN` | node and python `release` operation only | Registry write; exists only in the publish step (`ci-oci-release-stage.yml`). Optional `DOCKERHUB_USERNAME` / `DOCKERHUB_READ_TOKEN` for authenticated base image pulls. |
| `FORGEJO_REGISTRY_USERNAME` / `CIW_MAVEN_PACKAGE_READ_TOKEN` | gradle, xcode, python, node | Private registry read access. |
| `STREAMSCAPE_*`, `XTREAM_*` fixture secrets | xcode `test` job only | Already used by the legacy path. |
| Project setting: ecosystem workflow | runner/OpenProject | The project names `gradle`, `xcode` or `python`; nothing is auto-detected. |

## Input contract with the runner and stage identifiers

Every entry workflow (`ci-gradle.yml`, `ci-xcode.yml`, `ci-node.yml`, `ci-python.yml`) is dispatched with exactly these
inputs: `request_id`, `repository`, `ref`, `is_tag`, `operation` (required: the top requested operation),
`target` (optional, accepted and ignored) and `options` (JSON, default `{}`). Any other input makes GitHub
answer 422, so the runner must not send more. `operation` today is `lint`, `test`, `build` or `release`; `full`
and `ui-test` are rejected by `plan` until their workflows exist.

Hierarchy: operation X runs every stage below it in the same run and the same job
(`lint` < `test` < `build`): `lint` runs lint; `test` runs lint and test; `build` runs lint, test and build. The
stages do not wait for lint (the build starts first or together; Node starts lint after its build). A failed
`lint` is a warning and never fails a `test` or `build` request; `test` and `build` decide the request; a
request whose top operation is `lint` fails when lint fails.

Stage identifier: the stage id is `lint`, `test` or `build`. It is the `<stage>` segment of the S3 path
`logs/<project>/<request_id>/<stage>/<seq>.log` (and `final.log`). The GitHub API names the jobs of a
reusable workflow `<caller job> / <called job>`: one job per ecosystem, here `gradle / stages`, `xcode / stages`,
`node / stages`, `python / stages` (the `release / ...` job of Node and Python). The per-stage state is carried by static steps of that
job (`stage <op> requested`, `stage <op> passed|failed|skipped`, conclusion `success` when that step ran), which ci-broker
reads (stage conclusion `warning` = lint failed in a request that does not depend on lint). Older shapes with one job
per stage (`lint / lint`) are still read by the broker.

## Release (images and charts)

`ci-node.yml` and `ci-python.yml` accept operation `release` (lint, test, build, then release; lint never gates it). The release stage is the reusable
`ci-oci-release-stage.yml` with the building block `actions/ci-release` (`scripts/ci/ci_release.py`). See `docs/ci-conventions/node.md` for the `.ci/release.json` declaration, the
version authority (tag equal to the root `VERSION`, or `main`), the immutable publication and read-back.

## Native release (Xcode and Gradle, AS ci-workflows#1220)

`ci-xcode.yml` and `ci-gradle.yml` accept operation `release` as well: lint, test and build run in the
`<ecosystem> / stages` job as for `build`, then (only when that job succeeded; a failed lint never gates it) the
`release` job (`release / release`, stage `release`) runs the repository's tracked executable `.ci/release.sh`
(there is no release convention) through `ci_run_stage.py`, with its own S3 log stream
`logs/<project>/<request_id>/release/`. A tag (`is_tag=true`) is required; a branch is refused in `plan`.

- `CI_OPERATION=release`, `CI_HOST_OS` `macos` (Xcode, `macos-latest`) or `linux` (Gradle, `ubuntu-latest`, JDK 25),
  `CI_HOST_CLASS`, `CI_LOG_DIR`, `CI_ARTIFACT_DIR`, `CI_PROGRESS_FILE` as for every stage.
- `CI_INPUTS_JSON` = `{"schemaVersion":1,"inputs":{"release_kind":...,"build_identity":...}}` (plus `product_target`
  when the request carries one). `release_kind` (`prepare` or `publish`) comes from the request options, i.e. the
  project's CI profile, and is required. `build_identity` comes from the tag only (a request that supplies it is
  refused): Xcode passes the tag itself (an app's `<marketing-version>_<build>`, a library's `VERSION`);
  Gradle passes the build number of its `<marketing-version>_<build>` tag (the Android versionCode); any other Gradle
  tag is refused in `plan`. The Gradle release also gets `CI_ANDROID_PLAY_SOURCE_IS_TAG=true` and
  `CI_ANDROID_PLAY_RELEASE_VERSION=<marketing-version>`, so the Play script checks the tag against `versionName`.
- The store credentials (table above) exist only in this job and reach only the release process; their values are
  redacted from the stage log. The lint/test/build job never receives them. Cache: restore only (a tag never saves).
- A tag request from ci-broker (`requested_by` `tag push`) carries the profile's `release` options, so the profile's
  `remote.operations.release.options` must hold `release_kind`.
