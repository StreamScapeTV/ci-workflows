# Convention: Xcode and Swift — workflow `ci-xcode.yml`

A repository chosen for the Xcode workflow needs **no `.ci` directory** if it provides one of:

**A. Swift package** (`Package.swift`, no `.xcworkspace`/`.xcodeproj` at the root):

| Stage | Command |
| --- | --- |
| `build` | `swift build -c <debug\|release>` |
| `test` | `swift test -c <debug\|release>` |

**B. Xcode project**: exactly one `.xcworkspace` or `.xcodeproj` at the repository root and a shared
scheme named in the request (`options.scheme`):

| Stage | Command |
| --- | --- |
| `build` | `xcodebuild -workspace\|-project <container> -scheme <scheme> -configuration <Debug\|Release> -destination <d> -derivedDataPath <work>/derived-build build CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO` |
| `test` | same with `test`; **macOS only** (`options.product_target=macos`). Simulator tests are not standardised yet: keep `.ci/test.sh` (transition) |

`options.product_target` selects the destination: `ios` and `tvos` use the generic simulator
destination (build only), `macos` uses `platform=macOS`. `options.build_configuration` is `debug`
(default) or `release`.

**Lint**: a tracked `.swiftlint.yml`; the workflow runs `swiftlint lint --strict`. Without it the `lint` stage is **skipped** (green, with the reason in the log), never red.

A stage the convention does not cover is skipped the same way: `test` for `product_target` `ios`/`tvos` (simulator tests are not standardised; keep `.ci/test.sh` to run them). A project can therefore request `build` without lint or simulator tests.

Dependencies come from SwiftPM (`Package.resolved` is the cache fingerprint). Private packages are
reached with the ephemeral netrc/Git credential the workflow sets up (github.com and the Forgejo
registry over Tailscale); the project never reads credentials. No signing, provisioning,
TestFlight or App Store step exists in the lint/test/build stages.

**Release** (operation `release`, from a tag only): after lint, test and build, a separate `release` job runs the
tracked executable `.ci/release.sh` (no convention) on macOS with `CI_INPUTS_JSON` = `release_kind` (profile
option) and `build_identity` = the tag. That job alone gets the App Store Connect credentials as
`CI_APPLE_TEAM_ID`, `CI_APP_STORE_CONNECT_KEY_ID`, `CI_APP_STORE_CONNECT_ISSUER_ID` and
`CI_APP_STORE_CONNECT_API_KEY_P8_BASE64`. See `per-purpose-ci-workflows.md`, section Native release.

Test fixtures: the `test` stage (only) gets the shared integration fixtures as a JSON file named by
`CI_TEST_FIXTURE_FILE`.

Transition: a tracked executable `.ci/lint.sh`, `.ci/build.sh` or `.ci/test.sh` is used instead
until the project follows the convention; then delete it.

Caches (integration branch writes only): SwiftPM `artifacts` and `repositories`.
Only the last stage of a request saves, under a key that ends with that stage (`...-<day>-build`), so a
`build` request leaves a cache holding everything its build downloaded. The stage log starts with
`== <kind>-cache: exact_hit=... ==`, then `== <kind>-cache-restore: seconds=N ==`; the saving stage
ends with `== <kind>-cache-save: seconds=N saved=<true|false> ==` (kind `gradle` or `swiftpm`). DerivedData reuse
needs a small product change (see `per-purpose-ci-workflows.md`).

## Where the output goes

The tool output of every stage is written only to the S3 stage log (`logs/<repo>/<request_id>/<stage>/<seq>.log`, `final.log`) and never to the Actions log of this repository. The Actions step shows only status lines: `== stage <name> started s3=<prefix> ==` and `== stage <name> finished result=ok|failed|skipped exit=N s3=<prefix> ==`. Tool `::error::` lines stay inside the S3 log. A log stream that cannot start or finish fails the job (the log would be lost).

## One job, one runner

All requested stages run in ONE job (`xcode / stages`) on one macOS runner: nothing compiles twice. Central chooses that runner from the repository (`scripts/ci/ci_execution_class.py`): the `streamscape-media` repository runs on the owner's persistent self-hosted Mac (class `macos-high-capacity`, also for its release job), every other project on GitHub-hosted `macos-latest`; a project or request cannot choose it, and `CI_HOST_CLASS` tells the stage process which one it is (see [`../per-purpose-ci-workflows.md`](../per-purpose-ci-workflows.md#execution-class-macos-runner-central-only)). On the persistent Mac, `TMPDIR` is per run and the job leaves no keychain, workspace or `~/.netrc` behind; persistent toolchain caches in the user home stay. On an Xcode project the `build` stage is `xcodebuild ... build-for-testing` and the `test` stage is `xcodebuild ... test-without-building` on the same `-derivedDataPath` (a request with only `build` or only `test` runs a plain `build` / `test`); a Swift package builds once in `.build` and `swift test` reuses it. `lint` (SwiftLint) runs as a parallel process next to that chain, never in front of it. A failed build makes the test that needs it `failed` (noted in its log). `lint` is a warning: a failed lint never fails a `build` or `test` request, only a request whose top operation is `lint`. A stage without a convention is `skipped`. With transition scripts (`.ci/*.sh`) the stages run one after the other, build first, and a failed stage never stops the next. Only the `test` stage sees `CI_TEST_FIXTURE_FILE`.
