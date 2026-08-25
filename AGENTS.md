# AGENTS.md — StreamScapeTV/ci-workflows

## Repository identity

- Repository: `StreamScapeTV/ci-workflows`
- Agent State project key: `ci-workflows`
- Protected integration branch: `main`
- Sole shared organization-policy entry point: `StreamScapeTV/organization-rules@main/AGENTS.md`

The project key is exactly `ci-workflows`. Never replace it with a phase, wave, batch, issue, branch, pull request, task name, or display title.

Before any work, read this file and then the current shared organization entry point. Read local `RUNNERS.md` when the bounded task requires CI capability selection, followed only by the repository architecture, contracts, source, tests, and issue material needed for the assigned slice. This file defines only central-CI product authority and stricter workflow requirements; the shared entry point owns the generic collaboration and development lifecycle.

## Repository authority and scope

This public repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories, including central source admission, semantic runner resolution, validation, publication mechanics, release support, and Flux orchestration. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts, contracts, policy, credentials, and deployment data.

Public visibility permits outside forks and pull requests; it does not grant outside contributors Central runner capacity or trusted execution. Same-repository branch pushes are the preferred self-CI path. Any retained repository `pull_request` job must be rejected before runner allocation unless the pull-request author is exactly `mimranfaruqi` and the head repository is exactly this repository. Do not broaden that exception to members, collaborators, prior contributors, association classes, or approval history. `contracts/repository-policy.json` is the machine-readable workflow event/trust inventory and the canonical validation harness enforces it.

`main` is the integration branch and initial bootstrap consumer channel. This repository is released by an exact compatible Git tag; it does not require a GitHub Release object, attached archive, container image, or Helm chart for its own release.

## Agent State boundary

- Ordinary Central reusable workflows, actions, validation functions and publication paths contain no Agent State transport, project mapping, credentials, ownership logic, issue bridge, MCP/plugin setup, or compatibility client. The reviewed #495 exception consists only of the shared `ci-lifecycle` client, the opaque private-CI executor/action, the dedicated Central dispatch orchestration, the bounded R2 private-log transport, and the external thin relay.
- The external service deployed by Flux for issue #495 is a transport-only relay under `src/ci_workflows/ci_relay*` and `scripts/ci/ci_broker.py`. It may authenticate the Supabase INSERT webhook, claim one bounded CI request, validate the reviewed workflow/profile, reject/cancel an invalid or definitively failed dispatch, and fire-and-forget the fixed Central workflow. Its public GitHub dispatch payload contains only the opaque `ci_run_id` and opaque hashed active identity. It must not resolve or check out source, read product configuration, admit dependencies, execute product logic, discover GitHub runs, process build logs, persist logs, or own normal lifecycle after dispatch.
- The dedicated Central dispatch workflow may receive fixed Agent State CI-service credentials, fixed source GitHub App credentials, and fixed R2 S3 credentials only inside its trusted private executor. The workflow-dispatch event and composite-action inputs must not contain the private source repository, project key, ref, tag bit, workflow/profile, source SHA, dependency identity, product configuration, or private command output.
- Central re-claims the canonical request internally by opaque CI UUID, records the actual GitHub run identity itself, resolves the human branch/tag inside the trusted executor, records the observed SHA as evidence only, and executes the existing canonical Apple implementation functions. Detailed private checkout/build/test/cleanup stdout and stderr stay runner-local; they are gzip-compressed, uploaded to private Cloudflare R2, downloaded again, and SHA-256 verified before terminal Agent State status is written. Agent State stores no log body, only bounded status/error plus the R2 receipt/status compatibility fields.
- Public GitHub Actions output for a private-source Central run is limited to generic Central orchestration/pass-fail information. Never emit private repository/ref/config/dependency names or private command stdout/stderr through workflow `with`, `env`, step output, job output, summary, shell tracing, or GitHub Actions artifacts. Cloudflare D1 is not part of #495 private-log storage.
- The superseded broker OIDC callback, `/actions/start`/`finish`, broker-side source/dependency/build execution, D1 diagnostic sink, and opaque dispatch-token exchange model must not be restored. R2 is a workflow-owned private log sink, not broker execution authority.
- The relay/lifecycle transport must not be reused for agent ownership, work, resources, coordination, issue topology, prompts, assignments, or arbitrary database access. All Agent State application code remains RPC-only; do not restore issue #32's superseded lifecycle/ownership workflow architecture or retired compatibility transport.
- Inspecting or modifying `StreamScapeTV/agent-state-supabase`, its hosted project, migrations, grants or deployment still requires a separate explicit assignment in that project. Never invent an Agent State receipt, schema state, deployment result, or fallback transport.

## Reusable workflow architecture

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml` and expose `workflow_call` only.
- Optional internal multi-job leaf workflows live under `.github/workflows/internal-*.yml`, may not call another reusable workflow, and must preserve the reviewed shallow call graph.
- Consumers own event triggers, concurrency, environments, minimum caller permissions, and bounded product configuration. Reusable workflows must not silently add scheduled, branch, manual publication, or trusted-dispatch paths.
- During bootstrap, consumers may call `@main`; tagged and full-SHA references remain supported. Privileged and production callers should migrate to immutable references after a stable tag when required by reviewed policy.
- Keep workflow YAML as short, ordered orchestration. Put non-trivial algorithms in named, typed, tested functions under `src/ci_workflows/` and expose them through thin composite actions or CLI adapters.
- Public inputs and outputs must match checked-in contracts and generated reference documentation. Inputs must be bounded and may not accept arbitrary shell commands, callbacks, registry hosts, runner labels, container engines, cluster targets, namespaces, service accounts, secret names, or unrestricted matrices.
- Public and internal workflow calls and composite-action calls must remain acyclic, accessible, shallow, and compatible with the supported consumer and product inventory.

### Function-first implementation rule

- Python functions under `src/ci_workflows/` are the implementation layer. Workflow YAML owns orchestration only; composite actions and CLI adapters stay thin and delegate reusable behavior to named tested functions.
- Central function/workflow/action names describe technologies or capabilities, not product identities. Product paths, tasks, scripts, and options remain bounded caller inputs and product-owned behavior.
- Secrets are read only from fixed named environment variables chosen by the central implementation. Never log secret values or accept a caller-selected secret-variable name as a public input.
- Ordinary validation must not depend on immutable-digest, remote read-back, provenance-ledger, canary, or rollback machinery unless the bounded workflow actually requires publication/deployment semantics. Publication-specific safeguards in this repository remain authoritative where explicitly required. Private-source log export is a security boundary and therefore explicitly requires R2 upload/read-back verification before terminal lifecycle.
- Do not add GitHub Actions cache as a workflow feature for local runners. Flux owns runner-side caching, persistent/shared storage, and deployed runner infrastructure.

## Authority boundaries

- Flux remains the sole authority for desired state, target and product allowlists, SOPS data, Kubernetes credentials, reconciliation policy, canary selection, live health, and rollback acceptance. This repository owns only reviewed orchestration around exact Flux-owned policy source.
- Product repositories retain toolchain pins, product commands, schemas, assertions, test selection, signing, release inputs, and deployment-specific data.
- Shared organization policy changes belong only in `StreamScapeTV/organization-rules`.

## Runner and central self-check boundary

- Semantic runner intent remains authoritative. Ordinary Python, policy, source-admission, and GitOps validation use the `portable` capability; consumers do not select concrete runner labels or hosts.
- The Central self-check runs on trusted same-repository branch pushes. A retained pull-request self-check is admitted at the job boundary only for exact owner `mimranfaruqi` with a same-repository head, so an outside or prior-contributor PR cannot allocate its GitHub-hosted runner.
- After job admission, the Central self-check verifies an absolute pre-provisioned CPython 3.12 Linux runtime before checkout. It installs or elevates no host runtime, applies the repository's digest-locked validation dependency bootstrap, and uses the verified absolute interpreter for every later Python command.
- General Linux validation grants no signing, provisioning, simulator, physical-device, notarization, store, registry, Kubernetes, production, or Agent State credential or authority.
- Do not restore the retired emergency macOS exception or copy it into another workflow. Apple-specific work continues to use separately reviewed `apple` capacity. The #495 private-source host path is the explicit reviewed GitHub-hosted `macos-latest` exception and has no physical-device authority.

## Security, artifacts, and cleanup

- Use explicit workflow and job permissions, explicit named secrets, workflow-scoped authentication files, and no `secrets: inherit`.
- Never execute untrusted pull-request, fork, issue-comment, `pull_request_target`, `workflow_run`, or mutable source in a privileged context.
- Untrusted public pull requests must not allocate GitHub-hosted, organization, Apple, device, image-build, registry, Flux, or other trusted Central runner capacity. Repository source checks inside an already-started job are defense in depth, not runner admission.
- Privileged modes require exact admitted source, exact checkout assertions, detached credential-free state where applicable, and `persist-credentials: false`.
- Central workflows select semantic runner profiles and internal implementations. Consumers do not select concrete runner labels, hosts, Docker versus Buildah, storage drivers, devices, clusters, namespaces, or service accounts.
- Routine workflows retain zero GitHub Actions artifacts. Private-source detailed logs are never Actions artifacts; they use the fixed private R2 path described above. Any other artifact exception must be named, bounded, justified, redacted, registered in contract, and tested.
- Cleanup runs under `if: always()` and fails closed when credentials, authentication files, containers, images, charts, caches, generated output, device or simulator state, result bundles, private log files, source checkouts, or temporary workspace state remain.

## Publication and release safety

- Product publication is admitted only from an exact approved Git tag and exact tagged source SHA.
- Immutable image and chart versions use the exact approved tag. Runner-image releases additionally publish the same built artifact under the mutable `latest` alias after the exact versioned tag is published; `latest` is a convenience deployment alias and is never release/source authority.
- The repository Git tag `latest` is not a valid release-version input for the runner-image workflow; an ordinary version/tag such as `1.0` remains the release authority and produces both `:1.0` and `:latest`.
- Historical tags build the exact historical commit without rewriting branches. Replaying an historical runner-image tag may therefore also move the mutable `latest` alias to that replayed artifact; use replay deliberately.
- Publication and deployment remain separate. Product release workflows do not receive Kubernetes or SOPS credentials and do not mutate clusters.
- Published images and charts require independent remote read-back. Runner-image publication must read back both the exact versioned tag and `latest` before the image job succeeds. Replays are idempotent for exact versioned content, and conflicting immutable content fails closed.

## Product validation contract

- Same-repository branch push is the normal pre-PR validation path. The canonical Central self-check may also validate an exact completed owner pull-request candidate against its current base when that PR path is retained.
- Workflow and action parsing, action and tool pins, permissions, trust classes, source admission, runner profiles, call graphs, readability, public API compatibility, documentation, inventory, fixtures, discovered tests, cleanup, and artifact policy must remain green.
