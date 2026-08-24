# CI broker Helm chart

This chart packages the Kubernetes runtime for the event-driven Central CI broker.

Release tags use `ci-broker-X.Y.Z`. The release workflow strips the prefix and publishes both:

- `git.faruqi.dev/mimranfaruqi/ci-broker:X.Y.Z`
- `oci://git.faruqi.dev/mimranfaruqi/helm-charts/ci-broker:X.Y.Z`

The packaged chart receives both `version=X.Y.Z` and `appVersion=X.Y.Z`. The Deployment defaults its image tag from `.Chart.AppVersion`, so a new chart version carries the matching broker image version without a mutable `latest` dependency.

The first broker implementation must run exactly one replica because its short-lived dispatch replay state is process-local. The chart schema therefore fixes `replicaCount` to `1` until a shared replay store is implemented.

Runtime credentials are not part of the chart. The Deployment consumes the existing `ci-broker-secrets` Secret, and private image pulls use the existing `private-registry` image-pull Secret. The container runs non-root with a read-only root filesystem and receives only a bounded memory-backed `/tmp` for GitHub App signing.
