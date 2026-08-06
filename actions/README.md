# Composite actions

Composite actions in this directory are thin, bounded adapters around named functions. They do not choose runners, elevate permissions, accept arbitrary shell callbacks, or hide multi-job orchestration.

## Shared foundation sequence

Issue #8 adds the non-language foundation adapters below:

1. `prepare-workspace` creates one marker-bound workflow-scoped state root and strict locale/environment isolation.
2. `verify-toolchain` verifies a contract-selected tool set plus semantic OS/architecture capability, or installs one checksum-locked asset.
3. `checkout-private-dependency` consumes a full SHA admitted by `source.resolve`, invokes the merged exact checkout contract, and erases remotes and credential-bearing Git state.
4. `verify-repository-policy` enforces tracked-secret, forbidden-file, clean-tree, generated-output, cache, and zero-artifact policy.
5. `render-evidence` emits deterministic redacted evidence beneath registered state.
6. `cleanup-workspace` removes only marker-bound registered paths and verifies zero residue on Linux and macOS.

`cleanup-workspace` must be invoked under `if: always()` after `prepare-workspace`. Callers never supply a deletion path. Caching remains disabled by default, and routine GitHub Actions artifacts remain zero unless a named reviewed exception exists.

The authoritative inputs, outputs, side effects, trust boundaries, and cleanup duties are generated in `docs/architecture/foundation-primitives.md` from `contracts/foundation-primitives.json`.
