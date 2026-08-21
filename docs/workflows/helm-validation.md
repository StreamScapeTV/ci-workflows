# Reusable Helm workflows

The core Helm family has two public reusable workflows:

- `.github/workflows/reusable-helm-validate.yml` runs common Helm validation and packaging;
- `.github/workflows/reusable-helm-publish.yml` performs the same validation/package path and then publishes the package to the product-owned OCI destination with caller-provided registry credentials.

## Ownership boundary

The product repository owns `.streamscape/helm-product.json`. That checked-in product metadata supplies the product identity, repository identity, chart name/root, values profiles, optional product policy path, locked dependencies, and OCI registry repository. Product-specific assertions remain product-owned. Central validates that exact caller metadata and supplies only common Helm mechanics; it does not require a per-product central allowlist.

The public workflow keeps the older optional `image_digest` and `immutable_references_json` fields for compatibility, but the core Helm path does not require or consume them. Generic Helm validation does not require every rendered image to be digest-pinned; it rejects an explicit `latest` image while product release/image policy remains the product repository's responsibility.

## Validation

`helm.validate` accepts the shared optional `execution_backend` input. `organization` remains the default and preserves semantic `general-small` organization capacity. Explicit `github-hosted` runs the same read-only validation contract on standard GitHub-hosted `ubuntu-latest`; it never falls back to StreamScapeTV private/ARC capacity. Repository visibility does not select the backend automatically, and callers cannot pass raw runner labels.

Planner capacity follows that same bounded choice. Explicit `github-hosted` calls use a lightweight `ubuntu-latest` plan/backend-resolution job; default or explicit `organization` calls use the existing `[linux, amd64, general, small]` planner. The two planners are mutually exclusive, and validation consumes only the successful planner's Central-owned selector. The validation job then:

1. admits the exact caller SHA and checks out that source without persistent credentials;
2. loads the product-owned Helm metadata;
3. prepares isolated Helm state;
4. runs dependency build when the product declares locked dependencies;
5. runs `helm lint --strict` and `helm template` against the selected values profile;
6. packages the isolated chart copy and normalizes the archive for stable path/order/mode/timestamp handling;
7. removes the package and all Helm/workspace state, verifies zero residue, and confirms the caller checkout stayed clean.

No package is retained as a routine GitHub Actions artifact.

## Publication

`helm.publish` is an ordinary trusted publication path. Its existing runner and permission behavior is unchanged by the hosted validation option. The caller owns whether the release authority is a Git tag push, GitHub release, or another reviewed product release trigger; it also owns version policy and the registry destination/credentials. Central receives the exact admitted source SHA plus release version and does not inspect or enforce the caller event kind.

The publication job then:

1. checks out the exact admitted source;
2. runs the same common lint/render/package path;
3. authenticates to the product-owned OCI registry using the two named caller secrets;
4. passes the registry token only through `helm registry login --password-stdin`;
5. performs one normal `helm push` of the validated package;
6. returns the normalized local package digest plus the published chart reference;
7. removes Helm package/auth/workspace state and verifies residue is absent.

Mandatory OCI pull/read-back, Skopeo manifest proof, provenance/canary evidence, Buildah measurement, image-publication evidence binding, and the global action-lock bootstrap are not part of the core path. Older helpers may remain for legacy use, but the reusable core workflows do not depend on them.

## Security boundary

Validation may run for ordinary admitted source according to the existing Helm trust model on either supported backend. Publication requires `trusted-exact` source before registry credentials are used, but the caller decides which reviewed release event supplies that exact source. The workflows do not accept runner labels, shell commands, Kubernetes/Flux targets, kubeconfigs, SOPS material, or cache backends. They never reconcile Flux or install a chart into Kubernetes.

Central owns backend and semantic runner mapping. Registry authentication and temporary Helm state are removed under terminal cleanup, and routine Actions artifacts remain zero.
