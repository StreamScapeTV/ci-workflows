# Trusted OCI publication (`oci.publish`)

`reusable-oci-publish.yml` is the privileged publication layer for inventory-approved OCI products. It is separate from pull-request build validation and accepts only an exact admitted source SHA, a checked-in product ID, and an exact stable SemVer release version. Registry credentials are two explicitly named workflow-call secrets. Callers cannot select a registry host, repository, builder, container engine, runner labels, storage driver, shell command, callback, Flux target, Kubernetes target, or publication script.

The workflow has one bounded path:

1. **resolve-product** — resolve the exact existing release tag and the checked-in OCI product/runner contract;
2. **build-exact-source** — check out the admitted SHA and rebuild/inspect it through the non-publishing OCI build contract from issue #16;
3. **authenticate** — create one workflow-scoped registry auth file and authenticate to the fixed registry host with the two named credentials;
4. **publish** — publish only the immutable release-version and `sha-<40-hex>` identities, accepting an existing reference only when its digest exactly matches the local built manifest;
5. **read-back** — independently pull the published version identity through Skopeo into a fresh OCI layout and inspect the registry bytes;
6. **verify** — require manifest, per-platform manifest/config/layer digests, metadata, runtime assertions, and source/version labels to match the locally validated build; require the source-SHA identity to resolve to the same manifest;
7. **clean** — remove registry auth, read-back layouts, publication state, OCI build state, and registered workspace state under `if: always()` and then verify residue is absent.

No routine Actions artifact is retained. Publication never mutates Flux or Kubernetes state.

## Trust and release authority

Publication rejects pull-request, `pull_request_target`, and `workflow_run` source. The exact admitted SHA must equal the source SHA returned by the central release-tag authority for the exact stable version. The workflow resolves that authority in the planning job and revalidates the same tag object/commit immediately before privileged build and registry work.

The publication action additionally requires the exact release-authority SHA on every privileged phase, so an action invocation cannot turn a different SHA into a release merely by supplying a product ID and version.

## Destination policy

The merged publication layer has no caller destination input. It derives a canonical GHCR destination from the checked-in `contracts/oci-products.json` source repository and target inventory:

- a single-target product maps to `ghcr.io/streamscapetv/<source-repository-name>`;
- a multi-target product maps each target to `ghcr.io/streamscapetv/<source-repository-name>-<target-id>`.

This is deliberately fail-closed and deterministic. The merged `oci.build` contract owns product/source/assertion inventory in `contracts/oci-products.json`, and `oci.publish` consumes that same checked-in authority instead of maintaining a second destination or product decision engine. Any future publication-specific inventory extension must be reviewed through the shared contract and public API rather than introduced as caller-selected destination authority.

## Immutable identity and replay behavior

For every target the workflow uses exactly two tags:

- `<release-version>` — for example `2.4.1`;
- `sha-<exact-source-sha>`.

Before any write, both remote references are inspected. If either exists with a different manifest digest, publication fails with `immutable_reference_conflict` before repairing or replacing anything. If an existing reference matches exactly, it is accepted as a replay. A partially present matching pair is repaired by publishing only the missing immutable identity. After publication, both references are read again and must resolve to the exact local manifest digest.

There is no mutable convenience tag.

## Independent read-back

The builder creates and validates a local OCI layout. Publication uses Skopeo's OCI-to-registry path. Verification then performs a separate registry-to-OCI Skopeo copy into a fresh read-back layout and checks:

- top-level registry manifest/index digest;
- exact expected platform set;
- per-platform manifest digest;
- config digest and layer digest list;
- source SHA, stable version, product ID, title, description, license, source URL, and created metadata labels;
- configured user, entrypoint, command, and exposed ports.

The remote per-platform manifest/config/layer identities must equal the local validated layout exactly. That byte identity preserves the merged `oci.build` local assertions for required files/tools and forbidden tools without executing untrusted product scripts in the publication verifier.

## Registry credentials and cleanup

`registry_username` and `registry_token` are the only publication secrets. The token is passed to `skopeo login` through stdin and is never placed in a command-line argument, output, evidence object, state JSON, or artifact. The generated auth file is forced to owner-only permissions and exists only below the marker-derived publication state under `RUNNER_TEMP`.

Cleanup removes the auth file together with all read-back and publication state. Symlink substitution of the state root is unlinked without following the target and is treated as a cleanup failure. The separate OCI build cleanup removes local manifests, images, builders, layouts, and caches. Both layers then run residue checks.

## Reusable workflow call

A caller provides the already-admitted exact source and stable release version. The release tag must already exist and resolve to that exact SHA.

During active Central development, repository consumers call public reusable
`ci-workflows` workflows at `@main` as ordinary shared-library references. No
per-product bootstrap or registration step, consumer-maintained Central SHA, or
synchronization handshake is required. Human-readable compatibility tags and
full-SHA references remain supported, and a later reviewed policy may prefer a
stable tag such as `@v1`. This does not weaken internal/private helper pins,
which remain exact immutable SHAs.

```yaml
jobs:
  publish_backend:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-oci-publish.yml@main
    with:
      admitted_sha: ${{ needs.release_source.outputs.sha }}
      product_id: iptv-backend-image
      release_version: ${{ needs.release_source.outputs.version }}
      # Optional contract confirmation; omission never broadens the checked-in matrix.
      platform_set: linux-multi-arch
    secrets:
      registry_username: ${{ secrets.OCI_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.OCI_REGISTRY_TOKEN }}
```

The called workflow itself has no branch, schedule, manual, or pull-request trigger. `platform_set` is optional and can only confirm the exact checked-in platform set for every target; it cannot request a new or narrower matrix.

## Product shapes

### IPTV backend

`iptv-backend-image` is a single product target. Its canonical repository is derived from the checked-in source repository, and the workflow returns its immutable version/SHA references plus manifest and platform evidence. Product-owned startup/runtime assertions remain in `contracts/oci-products.json` and are not caller inputs.

### Agent State

`agent-state-image` uses the same API. No Agent State database, Supabase project, schema, credential, or lifecycle authority is available to the publication workflow. The workflow handles only the OCI image bytes and registry credentials.

### Flux runner images

`flux-runner-images` is a multi-target product. Each checked-in runner target receives a distinct deterministic repository. Read-back must preserve the exact tool/runtime bytes already validated by the merged `oci.build` layer. The contract-owned canary, previous-known-good, and rollback identities are carried inside `immutable_references_json` for later issue-#33 selection logic. Publication does not edit Flux manifests, choose a live runner image, reconcile a scale set, or receive Kubernetes/SOPS authority.

## Outputs

The final verify phase returns only the four registered redacted values:

- `result`;
- `image_digest` — deterministic JSON map of target IDs to read-back manifest/index digests;
- `platform_digests_json` — per-target platform manifest/config/layer identity evidence;
- `immutable_references_json` — canonical target repositories plus version/source identities and, for Flux products, the contract-owned canary/previous-known-good/rollback handoff identities.

No credential, auth-file path, runner identity, private host detail, builder storage path, or image archive is returned.

## Validation status and private-registry proof

The merged central mock/unit suite covers trusted/untrusted publication boundaries, exact release authority, fixed destination derivation, immutable replay and conflict behavior, multi-platform manifest/config/layer read-back, metadata/runtime assertion rejection, workflow API restrictions, zero-artifact smoke behavior, and symlink-safe cleanup.

Completion of the central `oci.publish` mechanism does not by itself activate any producer or claim a live private-registry publication. Exact producer readiness, least-authority registry write policy, private-registry first-publish/replay/conflict/read-back evidence, and per-product adoption remain separately gated by the current OCI adoption work (issue #154). A successful central mock/unit or contract run proves only the code and contract paths that executed; it does not substitute for those live adoption proofs.
