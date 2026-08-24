# syntax=docker/dockerfile:1.7

# PR-safe daemonless service-validation runner. This image intentionally owns
# only the rootless Podman/Compose runtime required by validation.service-compose;
# it does not carry Buildah/Skopeo publication tooling or Docker/DinD.
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner

USER root

ARG PODMAN_PACKAGE_VERSION=4.9.3+ds1-1ubuntu0.2
ARG PODMAN_COMPOSE_PACKAGE_VERSION=1.0.6-1
ARG NETAVARK_PACKAGE_VERSION=1.4.0-4
ARG AARDVARK_DNS_PACKAGE_VERSION=1.4.0-5

ENV DEBIAN_FRONTEND=noninteractive \
    STORAGE_DRIVER=vfs \
    XDG_RUNTIME_DIR=/home/runner/.local/run \
    CI=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      aardvark-dns="${AARDVARK_DNS_PACKAGE_VERSION}" \
      bash \
      ca-certificates \
      crun \
      curl \
      fuse-overlayfs \
      git \
      jq \
      netavark="${NETAVARK_PACKAGE_VERSION}" \
      podman="${PODMAN_PACKAGE_VERSION}" \
      podman-compose="${PODMAN_COMPOSE_PACKAGE_VERSION}" \
      python3 \
      python3-yaml \
      slirp4netns \
      tar \
      uidmap \
      util-linux \
    && apt-mark hold aardvark-dns netavark podman podman-compose \
    && rm -rf /var/lib/apt/lists/*

# The official Actions runner image includes Docker's static runtime bundle.
# Service validation is deliberately daemonless. Remove Docker/containerd and
# their inherited CLI plugins, and fail the image build if any remain usable.
RUN rm -f \
      /usr/bin/docker \
      /usr/local/bin/docker \
      /usr/bin/dockerd \
      /usr/local/bin/dockerd \
      /usr/bin/containerd \
      /usr/bin/containerd-shim-runc-v2 \
      /usr/bin/ctr \
      /usr/bin/docker-init \
      /usr/bin/docker-proxy \
      /usr/local/lib/docker/cli-plugins/docker-buildx \
      /usr/local/lib/docker/cli-plugins/docker-compose \
    && rm -rf \
      /etc/docker \
      /root/.docker \
      /home/runner/.docker \
      /usr/libexec/docker \
      /usr/local/lib/docker \
      /var/lib/docker \
    && ! command -v docker \
    && ! command -v dockerd \
    && ! command -v containerd \
    && ! command -v buildah \
    && ! command -v skopeo \
    && test ! -e /var/run/docker.sock \
    && test ! -e /run/docker.sock

# Rootless Podman owns subordinate IDs and writable state as the runner user.
# VFS avoids requiring privileged overlay mounts in the ordinary ARC pod.
RUN sed -i '/^runner:/d' /etc/subuid /etc/subgid \
    && printf '%s\n' 'runner:100000:65536' >> /etc/subuid \
    && printf '%s\n' 'runner:100000:65536' >> /etc/subgid \
    && test "$(grep -Fc 'runner:100000:65536' /etc/subuid)" -eq 1 \
    && test "$(grep -Fc 'runner:100000:65536' /etc/subgid)" -eq 1 \
    && install -d -m 0700 -o runner -g runner \
      /home/runner/.config/containers \
      /home/runner/.local/run \
      /home/runner/.local/share/containers/runroot \
      /home/runner/.local/share/containers/storage \
      /home/runner/_work

RUN printf '%s\n' \
      '[storage]' \
      'driver = "vfs"' \
      'runroot = "/home/runner/.local/share/containers/runroot"' \
      'graphroot = "/home/runner/.local/share/containers/storage"' \
      '' \
      '[storage.options]' \
      'additionalimagestores = []' \
      > /home/runner/.config/containers/storage.conf \
    && printf '%s\n' \
      '[engine]' \
      'cgroup_manager = "cgroupfs"' \
      'events_logger = "file"' \
      'runtime = "crun"' \
      '' \
      '[network]' \
      'network_backend = "netavark"' \
      > /home/runner/.config/containers/containers.conf \
    && chown -R runner:runner /home/runner/.config /home/runner/.local /home/runner/_work

COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

USER runner
WORKDIR /home/runner

# Image construction proves the immutable package/configuration contract. The
# shared runner-image action executes the same smoke without this flag from the
# finished image, where it additionally proves live rootless networking/DNS.
RUN CIW_RUNNER_IMAGE_BUILD_PHASE=1 /usr/local/bin/runner-image-smoke
