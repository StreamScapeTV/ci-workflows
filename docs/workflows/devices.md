# Physical-device validation workflow

`validation.device` is the bounded device-validation interface with stable check name `CI / Physical device validation`. The implementation supports exact source admission, typed planning, opaque device aliases, synthetic Android/iOS/tvOS validation, stable authorization denial, restoration/cleanup contracts, and zero-artifact evidence. It does **not** grant real hardware mutation authority by itself.

## Current execution boundary

There is no checked-in physical-device execution authorization. A real reusable invocation without a separately accepted authorization and fencing receipt reaches the stable `physical_authorization_required` denial and fails on general-Linux reporting capacity instead of silently skipping or treating a runner/secret as authority.

The planner may serialize the centrally approved base selector for an unauthorized plan so planning can complete and report the stable denial. That selector is scheduling metadata only; it is not a physical lock, fencing token, owner authorization, or proof that hardware exists.

A runner label, attached device, secret, issue, branch, profile name, or opaque alias is never authorization. Enabling real Android, iOS, or tvOS execution requires a separate exact-family owner authorization plus the reviewed production lock/execution receipt and physical evidence path.

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

There is no raw serial or UDID and no caller-supplied `source_trust` input.

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

The reusable workflow invokes the reviewed `validate-device` implementation as an immutable private action. Exact checkout, workspace preparation, and workspace cleanup use the organization’s immutable shared private-action checkpoints. The workflow does not use a mutable central `.ciw` checkout as executable helper source.

The planner derives current GitHub source admission from repository/event/SHA/fork metadata and emits one canonical typed plan plus SHA-256. The executor consumes only that plan, checks out the caller source at the exact admitted SHA, rechecks the detached clean tree, and cannot accept a caller replacement.

The stable named component bridge is `ci_workflows.devices`:

- `lock` delegates exact plan/device acquisition to the bounded lock adapter;
- `validate` delegates to the restoration-first typed lifecycle;
- `cleanup` removes only registered device state and proves zero owned residue.

The plan emits a reviewed profile/family/alias-class concurrency group. The executor uses it with `cancel-in-progress: false`; callers cannot override the group or cancellation behavior. GitHub concurrency is serialization only and is not a fencing token.

## Stable CIW surface

The public composite action and `scripts/ci/device.py` compatibility adapter route ordinary device phases through `ciw device validate`. The legacy adapter path is retained only for fixed no-follow checkout cleanup. This keeps plan, synthetic, execute, cleanup, and residue behavior behind the same typed command contract used by Central validation.

## Synthetic contract smoke

`.github/workflows/device-validation-contract-smoke.yml` runs on `[linux, amd64, general]` source-only capacity and covers Android, iOS, and tvOS synthetic profiles. It executes focused tests, deterministic parsers, selection, redaction, restoration-first cleanup, primary-plus-cleanup reporting, source-admission checks, and zero-artifact verification. It does not touch hardware or receive physical-device credentials.

The smoke keeps its own exact branch implementation checkout and removes that `.ciw` tree under no-follow cleanup because the smoke is testing the branch candidate itself. The reusable public workflow is different: it uses immutable private action references.

All synthetic fixtures are descriptive and indexed in `cases.json`. There are no `.checkpoint` or placeholder files.

## Runner contract

Central runner policy registers `validation.device` as the guarded `physical-device` overlay. The overlay maps Android to the `mobile` base profile and iOS/tvOS to the `apple` base profile, and requires exact trusted source plus lock/authorization evidence before a real execution profile can be resolved.

Synthetic and denied-plan selector serialization uses only centrally approved concrete base selectors. It does not change the guarded-overlay authorization boundary.

## Cleanup and artifacts

Every physical executor terminal path independently records execution, device cleanup, residue verification, source cleanup, and registered-workspace cleanup before projecting the terminal result. The fixed caller `source` checkout is removed through the device no-follow cleanup action. Zero routine Actions artifacts are retained.

## Activation and consumer adoption

Central registration keeps `device_alias` as the public opaque selector, registers the real `ci_workflows.devices.*` components and stable `ciw device validate` command, and preserves the existing guarded runner binding. Consumer cutover and actual physical-device evidence remain separately reviewable.

A current owner authorization does not become checked-in repository authority. Until an accepted runtime authorization/fencing receipt producer is available to the execution path, the checked-in workflow continues to fail closed with `physical_authorization_required`.
