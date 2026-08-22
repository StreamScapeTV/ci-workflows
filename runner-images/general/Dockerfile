# Build-only stage for common Debian utilities, the bounded native toolchain,
# the setup-node compatibility probe, and zstd. Every network asset is
# materialized and checksum-verified by the product-local input preparer before
# Buildah starts; Dockerfile execution itself is networkless.
FROM docker.io/library/buildpack-deps@sha256:3c55df66bea98808f842d6d59aa498012888f4cf82874c4a7f2b3a42a2c9c82e AS build-tools

COPY .ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz /tmp/runner.tar.gz
COPY .ciw-build-inputs/node-26.7.0-linux-x64.tar.gz /tmp/node26.tar.gz
COPY .ciw-build-inputs/zstd-1.5.7.tar.gz /tmp/zstd.tar.gz
COPY .ciw-build-inputs/cmake-4.4.2-linux-x86_64.tar.gz /tmp/cmake.tar.gz
RUN set -eux; \
    mkdir -p /tmp/runner /tmp/zstd /node26-compat /out/usr/local/bin \
      /native-root/opt/cmake /native-root/usr/local/bin; \
    tar -xzf /tmp/runner.tar.gz -C /tmp/runner; \
    tar -xzf /tmp/node26.tar.gz -C /node26-compat --strip-components=1; \
    tar -xzf /tmp/zstd.tar.gz -C /tmp/zstd --strip-components=1; \
    tar -xzf /tmp/cmake.tar.gz -C /native-root/opt/cmake --strip-components=1; \
    test -x /node26-compat/bin/node; \
    test -f /node26-compat/lib/node_modules/npm/bin/npm-cli.js; \
    /native-root/opt/cmake/bin/cmake --version | head -n1 | grep -Fx 'cmake version 4.4.2'; \
    test "$(gcc -dumpfullversion)" = '14.2.0'; \
    test "$(g++ -dumpfullversion)" = '14.2.0'; \
    make --version | head -n1 | grep -Fx 'GNU Make 4.4.1'; \
    make -C /tmp/zstd -j2 zstd; \
    install -m 0755 /tmp/zstd/programs/zstd /out/usr/local/bin/zstd; \
    for tool in cmake ctest cpack; do \
      ln -s "/opt/cmake/bin/${tool}" "/native-root/usr/local/bin/${tool}"; \
    done; \
    native_packages='binutils binutils-common binutils-x86-64-linux-gnu cpp cpp-14 cpp-x86-64-linux-gnu cpp-14-x86-64-linux-gnu g++ g++-14 g++-x86-64-linux-gnu g++-14-x86-64-linux-gnu gcc gcc-14 gcc-x86-64-linux-gnu gcc-14-x86-64-linux-gnu libc6-dev libgcc-14-dev libgcc-s1 libstdc++-14-dev libstdc++6 linux-libc-dev make'; \
    for package in ${native_packages}; do \
      dpkg-query -W -f='${db:Status-Abbrev}\n' "${package}" | grep -Fx 'ii '; \
      dpkg-query -L "${package}"; \
    done | sort -u | while IFS= read -r path; do \
      case "${path}" in /usr/share/*) continue ;; esac; \
      if test -f "${path}" || test -L "${path}"; then \
        mkdir -p "/native-root$(dirname "${path}")"; \
        cp -a "${path}" "/native-root${path}"; \
      fi; \
    done; \
    { \
      for executable in \
        /usr/bin/gcc /usr/bin/g++ /usr/bin/cpp /usr/bin/make \
        /usr/bin/x86_64-linux-gnu-as /usr/bin/x86_64-linux-gnu-ld \
        /native-root/opt/cmake/bin/cmake /native-root/opt/cmake/bin/ctest; do \
        ldd "${executable}" 2>/dev/null || true; \
      done; \
      find /usr/lib/gcc/x86_64-linux-gnu/14 -type f -perm /111 -print0 \
        | xargs -0 -r -n1 sh -c 'ldd "$1" 2>/dev/null || true' sh; \
    } | awk '/=> \/.+ \(/ {print $(NF-1)} /^\/.+ \(/ {print $1}' \
      | sort -u \
      | while IFS= read -r library; do \
          library_dir="$(readlink -f "$(dirname "${library}")")"; \
          test -d "${library_dir}"; \
          mkdir -p "/native-root${library_dir}"; \
          cp -pL "${library}" "/native-root${library_dir}/$(basename "${library}")"; \
        done; \
    for path in /usr/bin/bash /usr/bin/curl /usr/bin/git /usr/bin/gzip /usr/bin/tar; do \
      cp --parents -a "${path}" /out; \
    done; \
    cp --parents -a /usr/lib/git-core /out; \
    cp --parents -a /usr/share/git-core /out; \
    { \
      for executable in \
        /usr/bin/bash /usr/bin/curl /usr/bin/git /usr/bin/gzip /usr/bin/tar \
        /tmp/runner/bin/Runner.Listener /tmp/runner/bin/Runner.Worker /tmp/runner/bin/Runner.PluginHost; do \
        ldd "${executable}" 2>/dev/null || true; \
      done; \
      find /usr/lib/git-core -type f -perm /111 -print0 \
        | xargs -0 -r -n1 sh -c 'ldd "$1" 2>/dev/null || true' sh; \
    } | awk '/=> \/.+ \(/ {print $(NF-1)} /^\/.+ \(/ {print $1}' \
      | sort -u \
      | while read -r library; do \
          library_dir="$(readlink -f "$(dirname "${library}")")"; \
          test -d "${library_dir}"; \
          mkdir -p "/out${library_dir}"; \
          cp -pL "${library}" "/out${library_dir}/$(basename "${library}")"; \
        done; \
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
    atomic_library="$(ldconfig -p | awk '$1 == "libatomic.so.1" {print $NF; exit}')"; \
    test -n "${atomic_library}"; \
    atomic_real="$(readlink -f "${atomic_library}")"; \
    test -f "${atomic_real}"; \
    mkdir -p /out/usr/lib/x86_64-linux-gnu; \
    cp -pL "${atomic_real}" "/out/usr/lib/x86_64-linux-gnu/$(basename "${atomic_real}")"; \
    ln -s "$(basename "${atomic_real}")" /out/usr/lib/x86_64-linux-gnu/libatomic.so.1; \
    test -e /out/usr/lib/x86_64-linux-gnu/libatomic.so.1; \
    rm -rf /tmp/runner /tmp/zstd /tmp/runner.tar.gz /tmp/node26.tar.gz /tmp/zstd.tar.gz /tmp/cmake.tar.gz

FROM docker.io/library/node@sha256:0711b541c1c33a8a530ac4f0d391baa9a15b3d804695b1b24a47daa5fb60e74d AS node-runtime
RUN set -eux; \
    mkdir -p /node-root/usr; \
    cp -a /usr/local /node-root/usr/; \
    ldd /usr/local/bin/node \
      | awk '/=> \/.+ \(/ {print $(NF-1)} /^\/.+ \(/ {print $1}' \
      | sort -u \
      | while read -r library; do \
          library_dir="$(readlink -f "$(dirname "${library}")")"; \
          test -d "${library_dir}"; \
          mkdir -p "/node-root${library_dir}"; \
          cp -pL "${library}" "/node-root${library_dir}/$(basename "${library}")"; \
        done

# Final userspace: official CPython 3.12 on Debian Trixie slim. No Ubuntu or
# another StreamScapeTV runner image participates in the final filesystem.
FROM docker.io/library/python@sha256:0bdd92d1210a488ce24366c2e1d3b2390e923dcd70a0ce2f673b6f1ce090f556 AS final

ENV PATH=/usr/local/bin:/home/runner/bin:${PATH} \
    RUNNER_MANUALLY_TRAP_SIG=1 \
    ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1 \
    CI=true

COPY --from=build-tools /out/ /
COPY --from=node-runtime /node-root/ /
COPY --from=build-tools /native-root/ /

COPY .ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz /tmp/runner.tar.gz
COPY --chmod=0755 .ciw-build-inputs/jq-1.8.2-linux-amd64 /usr/local/bin/jq
COPY --chmod=0755 .ciw-build-inputs/yq-4.53.3-linux-amd64 /usr/local/bin/yq
COPY --chmod=0755 .ciw-build-inputs/kubectl-1.36.2-linux-amd64 /usr/local/bin/kubectl
COPY .ciw-build-inputs/helm-4.2.4-linux-amd64.tar.gz /tmp/helm.tar.gz
COPY .ciw-build-inputs/kustomize-5.8.1-linux-amd64.tar.gz /tmp/kustomize.tar.gz
COPY --chmod=0755 zip.py /usr/local/bin/zip
COPY --chmod=0755 unzip.py /usr/local/bin/unzip
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
RUN --mount=type=bind,from=build-tools,source=/node26-compat,target=/tmp/node26-compat,ro set -eux; \
    /tmp/node26-compat/bin/node --version | grep -Fx 'v26.7.0'; \
    /tmp/node26-compat/bin/node /tmp/node26-compat/lib/node_modules/npm/bin/npm-cli.js --version >/dev/null
RUN /usr/local/bin/runner-image-smoke

USER root
RUN rm -rf /home/runner/.npm /home/runner/.cache; \
    chown -R 1001:1001 /home/runner

USER 1001:1001
CMD []
