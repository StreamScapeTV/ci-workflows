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

**Lint**: a tracked `.swiftlint.yml`; the workflow runs `swiftlint lint --strict`.

Dependencies come from SwiftPM (`Package.resolved` is the cache fingerprint). Private packages are
reached with the ephemeral netrc/Git credential the workflow sets up (github.com and the Forgejo
registry over Tailscale); the project never reads credentials. No signing, provisioning,
TestFlight or App Store step exists in this workflow (those are release workflows).

Test fixtures: the `test` stage (only) gets the shared integration fixtures as a JSON file named by
`CI_TEST_FIXTURE_FILE`.

Transition: a tracked executable `.ci/lint.sh`, `.ci/build.sh` or `.ci/test.sh` is used instead
until the project follows the convention; then delete it.

Caches (integration branch writes only): SwiftPM `artifacts` and `repositories`. DerivedData reuse
needs a small product change (see `per-purpose-ci-workflows.md`).
