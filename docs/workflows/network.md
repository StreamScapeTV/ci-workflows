# Network download and extraction

Issue #363 publishes one product-neutral network capability on top of the shared `network_primitives` implementation. It is intentionally limited to bounded dependency retrieval and archive extraction; it does not add package-manager policy, provenance, signing, deployment, GitHub Actions cache transport, or arbitrary shell execution.

## Same-job composition

Use `actions/network` when a later step in the **same job** must consume the downloaded file or extracted directory. The action returns `local_path`, which is a runner-local absolute path inside the marker-bound workflow state prepared by `actions/prepare-workspace`.

```yaml
steps:
  - id: workspace
    uses: StreamScapeTV/ci-workflows/actions/prepare-workspace@<immutable-sha>
    with:
      profile: minimal
      cache_mode: disabled

  - id: fetch
    uses: StreamScapeTV/ci-workflows/actions/network@<immutable-sha>
    with:
      operation: download
      url: https://downloads.example.org/tool.zip
      relative_path: tool.zip
      expected_sha256: <64-hex-sha256>
      maximum_bytes: "104857600"

  - id: unpack
    uses: StreamScapeTV/ci-workflows/actions/network@<immutable-sha>
    with:
      operation: extract
      relative_path: tool.zip
      archive_format: zip
      relative_destination: tool

  - name: Consume extracted dependency
    shell: bash
    run: test -x "${{ steps.unpack.outputs.local_path }}/bin/tool"

  - if: always()
    uses: StreamScapeTV/ci-workflows/actions/cleanup-workspace@<immutable-sha>
```

The caller owns the surrounding job and must run terminal workspace cleanup with `if: always()`.

## Reusable workflow

`.github/workflows/reusable-network-download.yml` is the public `network.download` preflight wrapper. It prepares isolated state, performs one verified HTTPS download, optionally extracts ZIP or TAR content, and always removes registered state. Its public outputs are bounded result metadata:

- `result`
- `download_result_json`
- `extraction_result_json`
- `cleanup_result`

A reusable workflow call is a separate GitHub Actions job. Therefore it intentionally does **not** expose a `local_path`: runner-local files from the called job cannot be consumed by later caller steps after that job ends, and this API does not create an artifact bridge. Workflows that need the bytes for a later build/test step should use the same-job composite action above.

## Network and integrity policy

Downloads use HTTPS by default. The primitive rejects unbounded or malformed redirects, unsupported response encoding, over-limit bodies, unsafe destination paths, and partial writes. Callers may provide an exact SHA-256, exact byte size, and exact media type. `maximum_bytes` defaults to 512 MiB and the primitive hard limit is 8 GiB.

ZIP and TAR extraction is staged and bounded. Archive traversal, absolute paths, symlink/special members, duplicate/conflicting members, encrypted ZIP entries, excessive member counts, and excessive expansion are rejected before finalization. Extracted content is atomically moved into the requested relative generated-state directory.

The wrapper currently has no caller-provided credentials or custom HTTP headers. Network/integrity/extraction failures surface through the stable CIW projection as `ciw network run failed: <code>` and the job fails while terminal registered-state cleanup still runs.

Routine Actions artifacts and Actions cache entries are not created by this capability.
