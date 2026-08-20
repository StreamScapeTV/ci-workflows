# Device validation architecture

## Authority split

Central device orchestration is product-neutral. It contains generic family policy for Android, iOS, and tvOS, but no product/repository allowlist, product command profile, provider identity, or application-specific packet name. A consumer supplies its bounded checked-in stage scripts and non-secret configuration through the public reusable-workflow inputs.

The planner derives source trust from current GitHub event metadata and binds the exact admitted SHA, family, capability, **semantic host capacity**, command plan, request identity, timeout, and evidence policy into canonical `device-plan/2` JSON. The executor must replay the exact typed-plan hash; changing a script path, argument, environment value, source SHA, family, capability, host capacity, or request identity fails closed.

## Authorization and private identity

`device_authorization_receipt` is the only public secret. Receipt presence permits planning to schedule a real physical executor, but the executor validates the receipt again against repository, source SHA, family, capability, request ID, and expiry. Runner labels or another secret never constitute physical authorization.

Raw device identity is discovered only on the selected runner. The raw device identifier is retained only in private runner state and is passed to the caller's checked-in script only as generic runtime metadata required to address the selected device. Public outputs and durable evidence use one-way identity hashes; raw device serials and UDIDs are never public inputs or durable evidence.

## Fencing and lifecycle

GitHub concurrency is supplemental. Cross-run mutation authority is `device-lock/1` using the runner-infrastructure-owned `posix-shared-root-v1` backend. The lock binds family, capability, hashed discovered-device identity, exact tested SHA, hashed authorization receipt, request identity, and run owner.

The lock is acquired and verified immediately before mutation. The checked-in prepare/test/evidence stages execute in one physical executor/workspace. Caller-owned restoration/cleanup runs **exactly once** after execution/evidence and before expected-state lock release. Lock residue, Central device state, exact-source state, and workspace state are then verified clean even on failure paths.

## Capacity boundary

Semantic host capacity expresses where a reviewed physical device can be reached; it is not a raw runner selector. Android may use `mobile` or `apple` physical host capacity. iOS/tvOS use `apple`. **Ordinary Android** CI continues to use the normal Android reusable workflow on semantic mobile capacity and does not inherit physical-device authority.

## Artifact and secret boundary

The default is **zero routine Actions artifacts** and no GitHub Actions cache. A named redacted diagnostics exception remains bounded by the checked-in device evidence contract. Caller-owned environment is non-secret; credential-, password-, token-, session-, private-key-, Central-, GitHub-, runner-, and raw-device authority keys are rejected rather than forwarded.
