# Reusable Helm workflows

`.github/workflows/reusable-helm-validate.yml` implements the read-only
`helm.validate` contract. It accepts the reserved public inputs: exact admitted
SHA, product ID, optional exact release version, optional checked-in values
profile/policy path, and the zero-artifact exception field. Source trust is not
caller-controlled; the protected workflow derives `untrusted-fork`,
`trusted-pr`, or `trusted-exact` from the caller event and passes that explicit
classification to the internal action.

`.github/workflows/reusable-helm-publish.yml` implements trusted
`helm.publish`. It requires an exact release version and only the named
`registry_username` and `registry_token` secrets. Pull-request and
pull-request-target contexts are classified `trusted-pr`; the bounded Helm
planner rejects publication unless trust is `trusted-exact`.

Both workflows verify the exact central workflow checkout and exact admitted
caller source, prepare isolated state, and use `if: always()` Helm-specific plus
shared workspace cleanup. The validation/package implementation works on a copy
under temporary state, so dependency build and release version binding never
dirty the caller checkout. No package, rendered output, registry state, cache,
or diagnostic Actions artifact is retained.

Publication performs immutable pull-compare-before-push and mandatory pull
read-back. It rejects Kubernetes authority before registry login and never runs
`helm install`, `helm upgrade`, `kubectl`, Flux reconciliation, SOPS
decryption, or `latest`.

The Helm-exclusive branch tracks the real local producers:
`iptv-backend-chart`, `agent-state-chart`, and
`flux-github-actions-runner-chart`. Flux's separately mirrored upstream ARC
charts remain outside the local wrapper-chart manifest until Flux records the
complete upstream provenance tuple required by issue #18.

Shared public registration, dispatcher/bootstrap wiring, generated
documentation/inventory, runner-profile policy, and consumer adoption remain a
separate serialized integration lane. This slice intentionally does not edit
those shared files.
