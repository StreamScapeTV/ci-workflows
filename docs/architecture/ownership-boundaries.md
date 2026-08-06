# Workflow implementation and domain authority boundaries

This document is the issue #3 ownership decision for the StreamScapeTV workflow-centralization program. It classifies what moves into `ci-workflows`, what remains in a consumer repository or authoritative service, and what must be retired rather than generalized.

## Central implementation rule

`StreamScapeTV/ci-workflows` owns reusable GitHub Actions implementation and generic CI functions. A responsibility is central when it is reusable orchestration, transport, source admission, runner scheduling, tool setup, evidence, cleanup, publication, read-back, maintenance, or conformance behavior that does not depend on one product's domain decisions.

Central implementation does not transfer product, coordination, or cluster authority. A central workflow may validate and execute a bounded checked-in consumer contract, but it may not replace that contract with repository-name conditionals or hard-coded protected data.

## Approved classifications

Every workflow in `contracts/workflow-inventory.json` is assigned exactly one of these dispositions:

1. **central public reusable workflow** — an organization-facing `workflow_call` API maintained in `.github/workflows/reusable-*.yml`;
2. **central internal reusable workflow** — an optional non-nesting leaf used only for reviewed multi-job orchestration;
3. **central named function or composite action** — reusable step-level behavior implemented as typed code and a thin adapter;
4. **thin repository caller** — consumer-owned trigger, minimum permissions, concurrency/environment, stable check surface, and bounded identifiers;
5. **repository-owned product contract, command, policy, or data** — product-specific behavior that remains checked in with its owner;
6. **temporary, repair, recovery, diagnostic, or superseded workflow to retire** — one-shot behavior that must not become a permanent shared API.

A workflow may have a thin-caller target and repository-owned command boundary, but its inventory record has one primary disposition and one explicit migration target.

## `ci-workflows`

This repository owns:

- public and internal reusable workflow YAML;
- exact-source and tag admission;
- semantic runner-profile resolution;
- named CI functions and thin composite actions;
- reusable Python, Node/Next, Android, Flutter, Apple, device, YAML, Helm-render, and Kustomize validation orchestration;
- engine-neutral OCI build, immutable publication, and registry read-back;
- Helm validation, deterministic packaging, publication, and read-back;
- tag-driven release manifests and bounded handoff orchestration;
- reusable Agent State GitHub transport;
- reusable Flux infrastructure-asset and trusted reconciliation wrappers;
- domain-neutral organization maintenance and conformance checks;
- generic evidence, redaction, artifact, clean-tree, and residue-aware cleanup behavior.

Consumers do not choose a concrete runner label, image builder, registry command, secret name, arbitrary shell command, or cluster target through a public workflow input.

## Agent State

`StreamScapeTV/agent-state` remains the sole authority for:

- project and execution-identity mapping;
- sessions, assignment resume, claims, files, packages, and shared resources;
- collision, takeover, transfer, lifecycle, retry, replay, idempotency, and receipt decisions;
- API/database state and compatibility;
- sanitized lifecycle and ownership projection payloads;
- pull-request ownership results.

The lifecycle and ownership workflow implementation moves to `ci-workflows`. The central workflow performs trusted event admission, bounded transport, API-declared transient retry, redaction, and faithful projection. It does not infer decisions from GitHub labels or comments and does not maintain a second policy table.

The Agent State product repository retains its backend, frontend, Compose, image, chart, and domain-test commands. Its current organization-maintenance workflows are central candidates only when their decision logic is domain-neutral.

## Flux

`StreamScapeTV/flux` remains the sole authority for:

- Kubernetes and Flux desired state;
- Kustomizations, HelmReleases, values, resource policy, storage, networking, database, backup, and ingress configuration;
- SOPS-encrypted data and decryption credentials;
- product/target rollout allowlists and authorization rules;
- cluster credentials, environments, live reconciliation, health, canary, selection, rollback, and incident acceptance;
- runner product definitions, exact bases/upstreams, desired scale-set selection, quotas, and live infrastructure policy.

`ci-workflows` owns reusable source-validation, runner-image/chart build and publication, release-manifest, maintenance, and trusted reconciliation orchestration. A trusted central Flux wrapper checks out exact protected Flux source and executes Flux-owned allowlist, plan, reconcile, and health scripts. It never accepts an arbitrary namespace, object, kubeconfig path, command, image, chart, or cluster target from untrusted input.

Publication and live selection are separate evidence states. Publishing a runner image or chart never mutates the live scale set automatically.

## Organization operating policy

`StreamScapeTV/organization-rules` owns shared human and machine operating policy. It is not a reusable-workflow implementation repository, live coordination database, or runner authority. Its policy validation may use central source-only primitives, but the policy text and schemas remain repository-owned data.

The repository currently has no Agent State project mapping. The inventory records this explicitly and does not invent a project key.

## Product repositories

Each product repository retains:

- architecture and feature specifications;
- source-of-truth hierarchy;
- toolchain and dependency pins;
- checked-in product commands and bounded command profiles;
- schemas, migrations, fixtures, generated-file rules, and acceptance assertions;
- platform schemes, tasks, destinations, devices, signing, and store policy;
- product identifiers, image/chart/static products, release tag patterns, and deployment-specific data;
- stricter local security, privacy, evidence, and cleanup requirements.

The central workflow may call a checked-in bounded product script. It must not accept an unrestricted shell payload or duplicate the product logic centrally.

## Product decisions

Current OCI image producers are limited to:

- `StreamScapeTV/iptv-backend`;
- `StreamScapeTV/agent-state`;
- inventory-confirmed Flux runner/infrastructure images.

Current Helm/OCI chart producers are limited to:

- `StreamScapeTV/iptv-backend`;
- `StreamScapeTV/agent-state`;
- inventory-confirmed Flux runner/ARC chart assets.

`StreamScapeWeb` remains a static Cloudflare Pages product. Native Android, Apple, Flutter, Media, organization-policy, and shared-workflow repositories receive no implicit OCI, Helm, or Jib product.

## Temporary workflow rule

Issue-specific dispatchers, historical recovery publishers, one-time baselines, duplicate agent workflows, lifecycle probes, diagnostics, temporary activation/stress workflows, and superseded visual/recovery paths are classified for retirement. A completed repair workflow is removed; it is not made generic merely because it once required elevated permissions.

## Migration order

1. Capture and review the exact workflow/product inventory.
2. Define public APIs and compatibility policy.
3. Build the security, readability, and drift harness.
4. Centralize source, runners, workspace, functions, Agent State transport, platform validation, products, release, Flux assets, and maintenance in dependency order.
5. Migrate each consumer through a linked issue and same-SHA parity proof.
6. Atomically switch required checks, remove duplicate implementation, publish a stable tag, and prove rollback.

During the initial adoption phase, thin callers may reference protected `ci-workflows@main` as the owner-directed rapid-update channel. Git tags remain supported. A `ci-workflows` release is the tag itself, with no GitHub Release object or attached artifact.
