# Convention: Gradle (Android and JVM) — workflow `ci-gradle.yml`

A repository chosen for the Gradle workflow needs **no `.ci` directory** if it provides:

1. A tracked, executable `gradlew` (mode 100755) and `gradle/wrapper/gradle-wrapper.properties`
   whose `distributionUrl` names the Gradle version. The version selects the cache generation.
2. The standard lifecycle tasks. The workflow runs every requested stage in ONE job and ONE invocation,
   `./gradlew --no-daemon --continue --parallel <tasks>` (for example `assemble check` for lint+test+build;
   `check` contains `test`), so compilation happens once. A `--dry-run` of each stage's own tasks decides
   which task output and which failure belongs to which stage (the S3 logs are per stage):

| Stage (operation) | Command | What the project provides |
| --- | --- | --- |
| `lint` | `check -x test` (Android: `lintDebug`) | verification tasks attached to `check` (Android lint, detekt, ktlint, ...) |
| `build` | `assemble` (Android: `assembleDebug`, or `assembleRelease` with `build_configuration=release`) | the artifacts to build |
| `test` | `test` (Android: `testDebugUnitTest`) | JVM unit tests (no emulator; emulator/instrumentation is a separate workflow) |

3. Product specifics in Gradle itself (tasks, `gradle.properties`, build types), never in a CI script.
   Enable the build cache (`org.gradle.caching=true`) and, if wanted, the configuration cache.
4. Dependencies: public repositories, or the private Forgejo Maven reached through the properties the
   workflow writes (`centralRegistryUsername`, `centralRegistryReadToken` in the ephemeral
   `GRADLE_USER_HOME/gradle.properties`). Read them with `providers.gradleProperty`.
An Android project (a `com.android.*` plugin in the root/`app` build file or the version catalog) runs the debug variant only (deliberate narrowing: release, benchmark and R8 are no longer part of the default check; only `build_configuration=release` adds `assembleRelease` to the build stage; lint and test stay Debug), with `--max-workers=3`: building all variants (benchmark, release, R8) in parallel is the inferred (not proven) cause of a 70-minute stall of a hosted run.
5. JDK 25 (Temurin) is provided by the workflow.

Request options: `build_configuration` (`debug`/`release`), `test_selectors` (test stage), `product_target`.
They reach a transition script as `CI_INPUTS_JSON`. The convention honors only `build_configuration` (Android build stage); the other options are ignored today.

A failed `lint` is a warning (reported, findings in the S3 log) and does not fail a `test` or `build` request; a failed `test` or `build` fails the request; when `lint` itself is the requested operation a failure fails it.
With transition scripts (`.ci/*.sh`) the stages run one after the other in the same job, build first, and a failed stage never stops the next one; delete the scripts to get the single shared invocation.

Watchdog (convention path): after 20 minutes without output the runner writes a `jstack` of every JVM into all stage logs; after 90 minutes (job limit 120) it dumps again and kills the Gradle process group, marks the stages failed and still writes each stage's `exit` line and final log (`--stall-minutes`, `--timeout-minutes`). Kills of the runner itself by GitHub cannot be caught.

Failure messages name what is missing (for example "commit an executable gradlew"). Transition: a
tracked executable `.ci/lint.sh`, `.ci/build.sh` or `.ci/test.sh` is used instead of the command
above until the project follows the standard; then delete the script.

Cache save runs with `always()`: after a failed stage, a watchdog kill, cancellation and the job timeout too. Only a fully successful request saves the exact key; anything else saves `<key>-partial` (one per day and slot, never an exact hit, only a restore-key fallback). Untrusted refs and tags never save.

Caches (integration branch writes only): Gradle `caches/modules-2`, `caches/build-cache-1`, `wrapper/dists`.
Only the last stage of a request saves, under a key that ends with that stage (`...-<day>-build`), so a
`build` request leaves a cache holding everything its build downloaded. The stage log starts with
`== <kind>-cache: exact_hit=... ==`, then `== <kind>-cache-restore: seconds=N ==`; the saving stage
ends with `== <kind>-cache-save: seconds=N saved=<true|false> ==` (kind `gradle` or `swiftpm`).

## Where the output goes

The tool output of every stage is written only to the S3 stage log (`logs/<repo>/<request_id>/<stage>/<seq>.log`, `final.log`) and never to the Actions log of this repository. The Actions step shows only status lines: `== stage <name> started s3=<prefix> ==` and `== stage <name> finished result=ok|failed|skipped exit=N s3=<prefix> ==`. Tool `::error::` lines stay inside the S3 log. A log stream that cannot start or finish fails the job (the log would be lost).
