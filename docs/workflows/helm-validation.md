# Reusable Helm workflows

`.github/workflows/reusable-helm-validate.yml` is the read-only
`helm.validate` implementation in issue #18.  It accepts an exact admitted SHA,
one product ID, and optional checked-in values/policy selectors.  It outputs a
deterministic chart digest and retains no Actions artifact.

`.github/workflows/reusable-helm-publish.yml` is the trusted `helm.publish`
implementation.  It requires an exact release version plus the named
`registry_username` and `registry_token` secrets.  Its only write is an
idempotent immutable OCI chart push after a pre-push pull comparison; it then
pulls the same version back and verifies the normalized package digest.

Both workflows use the portable semantic capability through a protected plan,
verify their exact central and caller checkouts, use `if: always()` cleanup, and
leave no package, registry configuration, cache, or result artifact behind.
Shared public-registration, dispatcher, generated-documentation, and consumer
adoption changes remain serialized behind the OCI publication predecessor.
