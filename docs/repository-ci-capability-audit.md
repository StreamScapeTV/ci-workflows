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
catalog and private host-policy binding are documented in the canonical repository-CI onboarding
guide when that catalog is integrated.

## Current-fleet matrix

| Requirement family | Classification | Repository CI v1 treatment |
| --- | --- | --- |
| Exact source checkout, branch/tag namespace selection and observed SHA | Central baseline | Central admits and records the exact source before product execution. |
| Agent State start/source-observation/finish lifecycle | Central baseline | Central owns lifecycle settlement; repository scripts do not call Agent State. |
| Fixed operation to `.ci/*` entrypoint mapping | Central baseline | Only `build`, `test`, `full`, `ui-test`, and separately authorized `release` map to fixed tracked executable files. |
| Versioned bounded semantic inputs | Central baseline | `CI_INPUTS_JSON` contains only reviewed semantics; product scripts validate and translate them. |
| Private log, progress and bounded artifact locations | Central baseline | Central owns locations, scrubbing, packaging, private Drive transport, retention and cleanup. |
| Private network access for the duration of one operation | Optional generic capability: `private_network` | Central establishes and cleans up the reviewed private network around the whole repository entrypoint. |
| Ephemeral Git HTTPS authentication | Optional generic capability: `github_git` | Central configures Git defaults; repository scripts use ordinary Git operations without acquiring credentials. |
| Ephemeral generic HTTPS package-registry read authentication | Optional generic capability: `registry_netrc` | Central configures the reviewed registry login in the operation environment. |
| Ephemeral Gradle private-Maven read authentication | Optional generic capability: `gradle_maven` | Central configures bounded Gradle properties for read-side dependency resolution. |
| Xcode, Gradle, Android SDK, Node, Python, Flutter and other product/toolchain commands | Repository-owned behavior | Fixed `.ci/*` scripts own toolchain installation/use, targets, tasks, selectors and product semantics. Central does not construct product argv. |
| Simulator, emulator and product-local test-environment lifecycle | Repository-owned behavior | Repository scripts own bounded product execution lifecycle. Physical-device infrastructure may remain separately governed and is not a v1 freeze blocker. |
| Provider publication/signing credentials and provider-specific release transport | Separately governed compatibility behavior | Existing reviewed release lanes remain live. `release.repository` may prepare/run a fixed `.ci/release.sh`, but v1 does not expose provider secrets or provider commands as generic caller input. |
| Shared dependency/build cache | Separately governed, non-blocking | No generic cache capability is required to migrate the current validation fleet. Add one only after measured reusable need; never accept caller cache paths or restore commands. |
| Diagnostic follow UX and optional progress polling | Separately governed, non-blocking | The stable private log/evidence contract is sufficient for v1 adoption; follow behavior may evolve independently. |

## Frozen non-host capability catalog

Repository CI v1 has exactly these non-host optional capability types:

- `private_network`
- `github_git`
- `registry_netrc`
- `gradle_maven`

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
   credential/setup class for that publication family. Central may materialize only bounded
   ephemeral credential files/environment/tool defaults for the lifetime of `.ci/release.sh`.
4. Keep provider commands, package coordinates, store metadata, signing semantics, targets and
   release decisions repository-owned. Do not expose raw secret names, arbitrary registries,
   commands, arguments or runner labels through Agent State or workflow inputs.
5. Prove the generic release path on exact source with private evidence, cleanup and Agent State
   settlement. Record adoption/live proof in the private migration ledger.
6. Retire the corresponding legacy release lane only after its private live-caller count reaches
   zero.

This plan covers the current publication families without freezing speculative public capability
names for package publication, Maven publication, OCI/container publication, Android distribution,
or Apple distribution before their reusable credential contracts are demonstrated.

## Cache decision

The current fleet can adopt fixed `.ci/*` validation without a caller-configurable shared cache.
Therefore cache is not a v1 capability and does not block migration. Existing compatibility-lane
caches may continue while those lanes are live. Any future generic cache must use a Central-owned,
bounded key/path schema and must never accept arbitrary cache paths, keys or restoration commands.

## Migration gate

A consumer is ready to leave a legacy validation lane only when its required fixed entrypoints are
tracked/executable, its private capability grant is sufficient, required operations run on exact
source through the generic executor, private evidence/cleanup settle correctly, and the private
migration ledger records live proof. Release-lane retirement additionally follows the write-side
migration plan above.
