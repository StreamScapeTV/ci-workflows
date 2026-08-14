# syntax=docker/dockerfile:1.7

FROM docker.io/library/eclipse-temurin:25-jdk-noble@sha256:3eb81ed94d8c1a34422f19f8188548bdf02cae69c91d0328afdbb7abed90f617 AS java-runtime
FROM docker.io/library/node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node-runtime
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG FLUTTER_VERSION=3.44.8
ARG DART_VERSION=3.12.2
ARG FLUTTER_SHA256=672089e001571a9fbb209a495c583580c0c6c73ef98999264ba07fa93ace332d
ARG ANDROID_CMDLINE_TOOLS_VERSION=15859902
ARG ANDROID_CMDLINE_TOOLS_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
ARG ANDROID_COMPAT_PLATFORM_VERSION=36
ARG ANDROID_PLATFORM_VERSION=37
ARG ANDROID_PLATFORM_PACKAGE_VERSION=37.0
ARG ANDROID_COMPAT_BUILD_TOOLS_VERSION=36.0.0
ARG ANDROID_BUILD_TOOLS_VERSION=37.0.0
ARG ANDROID_NDK_VERSION=28.2.13676358

ENV DEBIAN_FRONTEND=noninteractive \
    JAVA_HOME=/opt/java/openjdk \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    FLUTTER_HOME=/opt/flutter \
    FLUTTER_SUPPRESS_ANALYTICS=true \
    PUB_ENVIRONMENT=github_actions:streamscapetv_homelab \
    CI=true \
    PATH=/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/opt/java/openjdk/bin:/usr/local/bin:${PATH}

COPY --from=java-runtime /opt/java/openjdk /opt/java/openjdk
COPY --from=node-runtime /usr/local/ /usr/local/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash build-essential ca-certificates clang cmake curl git jq \
      lib32stdc++6 lib32z1 libglu1-mesa libgtk-3-dev libstdc++6 \
      ninja-build pkg-config unzip util-linux xz-utils zip \
    && rm -rf /var/lib/apt/lists/*

RUN rm -f \
      /usr/bin/docker /usr/local/bin/docker /usr/bin/dockerd /usr/local/bin/dockerd \
      /usr/bin/containerd /usr/bin/containerd-shim-runc-v2 /usr/bin/ctr \
      /usr/bin/docker-init /usr/bin/docker-proxy /usr/bin/runc \
      /usr/local/lib/docker/cli-plugins/docker-buildx \
      /usr/local/lib/docker/cli-plugins/docker-compose \
    && rm -rf /etc/docker /root/.docker /home/runner/.docker /usr/libexec/docker /usr/local/lib/docker /var/lib/docker \
    && ! command -v docker \
    && ! command -v dockerd \
    && ! command -v buildah \
    && ! command -v podman \
    && ! command -v skopeo \
    && test ! -e /var/run/docker.sock \
    && test ! -e /run/docker.sock

RUN curl --fail --location --retry 5 --output /tmp/flutter.tar.xz \
      "https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_${FLUTTER_VERSION}-stable.tar.xz" \
    && echo "${FLUTTER_SHA256}  /tmp/flutter.tar.xz" | sha256sum --check --strict \
    && tar --extract --xz --file /tmp/flutter.tar.xz --directory /opt \
    && rm /tmp/flutter.tar.xz \
    && git config --system --add safe.directory /opt/flutter

RUN install -d -o runner -g runner /opt/android-sdk/cmdline-tools /opt/runner-cache \
    && curl --fail --location --retry 5 --output /tmp/android-command-line-tools.zip \
      "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMDLINE_TOOLS_VERSION}_latest.zip" \
    && echo "${ANDROID_CMDLINE_TOOLS_SHA256}  /tmp/android-command-line-tools.zip" | sha256sum --check --strict \
    && unzip -q /tmp/android-command-line-tools.zip -d /tmp/android-command-line-tools \
    && mv /tmp/android-command-line-tools/cmdline-tools /opt/android-sdk/cmdline-tools/latest \
    && rm -rf /tmp/android-command-line-tools /tmp/android-command-line-tools.zip \
    && chown -R runner:runner /opt/android-sdk /opt/flutter /opt/runner-cache /home/runner/_work

USER runner

RUN yes | sdkmanager --licenses >/dev/null 2>&1 || true \
    && sdkmanager --channel=3 \
      "platform-tools" \
      "platforms;android-${ANDROID_COMPAT_PLATFORM_VERSION}" \
      "platforms;android-${ANDROID_PLATFORM_PACKAGE_VERSION}" \
      "build-tools;${ANDROID_COMPAT_BUILD_TOOLS_VERSION}" \
      "build-tools;${ANDROID_BUILD_TOOLS_VERSION}" \
      "ndk;${ANDROID_NDK_VERSION}" \
    && ln -sfn "android-${ANDROID_PLATFORM_PACKAGE_VERSION}" \
      "${ANDROID_HOME}/platforms/android-${ANDROID_PLATFORM_VERSION}"

RUN flutter config --no-analytics --enable-web --enable-linux-desktop \
    && flutter precache --android --web --linux

COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

WORKDIR /home/runner
RUN /usr/local/bin/runner-image-smoke
