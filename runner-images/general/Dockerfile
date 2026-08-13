# General self-hosted runner image. Every mutable source is resolved by the OCI input lock.
FROM docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS python-runtime
FROM docker.io/library/node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node-runtime
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner

RUN sudo rm -f /usr/bin/containerd /usr/bin/containerd-shim-runc-v2 /usr/bin/ctr /usr/bin/docker-init /usr/bin/docker-proxy /usr/bin/runc
RUN sudo rm -f /usr/bin/docker /usr/local/bin/docker /usr/bin/dockerd /usr/local/bin/dockerd /usr/local/lib/docker/cli-plugins/docker-buildx /usr/local/lib/docker/cli-plugins/docker-compose
COPY --from=python-runtime /usr/local/ /usr/local/
COPY --from=node-runtime /usr/local/ /usr/local/

COPY .ciw-build-inputs/zstd-noble-amd64.pkg /tmp/zstd.pkg
COPY .ciw-build-inputs/zip-noble-amd64.pkg /tmp/zip.pkg
RUN mkdir -p /tmp/zstd-root /tmp/zip-root && \
    dpkg-deb -x /tmp/zstd.pkg /tmp/zstd-root && \
    dpkg-deb -x /tmp/zip.pkg /tmp/zip-root && \
    sudo install -m 0755 /tmp/zstd-root/usr/bin/zstd /usr/local/bin/zstd && \
    sudo install -m 0755 /tmp/zip-root/usr/bin/zip /usr/local/bin/zip && \
    rm -rf /tmp/zstd.pkg /tmp/zip.pkg /tmp/zstd-root /tmp/zip-root

COPY --chmod=0755 .ciw-build-inputs/yq-4.53.3-linux-amd64 /usr/local/bin/yq
COPY --chmod=0755 .ciw-build-inputs/kubectl-1.36.3-linux-amd64 /usr/local/bin/kubectl
COPY .ciw-build-inputs/helm-4.2.4-linux-amd64.tar.gz /tmp/helm.tar.gz
COPY .ciw-build-inputs/kustomize-5.8.1-linux-amd64.tar.gz /tmp/kustomize.tar.gz
RUN tar -xzf /tmp/helm.tar.gz -C /tmp && \
    sudo install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm && \
    sudo tar -xzf /tmp/kustomize.tar.gz -C /usr/local/bin kustomize && \
    rm -rf /tmp/helm.tar.gz /tmp/kustomize.tar.gz /tmp/linux-amd64

COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke
RUN python3 --version | grep -F 'Python 3.12.13' && \
    node --version | grep -F 'v24.18.0' && \
    command -v npm corepack git bash curl jq yq tar zstd gzip unzip zip helm kustomize kubectl >/dev/null && \
    ! command -v docker && ! command -v dockerd && \
    ! command -v buildah && ! command -v podman && ! command -v skopeo

USER runner
RUN /usr/local/bin/runner-image-smoke
