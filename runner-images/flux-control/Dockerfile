# Protected Flux-control Actions runner. Client tooling only; cluster authority is injected at runtime by Flux.
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner

USER root
RUN rm -f /usr/bin/containerd /usr/bin/containerd-shim-runc-v2 /usr/bin/ctr /usr/bin/docker-init /usr/bin/docker-proxy /usr/bin/runc
RUN rm -f /usr/bin/docker /usr/local/bin/docker /usr/bin/dockerd /usr/local/bin/dockerd /usr/local/lib/docker/cli-plugins/docker-buildx /usr/local/lib/docker/cli-plugins/docker-compose

COPY .ciw-build-inputs/flux-2.9.4-linux-amd64.tar.gz /tmp/flux.tar.gz
COPY --chmod=0755 .ciw-build-inputs/kubectl-1.34.8-linux-amd64 /usr/local/bin/kubectl
COPY --chmod=0755 .ciw-build-inputs/yq-4.53.3-linux-amd64 /usr/local/bin/yq
COPY .ciw-build-inputs/helm-4.2.4-linux-amd64.tar.gz /tmp/helm.tar.gz
COPY .ciw-build-inputs/kustomize-5.8.1-linux-amd64.tar.gz /tmp/kustomize.tar.gz

RUN mkdir -p /tmp/flux-root /tmp/helm-root /tmp/kustomize-root && \
    tar -xzf /tmp/flux.tar.gz -C /tmp/flux-root && \
    install -m 0755 /tmp/flux-root/flux /usr/local/bin/flux && \
    tar -xzf /tmp/helm.tar.gz -C /tmp/helm-root && \
    install -m 0755 /tmp/helm-root/linux-amd64/helm /usr/local/bin/helm && \
    tar -xzf /tmp/kustomize.tar.gz -C /tmp/kustomize-root && \
    install -m 0755 /tmp/kustomize-root/kustomize /usr/local/bin/kustomize && \
    rm -rf /tmp/flux.tar.gz /tmp/helm.tar.gz /tmp/kustomize.tar.gz /tmp/flux-root /tmp/helm-root /tmp/kustomize-root

COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

RUN test ! -e /root/.kube/config && \
    test ! -e /home/runner/.kube/config && \
    test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token && \
    ! command -v docker && ! command -v dockerd && \
    ! command -v buildah && ! command -v podman && ! command -v skopeo

USER runner
RUN /usr/local/bin/runner-image-smoke
