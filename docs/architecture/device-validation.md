# Physical-device validation architecture

## Authority boundary

`ci-workflows` owns reusable source admission, typed planning, deterministic device discovery, production cross-run fencing, bounded evidence identity, stage ordering, restoration, and cleanup. Product repositories retain checked-in device test scripts and assertions. Flux, signing, store, registry, and deployment authority are outside this workflow.

Real mutation requires **both** a separately supplied `device_authorization_receipt` and a current production `device-lock/1` fencing receipt. A runner label, attached device, opaque alias, issue, branch, GitHub concurrency group, or `live_test_credentials` secret never grants physical-device authority. When the exact owner receipt is absent or invalid, planning fails closed with `physical_authorization_required` before the device job is scheduled.

## Current GitHub source admission and immutable implementation identity

There is no caller-supplied `source_trust` field. The action derives trust from fixed GitHub repository, event, exact admitted SHA, event SHA, head repository, and fork state. Synthetic pull-request smoke is limited to the same `ci-workflows` repository. Real execution accepts only exact trusted reusable/dispatch source.

The planner emits one canonical `device-plan/1` packet and SHA-256. The executor rejects noncanonical bytes, hash drift, source drift, authorization-presence drift, changed concurrency, changed cancel behavior, changed profile/script facts, or moved source. It then validates the exact detached caller checkout and clean tree before discovery.

The reusable workflow invokes `validate-device` and `device-lock` through immutable private action checkpoints. Exact checkout, workspace preparation, and workspace cleanup also use reviewed immutable private actions. The public reusable workflow never executes mutable Central helper source from a local checkout.

## Opaque alias and runner-local device identity

The public request carries a bounded opaque alias, never a raw serial or UDID. Trusted runner-local discovery sees raw identifiers only after exact source and owner authorization have been validated. Deterministic selection filters family, version/API, reviewed model class, capability, connection, and health policy, then stores a private run-owned record and emits only a profile-scoped SHA-256 device identity.

Raw serial or UDID values are absent from public inputs, outputs, summaries, job names, concurrency groups, stable failures, and durable evidence. Product scripts receive the selected identifier only in the platform-specific process environment (`ANDROID_SERIAL` or the reviewed Apple selector variables). They do **not** receive the owner receipt, fencing receipt, device-lock backend root, Central lock internals, checkout token, or GitHub token.

## Scheduling and guarded mutation

Central runner policy maps Android device work to the reviewed `mobile` base capacity and iOS/tvOS to `apple`. The selected base runner is scheduling capability, not physical authority.

The workflow order is fixed:

1. derive and validate the typed exact-source plan;
2. deterministically discover one eligible physical device;
3. acquire one production `device-lock/1` receipt for exact family, capability, discovered-device hash, tested source SHA, owner/run identity, request ID, and authorization-receipt hash;
4. verify that receipt immediately before mutation;
5. execute only the contract-selected product command profile, which independently re-verifies the same receipt at the Python mutation boundary;
6. restore product-owned device state;
7. expected-state release the exact receipt;
8. prove the released receipt has no live fencing residue;
9. remove Central device state, exact source checkout, and registered workspace state and prove zero residue.

GitHub job concurrency remains supplemental serialization using the contract-owned group and `cancel-in-progress: false`. It is not the fencing token and cannot replace `device-lock/1`.

## Owner authorization receipt

`device_authorization_receipt` is an exact, canonical `device-authorization/1` JSON receipt carried only through the named reusable-workflow secret channel. It binds repository, exact source SHA, family, capability, request ID, and expiry. The checked-in contract contains no authorized family list and does not manufacture authorization from secret presence: the receipt bytes are validated before an authorized plan is emitted and again at the live boundary.

The independent `live_test_credentials` secret is only the reviewed non-production product backend credential channel. It may be required by a product command profile, but it never authorizes device scheduling or lock acquisition.

## Production fencing contract

The merged #136 `device-lock/1:posix-shared-root-v1` adapter is the production cross-run/cross-repository fencing authority. Runner infrastructure owns the backend root; callers cannot select it. The opaque receipt binds hashed authorization, discovered-device identity, source, owner, request, lease, and a fencing token. Stale, mismatched, superseded, expired, wrong-family, wrong-source, wrong-device, wrong-owner, replayed, or fabricated receipts fail closed.

The in-memory adapter remains a deterministic single-process **test double only**. Synthetic evidence cannot claim production fencing or physical proof.

## Synthetic execution

The three `ciw-synthetic-*` profiles exercise source admission, planning, inventory parsing, deterministic selection, evidence redaction, restoration-first lifecycle behavior, and the in-memory test lock. They never acquire `device-lock/1`, never receive `device_authorization_receipt`, and never mutate hardware.

## Restoration, cleanup, and evidence

The live executor records the first product-stage failure while still attempting product cleanup. The workflow then performs a separate lock-protected restore step before expected-state lock release, verifies released-lock residue, removes Central private device state, removes the exact source checkout without following symlinks, and removes registered workspace state. Terminal projection fails if any required discovery, fencing, execution, restore, release, residue, or cleanup outcome is not successful.

Zero routine Actions artifacts are retained. Stable product evidence is limited to bounded identifiers and routes through the shared physical-log policy; raw platform logs, host paths, device identifiers, credentials, receipts, environment dumps, screenshots, traces, and private media are not durable evidence. Synthetic evidence remains explicitly non-physical.
