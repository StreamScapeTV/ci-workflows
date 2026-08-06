# Exact admitted checkout

Use this composite action only after `source.resolve` admits an exact source.
Pass `source_repository` as `repository` and `source_sha` as `admitted_sha`.
The action accepts no branch, tag, arbitrary ref, command, or remote URL.

```yaml
- uses: StreamScapeTV/ci-workflows/actions/exact-checkout@<immutable-ref>
  with:
    repository: ${{ needs.source.outputs.source_repository }}
    admitted_sha: ${{ needs.source.outputs.source_sha }}
    path: source
    fetch_depth: ${{ needs.source.outputs.history_depth }}
```

The destination must be empty. The action performs a bounded `--no-tags` fetch,
checks out `FETCH_HEAD` detached, verifies `git rev-parse HEAD` equals the admitted
SHA, and verifies that no HTTP authorization header was persisted in local Git
configuration.
