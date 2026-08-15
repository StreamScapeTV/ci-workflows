# Physical-device validation workflow

`validation.device` is the bounded physical-device interface with stable check name `CI / Physical device validation`. It supports exact source admission, typed planning, opaque device aliases, deterministic runner-local discovery, independent owner authorization, production `device-lock/1` fencing, restoration-first terminal handling, and zero routine Actions artifacts.

## Public request

The caller supplies only bounded product facts:

- exact lowercase admitted source SHA;
- family: `android`, `ios`, or `tvos`;
- reviewed capability;
- bounded opaque alias from the selected profile;
- reviewed command profile and exact checked-in test script;
- bounded duration;
- optional named redacted-evidence exception;
- issue-scoped request ID.

There is no public raw serial or UDID and no caller-supplied `source_trust`, runner selector, concurrency group, lock backend, fencing token, or arbitrary command.

The named secret channels are separate:

- `device_authorization_receipt` — exact expiring owner authorization bound to repository/source/family/capability/request;
- `live_test_credentials` — optional fixed non-production product backend credentials. This secret never grants physical authority.

```yaml
jobs:
  physical_device:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-device.yml@<reviewed-ref>
    with:
      admitted_sha: <exact-lowercase-sha>
      device_family: android
      device_capability: instrumentation
      device_alias: acceptance-primary
      command_profile: iptv-android-device
      script_path: build.sh
      max_duration_minutes: 60
      evidence_exception_id: ""
      request_id: issue-14-approved-smoke
    secrets:
      device_authorization_receipt: ${{ secrets.DEVICE_AUTHORIZATION_RECEIPT }}
      live_test_credentials: ${{ secrets.LIVE_TEST_CREDENTIALS }}
```

An absent or invalid authorization receipt produces stable `physical_authorization_required` before the hardware job is scheduled.

## Exact lifecycle

The planner derives current GitHub source admission and emits one canonical typed plan plus SHA-256. The physical job uses a centrally approved Android `mobile` or Apple runner selector and then performs the following fixed sequence:

1. exact detached caller checkout and clean-tree revalidation;
2. isolated workspace preparation;
3. deterministic discovery of exactly one eligible device, publishing only its SHA-256 identity;
4. production `device-lock/1` acquisition for the exact device/source/request/owner;
5. production lock verification immediately before physical mutation;
6. execution of only the contract-selected product scripts, with an independent Python receipt revalidation at the mutation boundary;
7. lock-protected product restore/cleanup;
8. expected-state lock release;
9. released-lock residue verification;
10. Central private device-state removal and residue verification;
11. exact source no-follow cleanup and registered workspace cleanup;
12. terminal projection that fails if any required lifecycle outcome failed.

The plan's GitHub concurrency group uses `cancel-in-progress: false` and remains supplemental serialization only. It is **not** the fencing token.

## Authority isolation

Raw hardware identity stays runner-local. Product scripts receive only the selected platform identity they need to target the device. They do not inherit `device_authorization_receipt`, the opaque resource-lock receipt, the device-lock backend root, `CIW_LOCK_*` internals, checkout credentials, or the GitHub token.

The merged #136 fencing action owns acquisition, verification, expected-state release, and residue semantics. Runner infrastructure owns its shared backend; callers cannot select a filesystem path or infrastructure endpoint. The in-memory device adapter remains test-only and cannot authorize production execution.

## Synthetic contract smoke

`.github/workflows/device-validation-contract-smoke.yml` remains source-only. Its Android/iOS/tvOS synthetic profiles exercise exact planning, deterministic inventory parsing, selection, redaction, restoration/cleanup behavior, and the in-memory lock test double. The smoke receives neither `device_authorization_receipt` nor live product credentials and never claims physical proof.

## Immutable implementation and cleanup

The reusable workflow calls the reviewed `validate-device`, `device-lock`, exact-checkout, workspace-preparation, and workspace-cleanup actions through immutable private action SHAs. Public workflow execution does not depend on a mutable Central checkout.

All private selected-device state is stored below the registered device-validation state root, removed without following symlinks, and independently residue-checked. Zero routine Actions artifacts are retained. Stable evidence is bounded and redacted; raw platform logs, receipts, device identifiers, host paths, credentials, environment dumps, screenshots, traces, and private media are not durable product evidence.

## Activation

Central public registration marks `validation.device` implemented only after the reusable workflow, contracts, immutable action boundaries, documentation, and exact-head tests agree. Consumer repositories may then adopt the reviewed interface without selecting runners, hardware identifiers, fencing infrastructure, or Central internals.
