# CI Runner and Workflow Intents

Use this file to choose CI capability. Do not inspect workflow source or Flux manifests merely to select a runner.

| Intent | Use for | Restriction |
|---|---|---|
| `portable-validation` | Source checks, policy, lint, scripts, ordinary unit tests | Non-privileged; no registry, Agent State mutation, signing, or cluster credentials |
| `general-linux` | Linux tooling that needs no specialist capability | Do not use Apple or container capacity without an intrinsic need |
| `docker-capable-validation` | A reviewed workflow contract that specifically requires Docker-compatible behavior | Consumers do not choose sockets, daemon flags, or concrete labels |
| `oci-build-tiny` | Very small OCI build/inspection jobs | Selected by the central product contract, not by arbitrary user input |
| `oci-build-small` | Small OCI build/inspection jobs | Same trust rules as other OCI build profiles |
| `oci-build-medium` | Normal multi-step or multi-platform OCI work | No untrusted publication credentials |
| `oci-build-high` | Large or memory/storage-heavy OCI work | Lowest concurrency; use only when the product contract requires it |
| `agent-state-control` | Trusted Agent State lifecycle and ownership transport | Never execute product PR, issue, branch, or fork source |
| `apple-simulator-xcode` | Xcode, Apple SDK compilation, or simulator validation | Use only when Apple capability is intrinsic |
| `apple-physical-device` | Explicitly authorized Apple physical-device validation | Requires device ownership, serialization, exact source, evidence, and cleanup |
| `android-physical-device` | Explicitly authorized Android physical-device validation | Requires device ownership, serialization, exact source, evidence, and cleanup |
| `flux-kubernetes-control` | Trusted allowlisted Flux/Kubernetes maintenance or reconciliation | Never execute untrusted source; target and credentials remain Flux-owned |

## OCI resource tiers

| Tier | Memory request / limit | Local storage | Suggested concurrency cap |
|---|---:|---:|---:|
| tiny | 512 Mi / 1 Gi | 6 Gi | 10 |
| small | 512 Mi / 2 Gi | 16 Gi | 6 |
| medium | 2 Gi / 4 Gi | 32 Gi | 3 |
| high | 4 Gi / 8 Gi | 44 Gi | 1 |

## Rules

- Request workflow or product intent; do not write concrete runner labels in consumer guidance.
- Never use bare `self-hosted`.
- Consumers do not choose Docker versus Buildah, storage drivers, runner hosts, clusters, namespaces, service accounts, or secret names.
- `ci-workflows` owns reusable workflow implementation and semantic selection. Flux owns concrete runner infrastructure and mapping.
- Untrusted validation receives no registry-write, Agent State mutation, signing, SOPS, Kubernetes, production database, or deployment credentials.
- If the target repository has not migrated to a central reusable workflow, use the current workflow named by its `AGENTS.md`; do not copy its concrete `runs-on` selectors into new code.
