# Trusted Gradle seed upload action

`upload-gradle-seed` is the central **client only** for the trusted Gradle dependency-seed promotion protocol owned by `StreamScapeTV/flux#327`. It does not create a trigger, choose a runner, grant `id-token: write`, or decide when product validation has succeeded.

The caller supplies only the exact pushed source SHA. The action validates the protected Android GitHub context, requests one short-lived GitHub OIDC token for audience `streamscapetv-gradle-seed-v1`, selects only portable writable `GRADLE_USER_HOME/caches/modules-*` files, excludes Gradle lock/GC state, hashes and streams the bounded delta to the fixed internal Flux service, verifies the promoted source/generation/count response, and retains no routine artifact or fallback cache transport.

## Protected Android push example

This is a **consumer-owned excerpt** for `StreamScapeTV/iptv-android#800`; it is not a workflow implemented by this action. The promotion lane remains separate from mixed-trust PR/dispatch validation and runs only after the exact pushed SHA has passed the required Android validation matrix.

```yaml
jobs:
  validate:
    # Existing Android validation matrix; no id-token permission here.
    permissions:
      contents: read
    # ... existing validation steps ...

  warm_gradle_seed:
    if: ${{ github.event_name == 'push' && github.ref == 'refs/heads/develop' }}
    needs: validate
    runs-on: mobile
    permissions:
      contents: read
      id-token: write
    steps:
      - id: plan
        uses: StreamScapeTV/ci-workflows/actions/validate-android@<immutable-ci-workflows-sha>
        with:
          phase: plan
          admitted_sha: ${{ github.sha }}
          validation_profile: compile
          task_profile: app-compile
          private_dependency_contract_id: streamscape-media-android-v1
          private_dependency_sha: <exact-reviewed-media-sha>

      - id: workspace
        uses: StreamScapeTV/ci-workflows/actions/prepare-workspace@<immutable-ci-workflows-sha>
        with:
          profile: gradle
          cache_mode: disabled
          source_sha: ${{ github.sha }}
          trust_mode: ${{ steps.plan.outputs.source_trust }}

      # Exact Android source + exact private dependency checkout use the existing
      # bounded central actions here. No artifact/cache transports bridge jobs.

      - id: execute
        uses: StreamScapeTV/ci-workflows/actions/validate-android@<immutable-ci-workflows-sha>
        with:
          phase: execute
          admitted_sha: ${{ github.sha }}
          validation_profile: compile
          task_profile: app-compile
          private_dependency_contract_id: streamscape-media-android-v1
          private_dependency_sha: <exact-reviewed-media-sha>
          # private_dependency_path / verification outputs come from the exact
          # checkout step omitted above for brevity.

      - id: promote
        uses: StreamScapeTV/ci-workflows/actions/upload-gradle-seed@<issue-251-immutable-sha>
        with:
          source_sha: ${{ github.sha }}

      - id: android_cleanup
        if: always() && steps.workspace.outcome == 'success'
        uses: StreamScapeTV/ci-workflows/actions/validate-android@<immutable-ci-workflows-sha>
        with:
          phase: cleanup
          admitted_sha: ${{ github.sha }}
          validation_profile: compile
          task_profile: app-compile

      - id: android_residue
        if: always() && steps.workspace.outcome == 'success'
        uses: StreamScapeTV/ci-workflows/actions/validate-android@<immutable-ci-workflows-sha>
        with:
          phase: residue
          admitted_sha: ${{ github.sha }}
          validation_profile: compile
          task_profile: app-compile

      - id: workspace_cleanup
        if: always() && steps.workspace.outcome == 'success'
        uses: StreamScapeTV/ci-workflows/actions/cleanup-workspace@<immutable-ci-workflows-sha>
```

`prepare-workspace` with profile `gradle` exports a job-private registered `GRADLE_USER_HOME`; the bounded compile writes only that job's misses/refreshed entries there. Promotion therefore happens in the **same job after execute and before cleanup**. The consumer must still include its normal exact source/private-dependency checkout steps and must pin every central composite action to the reviewed immutable revision.

A promotion failure is visible but does not retroactively change the already-successful validation result or the active Flux seed. There is no GitHub Actions Cache Storage, routine artifact, PAT/deploy key, S3, OCI, or arbitrary HOME fallback path.
