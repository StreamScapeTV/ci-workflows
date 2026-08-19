# Mobile Android/Flutter runner image. Base image digests are retained as build
# hygiene, while tool downloads are ordinary HTTPS inputs verified by the small
# checked-in toolchain lock rather than the retired central input/provenance path.
FROM docker.io/library/gcc:15-trixie@sha256:486d5379b2d679f18bca957901d628952aa0ebc2d82817047bbef1dbfbc13d70 AS build-tools

ARG ACTIONS_RUNNER_URL=https://github.com/actions/runner/releases/download/v2.336.0/actions-runner-linux-x64-2.336.0.tar.gz
ARG ACTIONS_RUNNER_SHA256=04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d

RUN set -eux; \
    curl --fail --location --http1.1 --retry 5 --retry-all-errors --retry-delay 1 \
      --proto '=https' --tlsv1.2 "${ACTIONS_RUNNER_URL}" -o /tmp/runner.tar.gz; \
    printf '%s  %s\n' "${ACTIONS_RUNNER_SHA256}" /tmp/runner.tar.gz | sha256sum -c -; \
    mkdir -p /tmp/runner /out /out/tmp; \
    tar -xzf /tmp/runner.tar.gz -C /tmp/runner; \
    cp /tmp/runner.tar.gz /out/tmp/runner.tar.gz; \
    for path in /usr/bin/bash /usr/bin/curl /usr/bin/git /usr/bin/gzip /usr/bin/sha256sum /usr/bin/tar; do \
      cp --parents -a "${path}" /out; \
    done; \
    cp --parents -a /usr/lib/git-core /out; \
    cp --parents -a /usr/share/git-core /out; \
    { \
      for executable in \
        /usr/bin/bash /usr/bin/curl /usr/bin/git /usr/bin/gzip /usr/bin/sha256sum /usr/bin/tar \
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
    for soname in libstdc++.so.6 libgcc_s.so.1; do \
      runtime_library="$(ldconfig -p | awk -v name="${soname}" '$1 == name {print $NF; exit}')"; \
      test -n "${runtime_library}"; \
      runtime_real="$(readlink -f "${runtime_library}")"; \
      test -f "${runtime_real}"; \
      cp -pL "${runtime_real}" "/out/usr/lib/x86_64-linux-gnu/${soname}"; \
    done; \
    test -e /out/usr/lib/x86_64-linux-gnu/libatomic.so.1; \
    test -e /out/usr/lib/x86_64-linux-gnu/libstdc++.so.6; \
    test -e /out/usr/lib/x86_64-linux-gnu/libgcc_s.so.1; \
    rm -rf /tmp/runner

FROM docker.io/library/eclipse-temurin:25-jdk-noble@sha256:3eb81ed94d8c1a34422f19f8188548bdf02cae69c91d0328afdbb7abed90f617 AS java-runtime

FROM docker.io/library/node:24.19.0-trixie-slim@sha256:0711b541c1c33a8a530ac4f0d391baa9a15b3d804695b1b24a47daa5fb60e74d AS node-runtime
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

FROM docker.io/library/python:3.12.14-slim-trixie@sha256:0bdd92d1210a488ce24366c2e1d3b2390e923dcd70a0ce2f673b6f1ce090f556 AS final

ARG FLUTTER_VERSION=3.44.8
ARG DART_VERSION=3.12.2
ARG ANDROID_CMDLINE_TOOLS_VERSION=15859902
ARG ANDROID_COMPAT_PLATFORM_VERSION=36
ARG ANDROID_PLATFORM_VERSION=37
ARG ANDROID_PLATFORM_PACKAGE_VERSION=37.0
ARG ANDROID_COMPAT_BUILD_TOOLS_VERSION=36.0.0
ARG ANDROID_BUILD_TOOLS_VERSION=37.0.0
ARG ANDROID_NDK_VERSION=28.2.13676358

ARG FLUTTER_URL=https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.44.8-stable.tar.xz
ARG FLUTTER_SHA256=672089e001571a9fbb209a495c583580c0c6c73ef98999264ba07fa93ace332d
ARG ANDROID_CMDLINE_TOOLS_URL=https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip
ARG ANDROID_CMDLINE_TOOLS_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
ARG ANDROID_PLATFORM_TOOLS_URL=https://dl.google.com/android/repository/platform-tools_r36.0.0-linux.zip
ARG ANDROID_PLATFORM_TOOLS_SHA256=0ead642c943ffe79701fccca8f5f1c69c4ce4f43df2eefee553f6ccb27cbfbe8
ARG ANDROID_PLATFORM_36_URL=https://dl.google.com/android/repository/platform-36_r02.zip
ARG ANDROID_PLATFORM_36_SHA256=37607369a28c5b640b3a7998868d45898ebcb777565a0e85f9acf36f29631d2e
ARG ANDROID_PLATFORM_37_URL=https://dl.google.com/android/repository/platform-37.0_r01.zip
ARG ANDROID_PLATFORM_37_SHA256=19bdcf42de0cb0e9500a27da6833fc30cdd49a7ec690aa5cabaa0ef893af9ebe
ARG ANDROID_BUILD_TOOLS_36_URL=https://dl.google.com/android/repository/build-tools_r36_linux.zip
ARG ANDROID_BUILD_TOOLS_36_SHA256=5d9ac77fb6ff43d9da518a337b4fcf8f9097113df531d99ccefe80ef7ce8250b
ARG ANDROID_BUILD_TOOLS_37_URL=https://dl.google.com/android/repository/build-tools_r37_linux.zip
ARG ANDROID_BUILD_TOOLS_37_SHA256=01af179347cbcd9c208b7f8171f7b21f6dd1d2f85bcd15e88caa51d5d7b86060
ARG ANDROID_NDK_URL=https://dl.google.com/android/repository/android-ndk-r28c-linux.zip
ARG ANDROID_NDK_SHA256=dfb20d396df28ca02a8c708314b814a4d961dc9074f9a161932746f815aa552f

ENV JAVA_HOME=/opt/java/openjdk \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    FLUTTER_HOME=/opt/flutter \
    FLUTTER_SUPPRESS_ANALYTICS=true \
    PUB_ENVIRONMENT=github_actions:streamscapetv_homelab \
    RUNNER_MANUALLY_TRAP_SIG=1 \
    ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1 \
    CI=true \
    PATH=/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/opt/java/openjdk/bin:/usr/local/bin:/home/runner/bin:${PATH}

COPY --from=build-tools /out/ /
COPY --from=build-tools /etc/ssl/certs /etc/ssl/certs
COPY --from=java-runtime /opt/java/openjdk /opt/java/openjdk
COPY --from=node-runtime /node-root/ /
COPY --chmod=0755 assemble.py /usr/local/bin/runner-mobile-assemble
COPY --chmod=0755 zip.py /usr/local/bin/zip
COPY --chmod=0755 unzip.py /usr/local/bin/unzip
COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

RUN set -eux; \
    download() { \
      url="$1"; sha="$2"; destination="$3"; \
      curl --fail --location --http1.1 --retry 5 --retry-all-errors --retry-delay 1 \
        --proto '=https' --tlsv1.2 "${url}" -o "${destination}"; \
      printf '%s  %s\n' "${sha}" "${destination}" | sha256sum -c -; \
    }; \
    download "${FLUTTER_URL}" "${FLUTTER_SHA256}" /tmp/flutter.tar.xz; \
    download "${ANDROID_CMDLINE_TOOLS_URL}" "${ANDROID_CMDLINE_TOOLS_SHA256}" /tmp/android-command-line-tools.zip; \
    download "${ANDROID_PLATFORM_TOOLS_URL}" "${ANDROID_PLATFORM_TOOLS_SHA256}" /tmp/android-platform-tools.zip; \
    download "${ANDROID_PLATFORM_36_URL}" "${ANDROID_PLATFORM_36_SHA256}" /tmp/android-platform-36.zip; \
    download "${ANDROID_PLATFORM_37_URL}" "${ANDROID_PLATFORM_37_SHA256}" /tmp/android-platform-37.zip; \
    download "${ANDROID_BUILD_TOOLS_36_URL}" "${ANDROID_BUILD_TOOLS_36_SHA256}" /tmp/android-build-tools-36.zip; \
    download "${ANDROID_BUILD_TOOLS_37_URL}" "${ANDROID_BUILD_TOOLS_37_SHA256}" /tmp/android-build-tools-37.zip; \
    download "${ANDROID_NDK_URL}" "${ANDROID_NDK_SHA256}" /tmp/android-ndk.zip; \
    test -z "$(getent passwd 1001 || true)"; \
    test -z "$(getent group 1001 || true)"; \
    printf '%s\n' 'runner:x:1001:1001:GitHub Actions Runner:/home/runner:/usr/bin/bash' >> /etc/passwd; \
    printf '%s\n' 'runner:x:1001:' >> /etc/group; \
    mkdir -p /home/runner /home/runner/_work /opt/android-sdk /opt/runner-cache; \
    tar -xzf /tmp/runner.tar.gz -C /home/runner; \
    /usr/local/bin/runner-mobile-assemble; \
    ln -sfn "android-${ANDROID_PLATFORM_PACKAGE_VERSION}" \
      "${ANDROID_HOME}/platforms/android-${ANDROID_PLATFORM_VERSION}"; \
    git config --system --add safe.directory /opt/flutter; \
    chown -R 1001:1001 /home/runner /opt/android-sdk /opt/flutter /opt/runner-cache; \
    rm -rf /tmp/runner.tar.gz /tmp/flutter.tar.xz /tmp/android-*.zip \
      /tmp/cmdline /tmp/platform-tools /tmp/platform-* /tmp/build-tools-* /tmp/ndk; \
    rm -f /usr/local/bin/runner-mobile-assemble; \
    rm -f \
      /usr/bin/apt /usr/bin/apt-cache /usr/bin/apt-cdrom /usr/bin/apt-config \
      /usr/bin/apt-get /usr/bin/apt-mark /usr/bin/dpkg /usr/bin/dpkg-deb \
      /usr/bin/dpkg-query; \
    for forbidden in docker dockerd containerd ctr runc buildah podman skopeo sudo; do \
      ! command -v "${forbidden}"; \
    done; \
    test ! -e /var/run/docker.sock; \
    test ! -e /run/docker.sock

USER 1001:1001
WORKDIR /home/runner
RUN /usr/local/bin/runner-image-smoke

USER root
RUN rm -rf \
      /home/runner/.android /home/runner/.cache /home/runner/.dart \
      /home/runner/.gradle /home/runner/.pub-cache; \
    chown -R 1001:1001 /home/runner

USER 1001:1001
CMD []
