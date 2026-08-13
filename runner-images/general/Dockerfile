# Immutable general runner image source.
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda
RUN sudo rm -f /usr/bin/docker /usr/local/lib/docker/cli-plugins/docker-buildx
COPY --chmod=0755 .ciw-build-inputs/kubectl-1.36.3-linux-amd64 /usr/local/bin/kubectl
USER runner
