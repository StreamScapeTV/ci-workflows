# Docker-capable Actions runner. The image contains client tooling only; the daemon is a separate runtime sidecar.
FROM docker.io/library/docker:29.7.2-cli@sha256:000bb62ff495f986c9f5578eb67cc2cb98b91138eda81d7762d5371eb8a497fe AS docker-cli

FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner

USER root
RUN rm -f /usr/bin/docker /usr/local/bin/docker /usr/local/lib/docker/cli-plugins/docker-buildx /usr/local/lib/docker/cli-plugins/docker-compose
COPY --from=docker-cli --chmod=0755 /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli --chmod=0755 /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/lib/docker/cli-plugins/docker-buildx
COPY --from=docker-cli --chmod=0755 /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
COPY --from=docker-cli --chmod=0755 /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose
COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke
RUN docker --version | grep -F '29.7.2' && \
    docker buildx version | grep -F 'v0.36.1' && \
    docker compose version | grep -F 'v5.4.0' && \
    docker-compose version | grep -F 'v5.4.0' && \
    test ! -e /usr/bin/dockerd && \
    test ! -e /usr/local/bin/dockerd
USER runner
