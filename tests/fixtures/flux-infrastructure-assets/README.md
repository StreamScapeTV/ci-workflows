# Flux infrastructure asset fixtures

These fixtures model issue #33's non-secret contract boundary. They intentionally
contain no registry credentials, Kubernetes objects, SOPS data, cluster names,
or live selection policy.

`live-inventory.json` captures the current product shape used to detect drift:
Buildah and Mobile are the only custom Flux runner image roots, Portable remains
an upstream Actions runner image, and `apps/github-actions-runner` is the
confirmed runner chart.

`dependency-evidence.json` contains fake deterministic digest/reference data for
unit tests. Only output **names** that already exist in the merged public catalog
are treated as current authority. Nested payload examples are synthetic fixture
data, not a substitute for an unmerged #17/#18 implementation contract; final
#33 dependency adapters must be reconciled to the exact payloads that actually
merge on `main` before release wiring is enabled.

`cases.json` is the required negative/rollback inventory for bootstrap,
immutable-conflict, forbidden runtime state, unsupported products, malicious
paths, chart attribution, residue, and rollback behavior.
