# Physical-device validation workflow

`validation.device` is the bounded device-validation interface with stable check name `CI / Physical device validation`. The current implementation supports exact source admission, typed planning, opaque device aliases, synthetic Android/iOS/tvOS validation, stable authorization denial, restoration/cleanup contracts, and zero-artifact evidence. It does **not** authorize real hardware mutation by itself.

## Current execution boundary

No real physical-device execution is authorized by the checked-in source package. A real reusable invocation reaches the stable `physical_authorization_required` denial and fails on general Linux reporting capacity instead of silently skipping or treating a runner/secret as authority.

The planner may serialize the centrally approved base selector for an unauthorized plan so that planning can complete and report the stable denial. That selector is scheduling metadata only; it is not a physical lock, fencing token, owner authorization, or proof that hardware exists.

A runner label, attached device, secret, issue, branch, profile name, or opaque alias is never authorization. Enabling real Android, iOS, or tvOS execution requires a separate exact-family owner authorization plus the reviewed production lock/execution adapter and physical evidence path.

## Public request

The thin caller supplies:

- exact lowercase admitted source SHA;
- family: `android`, `ios`, or `tvos`;
- one reviewed capability;
- one bounded opaque alias from the selected profile;
- one reviewed command profile and exact checked-in script;
- bounded duration;
- optional named redacted-evidence exception;
- issue-scoped request ID.

There is no raw serial/UDID and no caller-supplied `source_trust` input.

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
      live_test_credentials: ${{ secrets.LIVE_TEST_CREDENTIALS }}
```

The example documents request shape only. Secret presence does not authorize execution.

## Planner, components, and executor

The reusable workflow checks out its central implementation with the called-workflow identity (`job.workflow_repository` / `job.workflow_sha`) and separately handles the caller source through exact admitted SHA semantics. Caller workflow identity is not accepted as the central implementation identity.

The planner derives current GitHub source admission from repository/event/SHA/fork metadata and emits one canonical typed plan plus SHA-256. The executor consumes only that plan, rechecks the exact checkout, and cannot accept a caller replacement.

The stable named component bridge is `ci_workflows.devices`:

- `lock` delegates exact plan/device acquisition to the bounded lock adapter;
- `validate` delegates to the restoration-first typed lifecycle;
- `cleanup` removes only registered device state and proves zero owned residue.

The plan emits a reviewed profile/family/alias-class concurrency group. The executor uses it with `cancel-in-progress: false`; callers cannot override the group or cancellation behavior.

## Synthetic contract smoke

`.github/workflows/device-validation-contract-smoke.yml` runs on `[linux, amd64, general]` source-only capacity and covers Android, iOS, and tvOS synthetic profiles. It executes focused tests, deterministic parsers, selection, redaction, restoration-first cleanup, primary-plus-cleanup reporting, called-workflow identity checks, and zero-artifact verification. It does not touch hardware or receive physical-device credentials.

The smoke also watches the public/CIW/bootstrap/reference surfaces required by final registration so an integration-only candidate cannot bypass device validation.

All synthetic fixtures are descriptive and indexed in `cases.json`. There are no `.checkpoint` or placeholder files.

## Runner contract

Central runner policy already registers `validation.device` as the guarded `physical-device` overlay. The overlay maps Android to the `mobile` base profile and iOS/tvOS to the `apple` base profile, and requires exact trusted source plus lock/authorization evidence before a real execution profile can be resolved.

Synthetic and denied-plan selector serialization uses only centrally approved concrete base selectors. It does not change the guarded-overlay authorization boundary.

## Cleanup and artifacts

Every terminal path removes `source`, device-owned state, registered workspace state, and `.ciw`. Cleanup uses no-follow semantics and proves both absence and non-symlink status. Zero routine Actions artifacts are retained.

## Activation and consumer adoption

Final central registration must keep `device_alias` as the public opaque selector, register the real `ci_workflows.devices.*` components and stable `ciw device validate` command, and preserve the existing guarded runner binding. Consumer cutover remains separately reviewable.

Real physical-device evidence is a later authorization event, not something inferred from synthetic validation or central registration. Until an exact-family owner explicitly authorizes it, the checked-in workflow must continue to fail closed with `physical_authorization_required`.
