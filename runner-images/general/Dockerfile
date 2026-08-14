# General self-hosted runner image. Every mutable source is resolved by the OCI input lock.
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner

USER root

# The upstream Actions runner image includes Docker's static runtime bundle.
# General runners are intentionally container-engine-free.
RUN rm -f \
      /usr/bin/containerd \
      /usr/bin/containerd-shim-runc-v2 \
      /usr/bin/ctr \
      /usr/bin/docker \
      /usr/bin/dockerd \
      /usr/bin/docker-init \
      /usr/bin/docker-proxy \
      /usr/bin/runc \
      /usr/local/bin/docker \
      /usr/local/bin/dockerd \
      /usr/local/lib/docker/cli-plugins/docker-buildx \
      /usr/local/lib/docker/cli-plugins/docker-compose

# CPython and Node are materialized by the central OCI input contract before
# buildah bud starts. Dockerfile execution itself remains --network none.
COPY .ciw-build-inputs/cpython-3.12.13-linux-amd64.tar.gz /tmp/python.tar.gz
COPY .ciw-build-inputs/node-v24.19.0-linux-x64.tar.gz /tmp/node.tar.gz
RUN mkdir -p /opt/node && \
    tar -xzf /tmp/python.tar.gz -C /opt && \
    tar -xzf /tmp/node.tar.gz -C /opt/node --strip-components=1 && \
    test -x /opt/python/bin/python3.12 && \
    test -x /opt/node/bin/node && \
    ln -sfn /opt/python/bin/python3.12 /usr/local/bin/python3.12 && \
    ln -sfn /opt/python/bin/python3.12 /usr/local/bin/python3 && \
    ln -sfn /opt/node/bin/node /usr/local/bin/node && \
    ln -sfn /opt/node/bin/npm /usr/local/bin/npm && \
    ln -sfn /opt/node/bin/npx /usr/local/bin/npx && \
    ln -sfn /opt/node/bin/corepack /usr/local/bin/corepack && \
    rm -f /tmp/python.tar.gz /tmp/node.tar.gz

COPY .ciw-build-inputs/zstd-noble-amd64.pkg /tmp/zstd.pkg
COPY .ciw-build-inputs/zip-noble-amd64.pkg /tmp/zip.pkg
RUN mkdir -p /tmp/zstd-root /tmp/zip-root && \
    dpkg-deb -x /tmp/zstd.pkg /tmp/zstd-root && \
    dpkg-deb -x /tmp/zip.pkg /tmp/zip-root && \
    install -m 0755 /tmp/zstd-root/usr/bin/zstd /usr/local/bin/zstd && \
    install -m 0755 /tmp/zip-root/usr/bin/zip /usr/local/bin/zip && \
    rm -rf /tmp/zstd.pkg /tmp/zip.pkg /tmp/zstd-root /tmp/zip-root

COPY --chmod=0755 .ciw-build-inputs/yq-4.53.3-linux-amd64 /usr/local/bin/yq
COPY --chmod=0755 .ciw-build-inputs/kubectl-1.36.3-linux-amd64 /usr/local/bin/kubectl
COPY .ciw-build-inputs/helm-4.2.4-linux-amd64.tar.gz /tmp/helm.tar.gz
COPY .ciw-build-inputs/kustomize-5.8.1-linux-amd64.tar.gz /tmp/kustomize.tar.gz
RUN tar -xzf /tmp/helm.tar.gz -C /tmp && \
    install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm && \
    tar -xzf /tmp/kustomize.tar.gz -C /usr/local/bin kustomize && \
    rm -rf /tmp/helm.tar.gz /tmp/kustomize.tar.gz /tmp/linux-amd64

COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke
RUN python3 --version | grep -F 'Python 3.12.13' && \
    node --version | grep -F 'v24.19.0' && \
    command -v npm corepack git bash curl jq yq tar zstd gzip unzip zip helm kustomize kubectl >/dev/null && \
    ! command -v docker && ! command -v dockerd && ! command -v containerd && \
    ! command -v buildah && ! command -v podman && ! command -v skopeo

USER runner
RUN /usr/local/bin/runner-image-smoke
