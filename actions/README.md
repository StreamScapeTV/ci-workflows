# Composite actions

Composite actions in this directory are thin, bounded adapters around named functions in the checked-in `ciw` command registry. They do not choose runners, elevate permissions, accept arbitrary shell callbacks, or hide multi-job orchestration.

## Source and release authority

- `resolve-source` calls `ciw source resolve` and preserves the merged #7 source-admission outputs.
- `exact-checkout` calls `ciw source exact-checkout` and preserves exact detached credential-free checkout.
- `resolve-release-tag` calls only `ciw release-tag resolve` or `ciw release-tag revalidate`; merged #59 remains the authority for trusted tag-push/existing-tag semantics.

## Python validation

- `validate-python` calls only `ciw python validate` with `plan` or `execute` phase.
- It accepts exact admitted source and bounded profile/path identifiers, never arbitrary shell, callbacks, runner labels, engines, images, service addresses, database URLs, credentials, or deletion roots.
- The reusable workflow owns runner planning, exact source staging, marker-bound state, immutable runtime selection, zero-artifact policy, evidence, and `if: always()` cleanup.

## Shared foundation sequence

Issue #8 provides the six non-language foundation adapters below. Issue #31 changes only their dispatch path and preserves every public input, output, side effect, trust boundary, and cleanup duty.

1. `prepare-workspace` calls `ciw workspace prepare` to create one marker-bound workflow-scoped state root and strict locale/environment isolation.
2. `verify-toolchain` selects only the bounded `ciw tooling verify` or `ciw tooling install-asset` command.
3. `checkout-private-dependency` calls `ciw dependencies checkout-private`, consumes a full SHA admitted by `source.resolve`, invokes merged exact checkout, and erases remotes and credential-bearing Git state.
4. `verify-repository-policy` calls `ciw policy verify-repository` for tracked-secret, forbidden-file, clean-tree, generated-output, cache, and zero-artifact policy.
5. `render-evidence` calls `ciw evidence render` to emit deterministic redacted evidence beneath registered state.
6. `cleanup-workspace` calls `ciw workspace cleanup` to remove only marker-bound registered paths with descriptor-anchored no-follow traversal and verify zero residue on Linux and macOS.

`cleanup-workspace` must be invoked under `if: always()` after `prepare-workspace`. Callers never supply a deletion path. Caching remains disabled by default, and routine GitHub Actions artifacts remain zero unless a named reviewed exception exists.

The authoritative foundation inputs, outputs, side effects, trust boundaries, and cleanup duties are generated in `docs/architecture/foundation-primitives.md` from `contracts/foundation-primitives.json`. Python validation behavior is documented in `docs/workflows/python.md` and `docs/architecture/python-validation.md`. The complete typed command hierarchy is generated in `docs/reference/ciw.md` from `contracts/ciw-commands.json`.
