# Flux infrastructure asset fixtures

These fixtures model issue #33's non-secret contract boundary. They intentionally
contain no registry credentials, Kubernetes objects, SOPS data, cluster names,
or live selection policy.

`live-inventory.json` captures the current product shape used to detect drift:
Buildah and Mobile are the only custom Flux runner image roots, Portable remains
an upstream Actions runner image, and `apps/github-actions-runner` is the
confirmed runner chart.

`dependency-evidence.json` contains synthetic immutable outputs shaped like the
registered #16/#17/#18 public APIs. Digests and references are fake deterministic
values used only for unit tests.

`cases.json` is the required negative/rollback inventory for bootstrap,
immutable-conflict, forbidden runtime state, unsupported products, malicious
paths, chart attribution, residue, and rollback behavior.
