# Reusable Helm workflows

The core Helm family has two public reusable workflows:

- `.github/workflows/reusable-helm-validate.yml` runs common Helm validation and packaging;
- `.github/workflows/reusable-helm-publish.yml` performs the same validation/package path and then publishes the package to the product-owned OCI destination with caller-provided registry credentials.

## Ownership boundary

The product repository owns `.streamscape/helm-product.json`. That checked-in product metadata supplies the chart name/root, values profiles, optional product policy path, locked dependencies, and OCI registry repository. Product-specific assertions remain product-owned. Central CI admits the registered product/repository pair and supplies only common Helm mechanics.

The public workflow keeps the older optional `image_digest` and `immutable_references_json` fields for compatibility, but the core Helm path does not require or consume them. Generic Helm validation does not require every rendered image to be digest-pinned; it rejects an explicit `latest` image while product release/image policy remains the product repository's responsibility.

## Validation

`helm.validate`:

1. admits the exact caller SHA and checks out that source without persistent credentials;
2. loads the product-owned Helm metadata;
3. prepares isolated Helm state;
4. runs dependency build when the product declares locked dependencies;
5. runs `helm lint --strict` and `helm template` against the selected values profile;
6. packages the isolated chart copy and normalizes the archive for stable path/order/mode/timestamp handling;
7. removes the package and all Helm/workspace state, verifies zero residue, and confirms the caller checkout stayed clean.

No package is retained as a routine GitHub Actions artifact.

## Publication

`helm.publish` is a thin tag-push publication path. The caller owns version/tag policy and registry destination/credentials. Central requires the invocation to be a `push` whose ref type is `tag`, and requires `github.sha` to equal `admitted_sha` before the publication job can proceed.

The publication job then:

1. checks out the exact admitted source;
2. runs the same common lint/render/package path;
3. authenticates to the product-owned OCI registry using the two named caller secrets;
4. passes the registry token only through `helm registry login --password-stdin`;
5. performs one normal `helm push` of the validated package;
6. returns the normalized local package digest plus the published chart reference;
7. removes Helm package/auth/workspace state and verifies residue is absent.

Mandatory OCI pull/read-back, Skopeo manifest proof, provenance/canary evidence, Buildah measurement, and image-publication evidence binding are not part of the core path. The older helpers that implement those advanced checks may remain for legacy use, but the reusable core workflows do not depend on them.

## Security boundary

Validation may run for ordinary admitted source according to the existing Helm trust model. Publication remains `trusted-exact` and cannot be selected by a pull request or branch run. The workflows do not accept runner labels, shell commands, Kubernetes/Flux targets, kubeconfigs, SOPS material, or cache backends. They never reconcile Flux or install a chart into Kubernetes.

The publish workflow uses the semantic runner selected by the central runner contract, but callers cannot choose the concrete runner. Registry authentication and temporary Helm state are removed under terminal cleanup, and routine Actions artifacts remain zero.
