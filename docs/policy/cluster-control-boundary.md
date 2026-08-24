# Cluster-control boundary for agents and CI

## Purpose

StreamScapeTV agents do not obtain Kubernetes control-plane authority through CI merely to validate whether GitOps changes reconciled successfully. Flux owns declarative cluster desired state and reconciles merged Git state automatically.

This boundary applies to agent-authored and agent-selected validation/acceptance paths across StreamScapeTV repositories.

## Required validation order

Agents must prefer evidence that does not require Kubernetes credentials:

1. validate source, schemas, Helm/Kustomize rendering, policy, and immutable producer artifacts before merge;
2. merge only reviewed desired state;
3. let Flux reconcile the merged Git state through its normal controller loop;
4. validate externally observable health/API behavior where available;
5. prefer functional end-to-end evidence that exercises the real product path over a Kubernetes `Ready` field alone.

For example, the Central CI broker is accepted through its public health endpoint and the real Agent State -> broker -> Central workflow -> terminal Agent State -> R2 diagnostics round trip. A GitHub Actions job with a Kubernetes service-account token is not required for that acceptance.

## Owner boundary for live cluster inspection

If live Kubernetes or Flux resource inspection is genuinely required and the result cannot be established through credential-free external evidence, the agent must stop at the owner boundary and ask the owner to run the necessary bounded read-only commands on the Flux/K3s cluster.

The request should state exactly what must be checked and which bounded result is needed. The owner may return the relevant non-sensitive output or conclusion. Agents must not ask for, receive, store, or reconstruct kubeconfigs, service-account tokens, Kubernetes API credentials, cluster-admin credentials, or equivalent direct cluster access.

Lack of agent cluster credentials is therefore not an implementation blocker to work around by creating a privileged CI workflow.

## Flux-control capacity

The `flux-control` runner class may exist as infrastructure during migration of historical workflows, but it is **not an agent-selectable ordinary validation capability**.

New agent-authored workflows must not use `[linux, amd64, flux-control]`, mount a Kubernetes service-account token, consume a kubeconfig, or otherwise acquire Kubernetes API authority for ordinary validation or acceptance.

Existing Kubernetes-authorized workflows must not be copied or extended as precedent for new agent validation. They should be audited and migrated toward tokenless source/render validation, externally observable evidence, or owner-operated live inspection.

## Mutating the cluster

Agents modify cluster desired state only through reviewed Git changes in `StreamScapeTV/flux` and normal Flux reconciliation. Agents must not use `kubectl apply`, `kubectl patch`, `kubectl delete`, `helm upgrade`, rollout restarts, direct controller reconciliation, or equivalent CI-side cluster mutation to make desired state appear healthy.

Owner-operated recovery or maintenance remains an owner decision and does not grant reusable agent authority.

## Security invariant

No validation convenience justifies converting an agent, GitHub Actions job, or reusable Central workflow into a Kubernetes control-plane principal.
