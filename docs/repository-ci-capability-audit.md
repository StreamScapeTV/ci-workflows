# Repository CI v1 capability audit

This audit freezes the **non-host shared-infrastructure boundary** for repository-owned CI v1.
It is derived from the private migration ledger and the still-live compatibility workflows, but
it intentionally publishes only reusable consumer classes and infrastructure requirements.
Concrete consumer repositories, private host bindings, secrets, and migration status remain in
Agent State.

## Classification rules

Every observed requirement belongs to exactly one class:

1. **Central baseline** — available to every repository run without a capability grant.
2. **Optional generic capability** — shared infrastructure authorized privately through Agent State.
3. **Repository-owned behavior** — product/toolchain commands implemented only by fixed `.ci/*` scripts.
4. **Separately governed compatibility behavior** — retained outside the generic executor until a
   reusable migration contract and live proof exist.

Host-class selection is a separate Central baseline policy, not a capability. The finite host-class
catalog and private OS-aware host-policy binding are documented in the canonical repository-CI
onboarding guide. A tracked repository execution plan may select multiple reviewed OS children for
one semantic parent operation; it never selects runner labels or capabilities.

## Current-fleet matrix

| Requirement family | Classification | Repository CI v1 treatment |
| --- | --- | --- |
| Exact source checkout, branch/tag namespace selection and observed SHA | Central baseline | Central admits and records the exact source before product execution. |
| Agent State start/source-observation/finish lifecycle | Central baseline | Central owns lifecycle settlement; repository scripts do not call Agent State. |
| Fixed operation to `.ci/*` entrypoint mapping | Central baseline | Only `build`, `test`, `full`, `ui-test`, and separately authorized `release` map to fixed tracked executable files. |
| Multi-OS execution-plan fan-out | Central baseline | Optional tracked `.ci/execution-plan.json` selects only unique reviewed OS identifiers for one semantic operation; Central runs children concurrently, resolves each child host class privately, and settles one parent lifecycle after all children. |
| Versioned bounded semantic inputs | Central baseline | `CI_INPUTS_JSON` contains only reviewed semantics; product scripts validate and translate them. |
| Private log, progress and bounded artifact locations | Central baseline | Central owns locations, scrubbing, packaging, private Drive transport, retention and cleanup. |
| Private network access for the duration of one operation | Optional generic capability: `private_network` | Central establishes and cleans up the reviewed private network around the whole repository entrypoint. |
| Ephemeral Git HTTPS authentication | Optional generic capability: `github_git` | Central configures Git defaults; repository scripts use ordinary Git operations without acquiring credentials. |
| Ephemeral generic HTTPS package-registry read authentication | Optional generic capability: `registry_netrc` | Central configures the reviewed registry login in the operation environment. |
| Ephemeral Gradle private-Maven read authentication | Optional generic capability: `gradle_maven` | Central configures bounded Gradle properties for read-side dependency resolution. |
| Private OCI image + Helm OCI publication authentication | Optional generic capability: `registry_oci_publish` | Only for an authorized Linux repository release, Central establishes isolated Buildah/Skopeo + Helm registry auth with the existing reviewed write credential; product scripts own image/chart coordinates, immutability checks, push/read-back commands and release semantics. |
| Xcode, Gradle, Android SDK, Node, Python, Flutter and other product/toolchain commands | Repository-owned behavior | Fixed `.ci/*` scripts own toolchain installation/use, targets, tasks, selectors and product semantics. Central does not construct product argv. |
| Simulator, emulator and product-local test-environment lifecycle | Repository-owned behavior | Repository scripts own bounded product execution lifecycle. Physical-device infrastructure may remain separately governed and is not a v1 freeze blocker. |
| Product integration-test fixture/service credentials | Separately governed compatibility behavior | Existing Apple/Android profiles still carry bounded private test-service credentials. Generic Repository CI does not expose arbitrary secret names or environment maps; a caller may leave this lane only after those fixtures no longer need the credentials or an independently reviewed bounded private-fixture capability exists. |
| Provider publication/signing credentials and provider-specific release transport | Separately governed fixed release plumbing | Authorized macOS `release.repository` supplies the existing Apple signing/App Store Connect material through fixed `CI_APPLE_*` variables; other provider lanes remain compatibility behavior. Secret names, environment maps, and provider commands are never caller inputs. |
| Shared dependency/build cache | Separately governed, non-blocking | No generic cache capability is required to migrate the current validation fleet. Add one only after measured reusable need; never accept caller cache paths or restore commands. |
| Diagnostic follow UX and optional progress polling | Separately governed, non-blocking | The stable private log/evidence contract is sufficient for v1 adoption; follow behavior may evolve independently. |
| Apple signing/App Store Connect distribution | Separately governed compatibility behavior | Generic macOS `release.repository` forwards only the existing fixed Apple release material; store/signing semantics stay repository-owned and legacy Apple lanes remain until live callers have equivalent proof. |
| Android signing/store distribution | Separately governed compatibility behavior | Ordinary Repository CI does not invent Android store credentials; live Android release callers remain on their reviewed lane until an equivalent bounded release path is proven. |
| Physical-device/provider execution | Separately governed compatibility behavior | Physical device discovery, provider sessions and device evidence remain outside ordinary Repository CI. |
| Screenshot/visual-review orchestration | Separately governed compatibility behavior | Generic UI-test may run repository-owned UI semantics, but screenshot review/provider orchestration remains separately governed. |
| Immutable Maven publication/readback archive lifecycle | Separately governed compatibility behavior | Maven publication remains on its reviewed publication lane until an equivalent generic write-side path is proven for every live caller. |
| Source snapshot/checkpoint publication and branch deletion | Separately governed capability | These are source/lifecycle operations, not product execution profiles, and remain separate fixed Central workflows. |
| Technology-specific dependency caches | Legacy-only behavior that may disappear | Existing Apple/Node/etc. cache optimizations do not block migration because current fixed `.ci/*` execution is correct without caller-configurable cache paths. |

## Parity conclusion for current live lanes

Every currently live capability family in the Apple, Android, Flutter, Python, Node and Maven compatibility workflows is classified above as generic baseline, optional generic capability, repository-owned behavior, separately governed compatibility behavior, or legacy-only non-blocking behavior. There is no unclassified capability in the current public parity audit. This classification does **not** authorize deleting a lane: each lane still requires equivalent live proof for its actual callers and zero remaining live callers in the private migration ledger.

## Frozen non-host capability catalog

Repository CI v1 has exactly these non-host optional capability types:

- `private_network`
- `github_git`
- `registry_netrc`
- `gradle_maven`
- `registry_oci_publish`

A project may receive only a reviewed unique subset through private Agent State configuration.
No capability is enabled by repository source, semantic inputs, caller-provided secret names, or
arbitrary environment metadata. Unknown capabilities fail closed.

A new non-host capability type requires a demonstrated current-fleet shared-infrastructure gap. A
historical workflow step, one product command, or a convenience optimization is not sufficient.

## Release and publication migration plan

Write-side publication is intentionally **not** generalized by inventing provider capability names
before a target migration is ready. The bounded plan is:

1. Keep each still-used reviewed compatibility release lane live while its private migration-ledger
   caller count is non-zero.
2. Move product build/test/release semantics into the repository's fixed `.ci/release.sh` without
   moving credential acquisition into repository source.
3. When a current migration is ready to leave a compatibility lane, identify the smallest reusable
   credential/setup class for that publication family. The current Apple migration reuses the four
   existing Central signing/App Store Connect secrets as fixed macOS-release-only `CI_APPLE_*`
   variables; this is not a new caller-selectable capability. Other families may materialize only
   bounded ephemeral credential files/environment/tool defaults for the lifetime of `.ci/release.sh`.
4. Keep provider commands, package coordinates, store metadata, signing semantics, targets and
   release decisions repository-owned. Do not expose raw secret names, arbitrary registries,
   commands, arguments or runner labels through Agent State or workflow inputs.
5. Prove the generic release path on exact source with private evidence, cleanup and Agent State
   settlement. Record adoption/live proof in the private migration ledger.
6. Retire the corresponding legacy release lane only after its private live-caller count reaches
   zero.

The private OCI/Helm family is now demonstrated by `registry_oci_publish`. The existing Apple migration continues to use its fixed macOS-release-only `CI_APPLE_*` plumbing. Other publication families stay separately governed until their reusable credential contracts are demonstrated; do not infer package, Maven, or Android-distribution capabilities from this migration.

## Cache decision

The current fleet can adopt fixed `.ci/*` validation without a caller-configurable shared cache.
Therefore cache is not a v1 capability and does not block migration. Existing compatibility-lane
caches may continue while those lanes are live. Any future generic cache must use a Central-owned,
bounded key/path schema and must never accept arbitrary cache paths, keys or restoration commands.

## Migration gate

A consumer is ready to leave a legacy validation lane only when its required fixed entrypoints are
tracked/executable, any required multi-OS plan is tracked and bounded, its private capability grant
is sufficient, required operations run on exact source through the generic executor, child evidence
and cleanup settle correctly under one parent lifecycle, and the private migration ledger records
live proof. Release-lane retirement additionally follows the write-side migration plan above.
