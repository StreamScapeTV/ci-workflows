# Physical-device validation architecture

## Authority boundaries

`ci-workflows` owns reusable authorization validation, normalized profile resolution, deterministic discovery/selection, lock-adapter interfaces, redacted evidence shape, stage sequencing, and cleanup contracts.

Product repositories own device-specific prepare/test/evidence/cleanup scripts, assertions, certification decisions, accepted model/capability requirements, and test-backend policy. Flux owns runner infrastructure. The canonical Supabase resource-fencing program owns the accepted transactional lock implementation. This source does not create another ownership database or call provisional Agent State RPCs.

No live physical-device execution is authorized by the initial issue #14 assignment.

## Layering

The device package has five layers:

1. `device_types.py` defines immutable request, profile, inventory, plan, lock, selection, result, and stable-error types.
2. `device_contract.py` validates `device-profiles.json`, `device-evidence.json`, trust, request identity, repository/profile compatibility, fixed script identities, secret policy, and final plan.
3. `device_execution.py` parses synthetic inventory, filters/selects, hashes identity, models epoch-fenced lock acquisition/release, builds evidence, executes an injected lifecycle runtime, and performs no-follow cleanup.
4. `device.py` is the public Python façade.
5. `ciw_device.py`, `scripts/ci/device.py`, the composite action, and workflows are thin adapters.

Shared CIW/public API/runner registration is intentionally absent until Apple issue #13 merges.

## Profile normalization

A profile binds:

- allowed repositories and product classifications;
- one physical family;
- bounded OS/API range;
- bounded model and capability sets;
- `forbidden`, `contract-owned`, or `exact-caller` identifier policy;
- `unique` or reviewed `identity-hash` selection;
- semantic base capacity (`mobile` for Android, `apple` for iOS/tvOS);
- a fixed prepare/test/evidence/cleanup command profile;
- lock resource policy, timeout, artifact exception, test-backend policy, and restoration obligations.

Contracts contain no real fleet identifier, private hostname, network address, credential, endpoint, or personal data.

## Trust and authorization

Public physical execution accepts only trusted exact source from `workflow_dispatch` or a bounded reusable call. The same-repository pull-request path exists solely for the central synthetic contract smoke and can select only non-executable `ciw-synthetic-*` profiles.

The planner always returns `execution_authorized=false` in the initial package. Final authorization requires a separate owner-approved request plus the accepted resource-fencing adapter. A branch, issue, label, runner, device inventory row, or caller text cannot set this bit.

## Lock fencing

`DeviceLockAdapter` requires a canonical resource key, exact request/run identity, family/profile, monotonically increasing epoch, opaque token, owner hash, expiry, collision next actor/action, and release receipt.

`InMemoryDeviceLockAdapter` is deterministic test-only reference behavior. It proves acquisition, collision convergence, expiry, stale-epoch rejection, and release semantics without becoming a production authority.

A runner label is not a device lock. The future adapter must use the canonical reviewed resource RPC and must never accept an arbitrary endpoint or caller-selected lock key.

## Selection

Android and Apple parsers accept bounded synthetic projections, enforce size/member limits, reject duplicate or malformed identifiers, and normalize only the fields required for filtering.

Selection rejects:

- wrong family, version, model, capability, health, or connection;
- offline or unauthorized devices;
- personal devices;
- already conflicting records;
- identifier-policy violations;
- no match;
- ambiguous candidates without a reviewed tie-break.

Only a profile-scoped SHA-256 identity hash leaves the selector.

## Lifecycle failure semantics

The injected runtime captures state, prepares, tests, collects evidence, restores, and cleans. The orchestrator always attempts restore, cleanup/residue, and release. A later failure does not replace an earlier primary stage failure, while restoration, cleanup, and release failures remain visible when no prior failure exists.

The workflow may remove only registered run-owned state. The no-follow remover uses `lstat`, unlinks symlinks rather than following them, rejects special files, and preserves outside sentinels.

## Evidence

The packet schema uses one exact physical certification scope per family and always carries limitations that it does not certify simulator/emulator behavior, authorize signing/store/deployment, or exceed the exact reviewed profile/source.

Top-level fields are allowlisted. Raw identifiers and sensitive key names are rejected recursively. URL, IP-address, token, password, secret, and authorization patterns are rejected from serialized values. Retained evidence is a bounded digest inventory, not unrestricted logs or media.

## Ordered completion

Issue #11 must first obtain real semantic-mobile execution and merge. Flutter #12 then merges its shared registrations. Apple #13 reconciles and merges. Only afterward may issue #14 add shared registrations, integrate canonical fencing, obtain explicitly authorized family-specific physical proof, and merge.
