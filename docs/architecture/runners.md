# Runner capability architecture

## Decision

Central workflows select execution capacity from `contracts/runner-profiles.json`. Product repositories supply bounded intent such as workflow API, platform, product, exact source, and reviewed resource evidence. They do not supply `runs-on`, labels, container engines, infrastructure identities, or control-plane targets.

The contract records the public semantic name, approved internal selectors, operating system and architecture, pinned or runtime-verified tools, privilege and source-trust boundary, resources, concurrency cap, workflow API allowlist, forbidden uses, lifecycle, and stable evidence fields for every capability.

`RUNNERS.md` is the concise agent-facing projection. `generated/runner-mappings.json` is the internal scheduling projection. Both derive authority from the JSON contract rather than from prose or live infrastructure discovery.

## Scheduling mechanism

GitHub chooses a job's runner before its steps execute. A composite action cannot safely resolve `runs-on`. Multi-capability public workflows therefore use a trusted `portable` planning job in a shallow two-job pattern:

```yaml
jobs:
  plan:
    runs-on: portable
    outputs:
      runs_on: ${{ steps.resolve.outputs.runs_on }}
    steps:
      - id: resolve
        run: python3 scripts/ci/runner_contract.py resolve ...

  execute:
    needs: plan
    runs-on: ${{ fromJSON(needs.plan.outputs.runs_on) }}
```

The planner executes protected central code on the non-privileged `portable` profile. It rejects unknown APIs, caller selector fields, unknown or contradictory labels, unsafe source trust, and missing device-lock evidence. The dependent job receives only a selector already present in the generated mapping. Callers cannot replace that output.

Ordinary fixed-profile workflows may use their centrally generated selector directly. Direct public tags are a migration bridge, not a consumer API.

## Capability and authority boundary

`ci-workflows` owns:

- semantic profile definitions and workflow bindings;
- validation and deterministic resolution;
- the Buildah tier escalation rule;
- generated internal mappings and organization compatibility reports;
- public documentation and stable evidence contracts.

Flux owns:

- ARC scale sets, images, concrete registration labels, quotas, storage classes, service accounts, and cluster rollout;
- Kubernetes credentials and Flux reconciliation authority;
- the decision to change concrete infrastructure behind a semantic profile.

Organization-managed Apple/device capacity owns host and device registration. Central workflows verify the required Xcode/Swift/device capabilities at runtime and fail closed if they are unavailable. The runner contract does not publish machine names or device identifiers.

## Trust classes

`portable` is the only profile that may execute untrusted fork source, and only when the workflow provides no protected credential. `mobile` and `apple` require trusted PR or exact source. Buildah profiles are privileged and require trusted exact source. Physical devices additionally require explicit authorization and an exclusive resource lock. The Flux control profile executes no caller source. Agent State requires no runner profile because it is not a GitHub Actions transport.

Privilege is not inferred from tool availability. A profile's allowed workflow APIs and source-trust values are explicit allowlists. Secret policy remains in each public workflow permission/secret contract; selecting a runner never grants a credential.

## Lifecycle and cleanup

ARC build profiles are one-job ephemeral pods. Their workspaces and scratch volumes are bounded, and the pod is removed after the job. The mobile dependency cache is the only shared build cache recorded by this contract and uses a job/pruner lock.

Apple capacity is persistent/manual. Apple workflows must clean checkout residue, DerivedData, result bundles, simulator state, temporary files, crash/evidence bundles after publication, and any credential material. A successful build without cleanup evidence is not a successful capability execution.

A physical-device lock is narrower than the whole workflow. Source admission and host-only preparation occur before lock acquisition; the workflow acquires the exact device immediately before device interaction and releases it in an unconditional cleanup path.

## Buildah tiers

The tier resolver compares measured peak memory and local storage, with reviewed headroom, against the four contract limits. It returns the first fitting tier in this order:

1. `buildah-tiny` — 1 Gi memory / 6 Gi storage;
2. `buildah-small` — 2 Gi / 16 Gi;
3. `buildah-medium` — 4 Gi / 32 Gi;
4. `buildah-high` — 8 Gi / 44 Gi.

Generic `buildah` resolves to `buildah-small`; it is not an automatic escalation request. Docker and DinD selectors fail closed.

Binding a workflow API to these tiers establishes only which reviewed capacity
classes it may resolve. Real adoption remains gated on a per-product,
per-workflow-role measurement for the exact source plus reviewed headroom; an
aggregate or smoke measurement proves only the role it measured and does not
make any real product ready for build or publication.

## Compatibility and drift

`generate_compatibility_report` applies reviewed migration-class rules to every row in `contracts/workflow-inventory.json`. Every current workflow/job family must have one or more semantic profiles or an explicit `retire`/adoption exception. Missing migration classes fail validation.

The generator writes:

- `generated/runner-mappings.json`;
- `docs/inventory/runner-compatibility.md`.

`python3 scripts/ci/runner_contract.py generate --check` compares exact deterministic content and fails on drift. Fixture tests cover every profile and negative policy cases. The generator is read-only with respect to Flux and consumer repositories.
