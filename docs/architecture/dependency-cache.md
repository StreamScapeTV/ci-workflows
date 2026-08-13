# Native dependency caching

Issue #149 standardizes dependency-download caching for StreamScapeTV self-hosted validation. The cache is an optimization only: product lockfiles, runtime contracts, exact-source admission, clean-tree checks, cleanup, and zero routine Actions artifacts remain authoritative.

## Trust and lifecycle

Pull-request validation may restore a matching cache but cannot save one. A save is eligible only after the bounded validation itself succeeded on a protected push to the repository's actual default branch, with `trusted-exact` source classification. The cache action derives the default branch from the GitHub event payload and requires `GITHUB_REF_PROTECTED=true`; this is not a caller-selectable publication switch. Tag, workflow-dispatch, fork, ordinary branch, failed, and unprotected runs are restore-only.

Restore and save are separate phases. A reusable workflow restores after exact checkout and marker-bound workspace preparation, executes the product validation, then invokes save only on the successful terminal path and before workspace cleanup. An exact restore hit suppresses the later save.

## Key and poisoning policy

Native cache keys contain repository identity, dependency family, exact dependency-input digest, runner OS/architecture, and bounded validation profile. They intentionally do not contain the source SHA: unchanged lock inputs can therefore reuse download content across commits. There are no broad restore prefixes. A dependency-input change creates a different exact key, and only protected successful integration can populate a new key.

Identity files are contract-selected and bounded by file count and total bytes. Symlinked, escaped, missing, or oversized identity sets fail closed. Cache paths are also contract-selected. Every resolved path must stay below `CI_WORKFLOW_ROOT`, so repository source, repository build output, host-global caches, credentials, rendered manifests, and arbitrary caller paths cannot be cached by this action.

## Families

- `npm`: `package-lock.json`; caches the workflow-scoped npm download cache, never `node_modules` or static build output.
- `gradle`: wrapper/dependency build inputs; caches only Gradle module downloads and wrapper distributions, never project `build/` trees.
- `maven`: POM/wrapper identity; caches only the workflow-scoped local artifact repository.
- `pip`: requirements/lock metadata; caches the workflow-scoped pip download cache, never a virtual environment or installed source tree.
- `pub`: `pubspec.lock` plus `pubspec.yaml`; caches only the workflow-scoped Pub package cache, never Flutter build output.
- `helm`: `Chart.lock` plus `Chart.yaml`; supports the native-cache audit for reproducible repository/dependency downloads only. Rendered templates, packaged charts, registry state, and credentials remain outside the cache.

The family catalog is central policy rather than public workflow input authority. Consumer-facing validation APIs continue to select only reviewed product profiles; the reusable workflow maps those profiles to a cache family internally.

## Retention and measurements

Retention and eviction use GitHub Actions cache behavior. There is no scheduled cache-cleanup workflow and no cache data is uploaded as an Actions artifact. Each restore/save phase records a bounded sample containing only family, phase, hit boolean, and duration in milliseconds in the step summary. These samples support hit-rate and transfer-duration comparison before considering any separate Flux/Longhorn cache design.

## Integration rule

The typed cache engine and composite action are reusable across Node, Android/Gradle, Python, Flutter/Pub, Maven, and Helm validation. Final workflow wiring must preserve the existing immutable action/tool lock and be applied only after the shared registration owner releases that file. Until a family is wired by its reviewed central workflow, existing cache-disabled behavior remains unchanged.
