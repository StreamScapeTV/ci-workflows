# All network assets are acquired and verified before Buildah starts. Every OCI
# stage is Docker Hub so the central resolver can use one bounded registry API
# profile; the official Actions runner distribution is a checksum-locked input.
FROM docker.io/library/buildpack-deps@sha256:3c55df66bea98808f842d6d59aa498012888f4cf82874c4a7f2b3a42a2c9c82e AS build-tools

COPY .ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz /tmp/runner.tar.gz
RUN set -eux; \
    mkdir -p /tmp/runner /out; \
    tar -xzf /tmp/runner.tar.gz -C /tmp/runner; \
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
    rm -rf /tmp/runner /tmp/runner.tar.gz

FROM docker.io/library/eclipse-temurin@sha256:3eb81ed94d8c1a34422f19f8188548bdf02cae69c91d0328afdbb7abed90f617 AS java-runtime

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
        done; \
    atomic_library="$(ldconfig -p | awk '$1 == "libatomic.so.1" {print $NF; exit}')"; \
    test -n "${atomic_library}"; \
    atomic_real="$(readlink -f "${atomic_library}")"; \
    test -f "${atomic_real}"; \
    mkdir -p /node-root/usr/lib/x86_64-linux-gnu; \
    cp -a "${atomic_real}" /node-root/usr/lib/x86_64-linux-gnu/; \
    ln -s "$(basename "${atomic_real}")" /node-root/usr/lib/x86_64-linux-gnu/libatomic.so.1

FROM docker.io/library/python@sha256:0bdd92d1210a488ce24366c2e1d3b2390e923dcd70a0ce2f673b6f1ce090f556 AS final

ARG FLUTTER_VERSION=3.44.8
ARG DART_VERSION=3.12.2
ARG ANDROID_CMDLINE_TOOLS_VERSION=15859902
ARG ANDROID_COMPAT_PLATFORM_VERSION=36
ARG ANDROID_PLATFORM_VERSION=37
ARG ANDROID_PLATFORM_PACKAGE_VERSION=37.0
ARG ANDROID_COMPAT_BUILD_TOOLS_VERSION=36.0.0
ARG ANDROID_BUILD_TOOLS_VERSION=37.0.0
ARG ANDROID_NDK_VERSION=28.2.13676358

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
COPY --from=java-runtime /opt/java/openjdk /opt/java/openjdk
COPY --from=node-runtime /node-root/ /

COPY .ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz /tmp/runner.tar.gz
COPY .ciw-build-inputs/flutter-3.44.8-linux-amd64.tar.xz /tmp/flutter.tar.xz
COPY .ciw-build-inputs/android-command-line-tools-15859902-linux.zip /tmp/android-command-line-tools.zip
COPY .ciw-build-inputs/android-platform-tools-36.0.0-linux.zip /tmp/android-platform-tools.zip
COPY .ciw-build-inputs/android-platform-36-r02.zip /tmp/android-platform-36.zip
COPY .ciw-build-inputs/android-platform-37.0-r01.zip /tmp/android-platform-37.zip
COPY .ciw-build-inputs/android-build-tools-36.0.0-linux.zip /tmp/android-build-tools-36.zip
COPY .ciw-build-inputs/android-build-tools-37.0.0-linux.zip /tmp/android-build-tools-37.zip
COPY .ciw-build-inputs/android-ndk-r28c-linux.zip /tmp/android-ndk.zip
COPY --chmod=0755 assemble.py /usr/local/bin/runner-mobile-assemble
COPY --chmod=0755 unzip.py /usr/local/bin/unzip
COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

RUN set -eux; \
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
