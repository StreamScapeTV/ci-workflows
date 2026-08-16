# Public workflow API architecture

The public workflow registry is the design authority for organization-facing reusable workflows before implementation. It describes technologies, processes, trust classes, permissions, bounded inputs, outputs, and cleanup. **Application identity is not a compatibility dimension:** ordinary APIs do not contain a central allowlist of application repositories or products.

## Shallow call graph

The normal execution shape is:

```text
consumer caller → public reusable workflow → named function
```

A public workflow may call one reviewed internal reusable-workflow leaf when multi-job orchestration requires it. That internal leaf may not call another reusable workflow. The maximum supported total reusable-workflow depth is therefore two; ordinary APIs remain at depth one.

Public workflow YAML stays readable. Non-trivial algorithms live in named, typed, tested functions under `src/ci_workflows/` and are exposed through thin adapters. Caller-specific commands remain repository-owned hooks selected by bounded profiles or checked-in relative paths; callers never submit arbitrary shell.

## Reference channels

During the initial migration, protected `StreamScapeTV/ci-workflows@main` is the preferred channel for every public workflow family. Full commit SHAs and immutable SemVer tags remain supported for fixed or rollback references. A moving central reference never weakens source admission: caller source, release tags, pull-request heads, device commands, and Flux policy are still resolved as exact inputs.

## Caller and called-workflow boundary

The consumer repository owns:

- repository identity, event triggers, path filters, concurrency, and environments;
- exact project/product names, source/build/chart/output paths, schemes, tasks, scripts, and release manifests;
- minimum caller permissions and explicit named secrets;
- product commands, tests, toolchain pins, fixtures, signing policy, and deployment-specific data.

The called workflow owns:

- exact-source admission and checkout behavior;
- semantic runner intent and internal implementation selection;
- reusable setup, technology orchestration, evidence, cleanup, and redaction;
- bounded build/test/package/publication operations;
- reusable device, maintenance, and Flux transport around the owning system's authority.

The called workflow cannot elevate the caller's `GITHUB_TOKEN`. Each API binds to one permission profile, treats unspecified permissions as `none`, accepts only declared named secrets, and forbids `secrets: inherit`.

A new application repository may adopt an applicable reusable workflow by supplying the documented technology inputs. No central consumer/product list edit is required. Navigation inventories may still record organization repositories or workflow files for discovery and generic maintenance, but those records do not grant or deny API compatibility.

## Trust classes

### Source admission

Source resolution reads GitHub metadata and returns one admitted exact SHA. It does not execute caller source and has no product or infrastructure credential.

### Read-only validation

Validation may execute exact admitted caller source with read-only permissions. Repository-specific task/script choices are bounded caller inputs. Validation publishes zero routine Actions artifacts.

### Agent State exclusion

Agent State is deliberately outside the public workflow registry. `StreamScapeTV/agent-state-supabase` owns Agent State RPCs and lifecycle. This repository exposes no Agent State workflow, secret, or transport fallback.

### Physical-device validation

Device work is explicitly authorized, exact-source, time-bounded, and residue-checked. The API selects a semantic capability; callers do not choose hosts or runner labels.

### Trusted publication

Publication is expressed through technology inputs such as image names, Dockerfile/build-context paths, chart names/paths, versions, and caller-owned release manifests. Central APIs do not select repository-specific build/chart/release configuration through `product_id`. Callers still cannot choose registry hosts, container engines, storage drivers, or registry commands. Publication and deployment remain separate.

The deprecated bootstrap image/chart workflow remains a migration exception. Its caller-owned image/chart/path inputs are preserved until callers migrate to the generic publication/release APIs; recovery behavior remains narrowly bounded by its existing reviewed authority.

### Flux-authorized reconciliation

Flux remains the sole authority for desired state, target allowlists, SOPS/Kubernetes credentials, reconciliation, health, canary selection, and rollback. Central wrappers accept bounded target, operation, policy, and allowlist inputs; they do not use an application `product_id` compatibility list.

### Trusted maintenance

Generic organization maintenance is intentionally preserved. Artifact cleanup, merged-branch hygiene, issue dependency synchronization, workflow inventory/drift checks, organization conformance, and runner-infrastructure retry may inspect organization repositories when their bounded operation requires it. That discovery is operational scope, not reusable-API application identity or product compatibility.

## Inputs, outputs, and forbidden fields

Public inputs are typed in `contracts/public-workflow-types.json`. Callers cannot supply concrete runner labels, container-engine commands, registry hosts/commands, secret names, kubeconfig paths, clusters/namespaces, arbitrary shell/callbacks, or `product_id` as a selector for central application configuration.

Caller-owned relative paths and technology identifiers are allowed when the API needs them: for example `dockerfile_path`, `build_context`, `image_name`, `chart_path`, `chart_name`, `values_path`, `policy_path`, and `release_manifest_path`. Those inputs describe what technology operation to perform without making the central repository the application configuration authority.

Outputs are bounded catalog entries suitable for job outputs and concise summaries. Evidence is redacted and structured. Routine logs, result bundles, build products, archives, images, charts, database dumps, private media, and environment snapshots are not retained as evidence.

## Artifacts and cleanup

The default is zero routine Actions artifacts. A named exception must define producer, consumer, exact content, privacy classification, integrity, retention, and cleanup. Cleanup runs on success, failure, cancellation, and timeout and fails closed on credential, authentication-file, container, image, chart, cache, device, simulator, or temporary-state residue.

## Compatibility and implementation order

The registry is reviewed before implementation. Compatible additions, conditional changes, and breaking changes are classified by `scripts/ci/public_api_contract.py`. Breaking changes require an explicit acknowledgement with a migration issue and effective version.

A `migration-pending` API record means the reviewed next technology contract exists while the current reusable YAML still exposes its older interface. The follow-on wiring issue must migrate the YAML/CLI and then mark the version implemented. This allows contract architecture to be reviewed independently without falsely claiming old application-coupled YAML implements the new API.
