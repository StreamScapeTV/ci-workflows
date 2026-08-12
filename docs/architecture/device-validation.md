# Physical-device validation architecture

## Authority boundary

`ci-workflows` owns reusable admission, typed planning, bounded inventory parsing, deterministic selection, GitHub job serialization, named component orchestration, redacted evidence, stage ordering, and cleanup contracts. Product repositories retain checked-in device test scripts and assertions. Flux and product desired state are not modified by this issue.

No real physical-device execution is authorized by the checked-in source package. A real request remains `execution_authorized=false` with stable failure `physical_authorization_required`; the reusable workflow turns that result into an explicit general-Linux authorization-denied failure rather than silently skipping the device job. Runner labels, device presence, issue or branch text, opaque aliases, and secret presence are never authorization.

## Current GitHub source admission and reusable-workflow identity

There is no caller-supplied `source_trust` field. The action derives caller trust from fixed GitHub metadata: repository, event, exact lowercase admitted SHA, event SHA, head repository, and fork state. Synthetic pull-request smoke is limited to a same-repository `ci-workflows` head. Trusted reusable or dispatch planning requires same-repository, non-fork, exact-SHA metadata.

Central reusable-workflow implementation identity is separate from caller source identity. Each central checkout uses `job.workflow_repository` and `job.workflow_sha`; caller `github.workflow_sha` is not accepted as the implementation revision.

The executor receives the canonical one-line `device-plan/1` packet and its SHA-256 from the planner. It rejects unknown fields, noncanonical bytes, hash drift, source drift, changed authorization, changed concurrency, or changed cancel behavior. It then revalidates the exact detached checkout and clean tree before any execution boundary.

## Opaque alias and raw device identity

The public request carries a bounded opaque alias, never a raw serial or UDID. Each reviewed profile maps an allowlisted alias to one contract-owned alias class. Only after admission does trusted-host inventory parsing see a raw identifier. Selection filters family, version, model, capability, health, connection, personal/conflict flags, and profile policy, then emits only a profile-scoped SHA-256 identity hash.

Raw serial or UDID values are absent from public inputs, outputs, summaries, evidence, job names, concurrency groups, and stable errors.

## Guarded runner selection

Central runner policy registers `validation.device` only through the guarded `physical-device` overlay. That overlay maps Android to the `mobile` base profile and iOS/tvOS to the `apple` base profile. Resolving an actual physical execution profile requires exact trusted source plus the reviewed lock/authorization evidence.

Denied plans never schedule the device job. They may serialize the centrally approved concrete base selector so the planner can complete and emit `physical_authorization_required`; the selector is not authorization or lock evidence. Synthetic PR validation follows the same central selector authority and cannot escalate into the guarded overlay.

## Stable public components

`ci_workflows.devices` supplies the public implementation-component names used by the workflow contract:

- `lock` forwards exact typed plan/device acquisition through the supplied bounded lock adapter;
- `validate` forwards to the restoration-first `execute_device_plan` lifecycle;
- `cleanup` removes only registered device state and verifies zero residue.

The bridge adds no production lock backend and no physical authority. `InMemoryDeviceLockAdapter` remains a deterministic single-process test double only.

## Serialization, not fencing

The planner emits one group:

```text
device-validation-<reviewed-profile>-<family>-<contract-alias-class>
```

The executor uses that exact value with `cancel-in-progress: false`. There is no caller concurrency input and no raw identity in the group. GitHub job concurrency provides bounded CI serialization only; it is not a fencing token or database ownership authority.

## Synthetic execution

The three `ciw-synthetic-*` profiles permit successful source-only planning, inventory parsing, selection, redaction, lifecycle, restoration, cleanup, and local test-double lock coverage. They never perform device mutation. Real profiles remain represented for contract review but are not executable without a new exact-family owner authorization and a separately reviewed production execution/lock adapter.

## Restoration and cleanup

The lifecycle records the first primary failure while independently recording restoration, cleanup, and release failures. Restoration is attempted before cleanup. Cleanup removes only run-owned state and uses `lstat`-based no-follow traversal.

Both `source` and `.ciw` are removed under `if: always()`. The executor accepts only the fixed non-symlink `source` checkout; the workflow proves both `! -e` and `! -L`, so a symlink is unlinked rather than followed and persistent-host checkout residue fails closed.

## Evidence

Evidence is bounded, deterministic, and recursively allowlisted. It contains exact source, reviewed profile, hashed device identity, contract serialization facts, synthetic test-lock facts, contract-allowlisted assertions, restoration/cleanup state, and explicit limitations. It rejects raw identifiers, credentials, endpoints, environment dumps, unrestricted logs, screenshots, traces, or private media.

Every packet states that it does not certify simulator or emulator behavior, authorize release/signing/store/deployment, or claim cross-run database fencing. Synthetic evidence is never represented as real physical-device proof.
