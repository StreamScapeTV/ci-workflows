# Build-only stage for common Debian utilities and the zstd binary. Every
# network asset is materialized and checksum-verified by the central OCI input
# contract before Buildah starts; Dockerfile execution itself is networkless.
FROM docker.io/library/buildpack-deps:trixie@sha256:3c55df66bea98808f842d6d59aa498012888f4cf82874c4a7f2b3a42a2c9c82e AS build-tools

COPY .ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz /tmp/runner.tar.gz
COPY .ciw-build-inputs/zstd-1.5.7.tar.gz /tmp/zstd.tar.gz
RUN set -eux; \
    mkdir -p /tmp/runner /tmp/zstd /out/usr/local/bin; \
    tar -xzf /tmp/runner.tar.gz -C /tmp/runner; \
    tar -xzf /tmp/zstd.tar.gz -C /tmp/zstd --strip-components=1; \
    make -C /tmp/zstd -j2 zstd; \
    install -m 0755 /tmp/zstd/programs/zstd /out/usr/local/bin/zstd; \
    for path in /bin/bash /usr/bin/curl /usr/bin/git /usr/bin/gzip /usr/bin/tar /usr/bin/unzip; do \
      cp --parents -a "${path}" /out; \
    done; \
    cp -a /usr/lib/git-core /out/usr/lib/; \
    cp -a /usr/share/git-core /out/usr/share/; \
    { \
      for executable in \
        /bin/bash /usr/bin/curl /usr/bin/git /usr/bin/gzip /usr/bin/tar /usr/bin/unzip \
        /tmp/runner/bin/Runner.Listener /tmp/runner/bin/Runner.Worker /tmp/runner/bin/Runner.PluginHost; do \
        ldd "${executable}" 2>/dev/null || true; \
      done; \
      find /usr/lib/git-core -type f -perm /111 -print0 \
        | xargs -0 -r -n1 sh -c 'ldd "$1" 2>/dev/null || true' sh; \
    } | awk '/=> \/.+ \(/ {print $(NF-1)} /^\/.+ \(/ {print $1}' \
      | sort -u \
      | while read -r library; do cp --parents -a "${library}" /out; done; \
    for pattern in \
      '/usr/lib/x86_64-linux-gnu/libicu*.so.*' \
      '/usr/lib/x86_64-linux-gnu/libkrb5*.so.*' \
      '/usr/lib/x86_64-linux-gnu/libgssapi_krb5.so.*' \
      '/usr/lib/x86_64-linux-gnu/libk5crypto.so.*' \
      '/usr/lib/x86_64-linux-gnu/libcom_err.so.*' \
      '/usr/lib/x86_64-linux-gnu/libkeyutils.so.*' \
      '/usr/lib/x86_64-linux-gnu/libssl.so.*' \
      '/usr/lib/x86_64-linux-gnu/libcrypto.so.*' \
      '/usr/lib/x86_64-linux-gnu/libz.so.*'; do \
      for library in ${pattern}; do \
        test ! -e "${library}" || cp --parents -a "${library}" /out; \
      done; \
    done; \
    rm -rf /tmp/runner /tmp/zstd /tmp/runner.tar.gz /tmp/zstd.tar.gz

FROM docker.io/library/node:24.19.0-trixie-slim@sha256:0711b541c1c33a8a530ac4f0d391baa9a15b3d804695b1b24a47daa5fb60e74d AS node-runtime
RUN set -eux; \
    mkdir -p /node-root/usr; \
    cp -a /usr/local /node-root/usr/; \
    ldd /usr/local/bin/node \
      | awk '/=> \/.+ \(/ {print $(NF-1)} /^\/.+ \(/ {print $1}' \
      | sort -u \
      | while read -r library; do cp --parents -a "${library}" /node-root; done; \
    for library in /usr/lib/x86_64-linux-gnu/libatomic.so.1*; do \
      cp --parents -a "${library}" /node-root; \
    done

# Final userspace: official CPython 3.12 on Debian Trixie slim. No Ubuntu or
# another StreamScapeTV runner image participates in the final filesystem.
FROM docker.io/library/python:3.12.14-slim-trixie@sha256:0bdd92d1210a488ce24366c2e1d3b2390e923dcd70a0ce2f673b6f1ce090f556 AS final

ENV PATH=/usr/local/bin:/home/runner/bin:${PATH} \
    RUNNER_MANUALLY_TRAP_SIG=1 \
    ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1 \
    CI=true

COPY --from=build-tools /out/ /
COPY --from=node-runtime /node-root/ /

COPY .ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz /tmp/runner.tar.gz
COPY --chmod=0755 .ciw-build-inputs/jq-1.8.2-linux-amd64 /usr/local/bin/jq
COPY --chmod=0755 .ciw-build-inputs/yq-4.53.3-linux-amd64 /usr/local/bin/yq
COPY --chmod=0755 .ciw-build-inputs/kubectl-1.36.2-linux-amd64 /usr/local/bin/kubectl
COPY .ciw-build-inputs/helm-4.2.4-linux-amd64.tar.gz /tmp/helm.tar.gz
COPY .ciw-build-inputs/kustomize-5.8.1-linux-amd64.tar.gz /tmp/kustomize.tar.gz
COPY --chmod=0755 zip.py /usr/local/bin/zip
COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

RUN set -eux; \
    test -z "$(getent passwd 1001 || true)"; \
    test -z "$(getent group 1001 || true)"; \
    printf '%s\n' 'runner:x:1001:1001:GitHub Actions Runner:/home/runner:/bin/bash' >> /etc/passwd; \
    printf '%s\n' 'runner:x:1001:' >> /etc/group; \
    mkdir -p /home/runner /home/runner/_work; \
    tar -xzf /tmp/runner.tar.gz -C /home/runner; \
    tar -xzf /tmp/helm.tar.gz -C /tmp; \
    install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm; \
    tar -xzf /tmp/kustomize.tar.gz -C /usr/local/bin kustomize; \
    chown -R 1001:1001 /home/runner; \
    rm -rf /tmp/runner.tar.gz /tmp/helm.tar.gz /tmp/kustomize.tar.gz /tmp/linux-amd64; \
    rm -f \
      /usr/bin/apt /usr/bin/apt-cache /usr/bin/apt-cdrom /usr/bin/apt-config \
      /usr/bin/apt-get /usr/bin/apt-mark /usr/bin/dpkg /usr/bin/dpkg-deb \
      /usr/bin/dpkg-query; \
    for forbidden in docker dockerd containerd ctr runc buildah podman skopeo sudo; do \
      ! command -v "${forbidden}"; \
    done

USER 1001:1001
WORKDIR /home/runner
RUN /usr/local/bin/runner-image-smoke
