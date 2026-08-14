# syntax=docker/dockerfile:1.7

# Independent daemonless OCI runner. The final image is based directly on the
# official GitHub Actions runner and owns its Buildah/Skopeo/Podman versions.
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner

USER root

ARG BUILDAH_PACKAGE_VERSION=1.33.7+ds1-1ubuntu0.24.04.3
ARG SKOPEO_PACKAGE_VERSION=1.13.3+ds1-2ubuntu0.24.04.3
ARG PODMAN_PACKAGE_VERSION=4.9.3+ds1-1ubuntu0.2
ARG PODMAN_COMPOSE_PACKAGE_VERSION=1.0.6-1

ENV DEBIAN_FRONTEND=noninteractive \
    BUILDAH_ISOLATION=chroot \
    STORAGE_DRIVER=vfs \
    XDG_RUNTIME_DIR=/home/runner/.local/run \
    REGISTRY_AUTH_FILE=/home/runner/.config/containers/auth.json \
    CI=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash \
      binfmt-support \
      build-essential \
      buildah="${BUILDAH_PACKAGE_VERSION}" \
      ca-certificates \
      crun \
      curl \
      fuse-overlayfs \
      git \
      gzip \
      jq \
      make \
      podman="${PODMAN_PACKAGE_VERSION}" \
      podman-compose="${PODMAN_COMPOSE_PACKAGE_VERSION}" \
      python3 \
      python3-yaml \
      qemu-user-static \
      skopeo="${SKOPEO_PACKAGE_VERSION}" \
      slirp4netns \
      tar \
      uidmap \
      unzip \
      util-linux \
      xz-utils \
      zip \
    && apt-mark hold buildah skopeo podman podman-compose \
    && rm -rf /var/lib/apt/lists/*

# The official Actions runner image currently includes Docker's static runtime
# bundle. This product is deliberately daemonless: retain OCI client/runtime
# tools required by Buildah/Podman, but remove Docker and containerd daemons and
# Docker CLI plugins inherited from the base.
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
    && test ! -e /var/run/docker.sock \
    && test ! -e /run/docker.sock

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
      '[containers]' \
      'netns = "host"' \
      > /home/runner/.config/containers/containers.conf \
    && chown -R runner:runner /home/runner/.config /home/runner/.local /home/runner/_work

COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

USER runner
WORKDIR /home/runner

RUN /usr/local/bin/runner-image-smoke
