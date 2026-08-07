# Runner capability contract

Use semantic capability or workflow intent. Do not discover hosts, copy concrete `runs-on` values, or inspect Flux desired state merely to choose capacity.

`contracts/runner-profiles.json` is the implementation authority. This file is the agent-facing reference. `generated/runner-mappings.json` is the deterministic internal projection used by central workflows.

## Capabilities

| Capability | Current direct tag during migration | Guaranteed toolchain and resources | Trust and lifecycle | Never use for |
|---|---|---|---|---|
| `portable` | `portable` | Linux x64; Actions runner 2.336.0; 256 Mi / 1 Gi memory; 4 Gi local storage; 2 Gi disposable workspace | Tokenless, one job, ephemeral; untrusted fork source is allowed when the workflow itself exposes no secret | Containers, Apple SDKs, devices, Agent State mutation, Kubernetes credentials |
| `mobile` | `mobile` | Linux x64; JDK/Javac 25; Flutter 3.44.8; Dart 3.12.2; Node 24.18.0; Android API/Build Tools 36 and 37; NDK 28.2.13676358; 2 / 4 Gi memory; 6 Gi workspace; 20 Gi scratch; managed 20 Gi dependency cache | Tokenless, one job, ephemeral workspace; trusted PR or exact source because the managed cache is shared | Treating the runner as an attached Android device, OCI publication, Apple work, untrusted forks |
| `buildah-tiny` | `buildah-tiny` | Buildah 1.33.7, Skopeo 1.13.3, Podman 4.9.3; 512 Mi / 1 Gi; 6 Gi storage; cap 10 | Privileged daemonless OCI pod, tokenless, one exact trusted job | Docker/DinD, untrusted source, Kubernetes/Agent State credentials |
| `buildah-small` | `buildah` or `buildah-small` | Same daemonless OCI tools; 512 Mi / 2 Gi; 16 Gi storage; cap 6 | Privileged daemonless OCI pod, tokenless, one exact trusted job | Selecting a larger tier without measurements; Docker/DinD |
| `buildah-medium` | `buildah-medium` | Same daemonless OCI tools; 2 / 4 Gi; 32 Gi storage; cap 3 | Privileged daemonless OCI pod, tokenless, one exact trusted job | Ordinary small images or untrusted source |
| `buildah-high` | `buildah-high` | Same daemonless OCI tools; 4 / 8 Gi; 44 Gi storage; cap 1 | Privileged daemonless OCI pod, tokenless, one exact trusted job | Defaulting every image build to the largest tier |
| `apple` | `macOS` | Organization-managed macOS capacity; Xcode, Swift, SDKs, and simulator runtimes are verified at job start | Persistent/manual capacity; trusted PR or exact source; workflow must clean DerivedData, result bundles, simulators, temporary files, and credentials | Assuming signing identities, store credentials, or an attached physical device |
| `physical-device` | No direct tag | Guarded overlay: Android uses `mobile`; iOS/tvOS uses `apple` only after authorization, exclusive resource locking, deterministic discovery, exact source, evidence, and cleanup | Trusted exact source; concurrency one per device; lock held only for device access | Treating `mobile` or `apple` selection as proof that a device is attached |
| `agent-state-control` | No consumer-selectable tag | Central Agent State transport functions and protected central source only | Repository-scoped trusted control; never executes caller/product source | Product builds, issue/PR source, release, signing, deployment |
| `flux-control` | No consumer-selectable tag | Flux-owned Kubernetes service account and protected Flux policy/tooling | Ephemeral, repository-scoped to Flux, no caller source | Product PR source, arbitrary clusters/namespaces/service accounts, general builds |

The direct tags above exist only for controlled migration of current workflows. New and migrated consumers call a central reusable workflow with bounded product or validation inputs. They do not pass runner labels, hosts, engines, storage drivers, clusters, namespaces, service accounts, or secret names.

## Selection by intent

| Intent | Semantic selection |
|---|---|
| Policy, lint, source admission, ordinary Python/Node/GitOps/Helm validation | `portable` |
| Android/Gradle or Flutter-on-Linux validation | `mobile` |
| Apple compilation, Swift tests, macOS, iOS/tvOS simulator validation | `apple` |
| OCI build or publication | Smallest measured `buildah-*` tier allowed by the central product contract |
| Physical Android/iOS/tvOS validation | `physical-device` guarded overlay plus authorization and lock evidence |
| Agent State lifecycle or ownership transport | `agent-state-control` |
| Flux-authorized reconciliation | `flux-control` |

A reusable workflow with more than one possible profile uses a protected `portable` planning job. The planner validates semantic intent and emits a JSON selector from the checked-in mapping. A dependent job uses that output in `runs-on`. A composite action cannot change the runner after a job is scheduled.

## Temporary central self-check exception

Ordinary Python and policy validation remains assigned to `portable`. While the portable ARC incident tracked by Flux #268 is open, ci-workflows #60 authorizes only `.github/workflows/self-check.yml` to use the organization-managed `macOS` capability as an emergency exact-head merge gate. That workflow accepts only a same-repository pull request or exact trusted push/dispatch source, then verifies a pre-provisioned absolute executable as CPython 3.12.13 on Darwin arm64 or x86_64 before checkout. It never installs a runtime, invokes `sudo`, or elevates host privileges.

The exception receives no Apple signing, provisioning, simulator, physical-device, notarization, or store credential or entitlement. It does not change the public runner profile contract or generated mappings and must be removed in a later bounded change after portable ARC recovery.

## Buildah escalation

Generic `buildah` maps only to `buildah-small`. Select the smallest tier whose memory and storage limits cover measured peaks plus reviewed headroom. Escalation evidence must record:

- peak memory bytes and peak local-storage bytes;
- exact source SHA;
- workflow API and product ID.

A larger tier without those measurements is contract drift. Buildah capacity is privileged and daemonless; Docker daemons and Docker-in-Docker are retired and are not aliases.

## Physical-device contract

A physical-device job must have all of the following before device access begins:

1. trusted authorization receipt;
2. exclusive resource-lock receipt for the exact device;
3. requested device family and deterministic discovered device ID;
4. exact tested source SHA;
5. bounded execution and stable evidence ID;
6. cleanup evidence and lock release in an `always()` path.

The device lock covers only device access, not source checkout, dependency resolution, or unrelated compilation. This keeps scarce devices available while preserving exact single-owner access.

## Mandatory rules

- Never use bare `self-hosted`.
- Never introduce Docker-capable or DinD runner selection.
- Never combine semantic labels from different profiles.
- Never accept a runner selector from a workflow caller, issue, PR, matrix, or arbitrary JSON input.
- Untrusted source receives no registry-write, Agent State, signing, live-device, SOPS, Kubernetes, production-database, or deployment credential.
- Flux owns concrete ARC runner infrastructure and Kubernetes authority. `ci-workflows` owns the semantic contract and resolver.
- Stable outputs describe results, digests, receipts, evidence IDs, and cleanup; they do not expose host identity or private infrastructure details.

Validate and regenerate with:

```text
python3 scripts/ci/runner_contract.py validate
python3 scripts/ci/runner_contract.py generate --check
```
