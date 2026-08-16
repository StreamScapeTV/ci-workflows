# Helm validation and chart publication architecture

Issue #18 owns the common Helm mechanics that product repositories should not duplicate. Product repositories retain their chart layout, values profiles, locked dependencies, product-specific checks, release/tag policy, and registry destination/credentials.

## Product metadata

Each product keeps `.streamscape/helm-product.json` in its own repository. The simple core reads these fields from the exact admitted caller source and validates that the manifest repository/product identity matches the caller request:

- chart name and chart root;
- values-profile map;
- optional product policy path;
- locked Helm dependencies;
- OCI registry repository.

There is no per-product Central allowlist on the simple required path. Legacy image-reference and upstream-provenance fields may remain in the product manifest for compatibility with older helpers, but the core Helm path does not use them as mandatory central release gates.

## Common validation pipeline

The reusable workflow checks out the exact admitted caller SHA and creates marker-bound temporary Helm state. Validation runs only against an isolated chart copy:

1. validate chart structure, metadata, values/schema files, and dependency lock consistency;
2. run `helm dependency build` when locked dependencies are declared;
3. run `helm lint --strict`;
4. run `helm template` for the selected product-owned values profile;
5. reject explicit `latest` image references as generic hygiene without imposing product-specific digest policy;
6. package the chart, binding an optional release version only in the isolated copy;
7. normalize archive order, modes, ownership, and timestamps and scan package members for unsafe paths or secret-like content;
8. reverify exact source and leave the caller checkout unchanged.

Product-specific image assertions, provenance checks, render scenarios, or additional policy belong in the product repository. The optional checked-in policy path remains a way for the common workflow to execute an explicitly selected product-owned check without moving its logic into Central.

## Publication pipeline

Publication uses the same validation/package path. The caller owns its release authority: a Git tag, GitHub release, or another reviewed product release trigger may supply the exact admitted source and SemVer. Central does not inspect the caller event type or implement a separate tag/release authority object for this core path. The Helm command path requires `trusted-exact` source before registry credentials are used.

After validation, Central authenticates to the product-owned OCI destination with caller-provided `registry_username` and `registry_token`, sends the token only through `--password-stdin`, and performs one ordinary `helm push`. The public digest output is the normalized local chart-package SHA-256 used for that push; the compatibility JSON output records the published chart reference and that local identity.

Mandatory pull-before-push, remote Helm pull read-back, Skopeo raw-manifest proof, immutable-replay policy, provenance/canary evidence, runner-measurement gates, and the global action-lock bootstrap are deliberately outside the refactored core acceptance. Older implementation modules may remain checked in for legacy/advanced use so previous engineering is not discarded, but the reusable core workflows do not depend on them.

## Runner, credentials, and cleanup

`helm.validate` uses semantic validation capacity selected by the runner contract. `helm.publish` uses semantic trusted publication capacity selected by Central; callers never choose concrete ARC labels.

Neither workflow receives Kubernetes, Flux, SOPS, deployment, or Agent State authority. Publication receives only the two named registry credentials. Helm package/auth/cache/temp state is removed under terminal cleanup, workspace residue is checked, and routine GitHub Actions artifacts remain zero.

## Compatibility

The public workflow input/output shape remains backward-compatible during this simplification. Optional `image_digest` and `immutable_references_json` inputs remain accepted but are not required or consumed by the core path. A later public-API cleanup may remove legacy compatibility fields separately without coupling that cleanup to issue #18's functional closure.
