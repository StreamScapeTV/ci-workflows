# General self-hosted runner image. Every mutable source is resolved by the OCI input lock.
FROM docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS python-runtime
FROM docker.io/library/node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node-runtime
FROM docker.io/library/gcc:13.4.0-bookworm@sha256:056fa682471704249f619f65ccec87d671ad5f1b20878da54d60b0b863486621 AS utility-build
COPY .ciw-build-inputs/zstd-1.5.7.tar.gz /tmp/zstd.tar.gz
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda
RUN sudo rm -f /usr/bin/docker /usr/local/lib/docker/cli-plugins/docker-buildx
COPY --from=python-runtime /usr/local/ /usr/local/
COPY --from=node-runtime /usr/local/ /usr/local/
COPY --chmod=0755 .ciw-build-inputs/kubectl-1.36.3-linux-amd64 /usr/local/bin/kubectl
USER runner
