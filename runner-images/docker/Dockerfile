# Docker-capable Actions runner. The image contains client tooling only; the daemon is a separate Flux-owned runtime sidecar.
FROM docker.io/library/docker:29.8.0-cli@sha256:eccaacfeed644c7de222ff047483568cb988dde95476fbaaf10ea2d04921bb66 AS docker-cli

FROM ghcr.io/actions/actions-runner:2.337.0@sha256:e5496277be5d09bc968b3d64911b74e219ac4a3f2edce956a3ecf9271bea1ef4 AS actions-runner

USER root
RUN set -eux; \
    rm -f \
      /usr/bin/containerd \
      /usr/bin/containerd-shim-runc-v2 \
      /usr/bin/ctr \
      /usr/bin/docker \
      /usr/bin/docker-init \
      /usr/bin/docker-proxy \
      /usr/bin/dockerd \
      /usr/bin/runc \
      /usr/local/bin/docker \
      /usr/local/bin/dockerd; \
    rm -rf \
      /etc/docker \
      /root/.docker \
      /home/runner/.docker \
      /usr/libexec/docker \
      /usr/local/lib/docker \
      /var/lib/docker; \
    ! command -v dockerd; \
    ! command -v containerd; \
    ! command -v ctr; \
    ! command -v runc; \
    test ! -e /var/run/docker.sock; \
    test ! -e /run/docker.sock

COPY --from=docker-cli --chmod=0755 /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli --chmod=0755 /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/lib/docker/cli-plugins/docker-buildx
COPY --from=docker-cli --chmod=0755 /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
COPY --from=docker-cli --chmod=0755 /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose
COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

USER runner
RUN /usr/local/bin/runner-image-smoke
