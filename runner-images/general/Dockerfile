# General self-hosted runner image. Every mutable source is resolved by the OCI input lock.
FROM docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS python-runtime
FROM docker.io/library/node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node-runtime
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda
RUN sudo rm -f /usr/bin/docker /usr/local/lib/docker/cli-plugins/docker-buildx
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
COPY --chmod=0755 .ciw-build-inputs/kubectl-1.36.3-linux-amd64 /usr/local/bin/kubectl
USER runner
