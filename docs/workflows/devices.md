# Physical-device validation workflow

`validation.device` version `1.0.0` is the bounded shared contract for an explicitly authorized Android, iOS, or tvOS physical-device request. Its stable check name is `CI / Physical device validation`.

## Current implementation state

No live physical-device execution is authorized by issue #14’s initial source assignment. The checked-in workflow, action, profiles, synthetic inventory, in-memory lock reference, evidence model, cleanup logic, and portable contract smoke are implemented now. The execution job remains fail-closed until Apple issue #13 has merged and an accepted canonical resource-fencing adapter is integrated.

A runner label is not a device lock. A connected device, branch name, issue label, profile name, or caller prose is not owner authorization. Simulator or emulator evidence never certifies a physical device.

## Public call boundary

The future thin caller supplies only:

- exact admitted lowercase source SHA;
- `android`, `ios`, or `tvos`;
- one reviewed capability;
- an optional exact identifier only when that selected profile permits it;
- one reviewed command profile and its exact checked-in test script;
- a bounded duration;
- an optional named evidence exception;
- an issue-scoped request ID such as `issue-14-approved-smoke`.

The only fixed secret name is `live_test_credentials`, and it is accepted only by a reviewed profile whose contract explicitly permits a non-production test backend. Callers cannot select a secret name, runner, host, command, arbitrary script, endpoint, signing identity, provisioning profile, keychain, keystore, store operation, release operation, Kubernetes target, or deployment.

```yaml
jobs:
  physical_device:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-device.yml@<reviewed-ref>
    with:
      admitted_sha: <exact-lowercase-sha>
      device_family: android
      device_capability: instrumentation
      device_identifier: <exact-identifier-only-when-profile-permits>
      command_profile: iptv-android-device
      script_path: build.sh
      max_duration_minutes: 60
      evidence_exception_id: ""
      request_id: issue-14-approved-smoke
    secrets:
      live_test_credentials: ${{ secrets.LIVE_TEST_CREDENTIALS }}
```

This example describes the bounded interface. It is not current authorization to execute a device.

## Lifecycle

The reviewed stage order is fixed:

1. admit exact source and validate the issue/request identity;
2. resolve one repository/product/family/capability profile;
3. parse bounded discovery output;
4. select one healthy non-personal non-conflicting physical device;
5. acquire the canonical resource lock with request/run identity and epoch fencing;
6. capture only state that the run is allowed to restore;
7. run the contract-owned prepare profile;
8. run the contract-owned product test profile;
9. collect bounded redacted evidence;
10. restore captured state;
11. release the exact lock epoch;
12. remove registered local state and prove zero residue.

Every terminal path attempts restoration and lock release without hiding the primary failure. The workflow never broadly erases a device, removes unrelated applications or data, restores uncaptured state, or treats device presence as lock ownership.

## Discovery and identity

Android synthetic tests use a strict projection of `adb` inventory. Apple synthetic tests use a strict projection of `xcrun devicectl` inventory. The selectors filter by family, OS/API range, reviewed model class, reviewed capabilities, health, connection class, personal/conflict flags, and identifier policy.

Raw serial or UDID values remain internal to selection and are never written to results or evidence. Public output contains a deterministic SHA-256 identity hash scoped by profile and family.

Multiple candidates fail unless the selected profile explicitly owns the deterministic identity-hash tie-break. No “first device” fallback exists.

## Evidence and artifacts

The redacted packet records request and issue identity, repository and exact source, family/profile, redacted identity hash, bounded classification, lock epoch and hashed resource key, fixed command profiles, duration, stable result/failure, assertions, restoration, cleanup, optional named exception, retained evidence inventory, certification scope, and non-certification limitations.

The packet rejects raw serial or UDID, personal data, private media, private endpoint values, credentials, environment dumps, unrestricted logs, complete screenshots, and traces outside a named exception. Zero routine Actions artifacts is the default. A registered exception is limited to redacted JSON/text, twelve files, eight MiB, and three days.

## Contract smoke

`.github/workflows/device-validation-contract-smoke.yml` runs only on semantic `portable`. It validates focused tests plus synthetic Android, iOS, and tvOS inventory and reference lock lifecycles. It does not select, lock, install to, erase, reboot, or execute against hardware.

## Final integration

After Apple issue #13 merges, issue #14 will merge protected `main` normally, add only the minimum shared `validation.device` and `ciw device validate` registrations, integrate the accepted canonical resource-fencing contract, regenerate deterministic references, and obtain separate owner-authorized physical smoke for each required and available family. An unavailable family will remain explicitly unproven.
