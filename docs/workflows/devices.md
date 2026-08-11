# Physical-device validation workflow

`validation.device` is a draft bounded interface with stable check name `CI / Physical device validation`.

## Current state

No real physical-device execution is authorized. Planning and synthetic inventory smoke succeed; a reusable physical-device invocation emits `physical_authorization_required` and fails on `[linux, amd64, general]` general Linux capacity rather than silently skipping its device job. Exact owner authorization for one family in the current chat is still required before a later reviewed implementation can enable mutation.

A runner label, attached device, secret, issue, branch, or profile name is not authorization. The in-memory adapter is test-only and is not a fencing token.

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

There is no raw serial/UDID and no `source_trust` input.

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

The example documents the shape only. Secret presence does not authorize execution.

## Planner and executor

The planner derives current GitHub source admission from repository/event/SHA/fork metadata and emits one canonical typed plan plus SHA-256. The executor consumes only that plan, rechecks the exact checkout, and cannot accept a caller replacement.

The plan also emits a reviewed profile/family/alias-class concurrency group. The executor uses it with `cancel-in-progress: false`; callers cannot override the group or cancellation behavior.

## Synthetic contract smoke

`.github/workflows/device-validation-contract-smoke.yml` runs on `[linux, amd64, general]` source-only capacity and covers Android, iOS, and tvOS synthetic profiles. It executes focused tests, deterministic parsers, selection, redaction, restoration-first cleanup, primary-plus-cleanup reporting, and zero-artifact verification. It does not touch hardware.

All synthetic fixtures are descriptive and indexed in `cases.json`. There are no `.checkpoint` or placeholder files.

## Cleanup

Every terminal path removes `source`, device-owned state, registered workspace state, and `.ciw`. Cleanup uses no-follow semantics and proves both absence and non-symlink status. Zero routine Actions artifacts are retained.

## Handoff

This branch-exclusive repair intentionally does not modify shared public registries, CIW commands/dispatcher, generated references, action/tool locks, runner mappings, bootstrap inventories, shared tests, organization-rules, Flux, or product repositories. Agent 1 owns later current-main reconciliation, shared registration, exact-family authorization, real physical evidence, and final merge decisions.
