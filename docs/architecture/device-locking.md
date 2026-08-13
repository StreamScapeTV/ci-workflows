# Physical-device resource locking and fencing

Issue #136 defines the organization-central lock boundary required before `validation.device` may mutate real hardware. This boundary is **not** device authorization, runner selection, or product test policy. It provides cross-run/cross-repository mutual exclusion and a current-state fencing receipt after those authorities have admitted an exact device request.

## Backend authority

The production backend is `posix-shared-root-v1`. Runner/device infrastructure provisions one absolute private POSIX directory in `CIW_DEVICE_LOCK_ROOT` with mode `0700`, owned by the runner execution identity. The path is not a public workflow/action input and is never returned in outputs or evidence.

Every runner execution context that can reach the same physical device must see the same backend root. This is an infrastructure invariant. A consumer repository, workflow input, issue, runner label, device presence, or GitHub concurrency group cannot choose or replace the backend.

The provisioned filesystem is production-capable only when infrastructure guarantees coherent advisory `flock` across every relevant execution context, atomic same-directory `rename`/replace, durable file and directory `fsync`, and consistent ownership/mode semantics. A path that does not provide those primitives is not a valid `posix-shared-root-v1` backend and must fail closed or remain unprovisioned.

Synthetic contract tests may provision an isolated temporary `CIW_DEVICE_LOCK_ROOT`; that proves only the lock implementation and is never physical-device evidence.

## Resource identity

One resource key is the SHA-256 digest of:

1. exact device family;
2. reviewed device capability;
3. deterministic SHA-256 discovered-device identity.

Raw serials and UDIDs never enter the lock receipt or persistent state. The exact GitHub repository/run/attempt owner is also stored only as a SHA-256 identity. The owner-authorization receipt is one-way hashed before persistence. Exact tested source SHA and bounded request ID remain explicit because they are required replay/fencing facts.

## Acquisition and lease semantics

`contracts/device-lock.json` owns the stable `device-lock/1` receipt. Acquisition is serialized through one private backend transaction file and writes one active lease record per resource using atomic replace. The active lease binds:

- `device_family` and `device_capability`;
- hashed discovered-device identity;
- exact tested source SHA;
- hashed authorization receipt;
- hashed repository/run/attempt owner;
- bounded request ID;
- a cryptographically random 256-bit fencing token;
- bounded acquisition and expiry epochs.

An unexpired lease held by another exact owner/request fails as `lock_held`. An exact retry by the same owner/request is idempotent and returns the existing fence rather than creating a second token. After expiry, a new valid acquisition replaces the active lease with a new random fence. The former receipt is then stale and cannot verify or release the newer holder.

The lease duration is bounded to 60–18,000 seconds. The validation plan must choose a lease that covers the bounded product test plus restoration/cleanup headroom. A mutation step revalidates the current receipt immediately before physical mutation and may demand additional minimum remaining lifetime. Receipt expiry remains authoritative even if a retry supplies a different nominal lease duration.

## Verification and stale-holder prevention

Verification reconstructs the expected request from reviewed workflow inputs and runner-owned GitHub repository/run/attempt identity. It then compares the opaque receipt with the current active backend state under the transaction lock.

Verification fails closed for malformed/fabricated receipts, wrong family/capability/device/source/authorization/owner/request, released state, expired leases, insufficient remaining lease, or a superseding fencing token. A previous holder cannot regain mutation authority after a newer acquisition simply by replaying its old receipt.

The receipt is not a bearer credential for another request. It is valid only while all bound request facts match the current active state.

## Expected-state release and cleanup evidence

Release verifies the exact current receipt before removing the active lease. A holder cannot release another current owner or a newer fencing token. Under the backend transaction lock, release removes the exact active lease first and then writes one bounded release marker containing only hashed identities and deterministic evidence. This ordering revokes mutation authority before cleanup evidence is published. If a process is lost between those two operations, the lock is no longer active but the missing marker makes cleanup evidence fail closed; a run cannot falsely claim successful release.

Repeating release is idempotent only when the active lease is absent and the exact expected release marker already exists. Replaying an older receipt after a newer holder exists or after a newer receipt has released fails closed.

Release evidence is deterministic from the exact receipt ID. Cleanup evidence is deterministically derived from that release evidence. `residue` verification requires no active lease and the exact expected release marker.

The release marker is backend evidence metadata, not an active lock. Only one marker exists per resource and each successful newer release replaces the older marker, preventing unbounded history growth.

`validation.device` must run restoration before release when product state requires restoration, then release and residue verification under terminal `always()` cleanup. Workflow cancellation/timeout relies on that terminal cleanup when it can run; the bounded lease is the fail-closed recovery mechanism when a runner is terminated before cleanup. A later holder cannot acquire until the old lease expires.

## Security boundaries

The device-lock layer does not:

- authorize a family, capability, device, source, or product command;
- accept raw serials/UDIDs or arbitrary device selectors;
- select runners, hosts, backend paths, endpoints, or credentials;
- use Agent State as a lock database;
- treat GitHub concurrency as fencing authority;
- add signing, provisioning, store, registry, Kubernetes, Flux, deployment, or production-data authority;
- make synthetic validation physical-device proof.

GitHub concurrency may remain as supplemental repository/job serialization. Production mutation authority comes from the separately reviewed authorization boundary plus the current exact device-lock receipt and the guarded `physical-device` runner contract.

## Action and CIW boundary

`actions/device-lock` is a thin composite that invokes the stable central dispatcher as `scripts/ci/ciw.py device lock --phase <phase>`. The registered handler is implemented in `src/ci_workflows/ciw_device_lock.py`; non-trivial locking remains in `src/ci_workflows/device_lock.py`. Its phases are:

- `acquire` — create or idempotently recover the exact current receipt;
- `verify` — revalidate the current fence before mutation;
- `release` — expected-state release and deterministic cleanup evidence;
- `residue` — verify the exact receipt has no active lock residue.

The backend root is never an action or CIW argument. The composite reads only the runner-infrastructure environment and returns bounded opaque/hash evidence suitable for internal `validation.device` orchestration.
