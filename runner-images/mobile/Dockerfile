# syntax=docker/dockerfile:1.7

# Every mutable download is materialized and digest-verified by the central OCI
# input contract before Buildah starts. Dockerfile execution remains networkless.
FROM docker.io/library/eclipse-temurin:25-jdk-noble@sha256:3eb81ed94d8c1a34422f19f8188548bdf02cae69c91d0328afdbb7abed90f617 AS java-runtime
FROM docker.io/library/node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node-runtime
FROM docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS python-runtime
FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

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
    CI=true \
    PATH=/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/opt/java/openjdk/bin:/usr/local/bin:${PATH}

COPY --from=java-runtime /opt/java/openjdk /opt/java/openjdk
COPY --from=node-runtime /usr/local/ /usr/local/
COPY --from=python-runtime /usr/local/ /usr/local/

# The official Actions runner currently carries Docker's static runtime bundle.
# Mobile jobs need no Docker/Buildah/Podman daemon or client capability.
RUN rm -f \
      /usr/bin/docker /usr/local/bin/docker /usr/bin/dockerd /usr/local/bin/dockerd \
      /usr/bin/containerd /usr/bin/containerd-shim-runc-v2 /usr/bin/ctr \
      /usr/bin/docker-init /usr/bin/docker-proxy /usr/bin/runc \
      /usr/local/lib/docker/cli-plugins/docker-buildx \
      /usr/local/lib/docker/cli-plugins/docker-compose \
    && rm -rf /etc/docker /root/.docker /home/runner/.docker /usr/libexec/docker /usr/local/lib/docker /var/lib/docker \
    && ! command -v docker \
    && ! command -v dockerd \
    && ! command -v containerd \
    && ! command -v buildah \
    && ! command -v podman \
    && ! command -v skopeo \
    && test ! -e /var/run/docker.sock \
    && test ! -e /run/docker.sock

COPY .ciw-build-inputs/flutter-3.44.8-linux-amd64.tar.xz /tmp/flutter.tar.xz
COPY .ciw-build-inputs/android-command-line-tools-15859902-linux.zip /tmp/android-command-line-tools.zip
COPY .ciw-build-inputs/android-platform-tools-36.0.0-linux.zip /tmp/android-platform-tools.zip
COPY .ciw-build-inputs/android-platform-36-r02.zip /tmp/android-platform-36.zip
COPY .ciw-build-inputs/android-platform-37.0-r01.zip /tmp/android-platform-37.zip
COPY .ciw-build-inputs/android-build-tools-36.0.0-linux.zip /tmp/android-build-tools-36.zip
COPY .ciw-build-inputs/android-build-tools-37.0.0-linux.zip /tmp/android-build-tools-37.zip
COPY .ciw-build-inputs/android-ndk-r28c-linux.zip /tmp/android-ndk.zip

# Python is copied from an exact OCI stage so large xz/zip SDK payloads can be
# unpacked without installing host packages or reaching the network.
RUN python3 - <<'PY'
from pathlib import Path
import shutil
import tarfile
import zipfile

android = Path('/opt/android-sdk')
android.mkdir(parents=True, exist_ok=True)

with tarfile.open('/tmp/flutter.tar.xz', 'r:xz') as archive:
    archive.extractall('/opt', filter='data')


def extract_zip(source: str, temp: str) -> Path:
    target = Path(temp)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        archive.extractall(target)
    return target


def copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)

cmdline = extract_zip('/tmp/android-command-line-tools.zip', '/tmp/cmdline')
copy_tree(cmdline / 'cmdline-tools', android / 'cmdline-tools' / 'latest')

platform_tools = extract_zip('/tmp/android-platform-tools.zip', '/tmp/platform-tools')
copy_tree(platform_tools / 'platform-tools', android / 'platform-tools')

for source, api in (
    ('/tmp/android-platform-36.zip', '36'),
    ('/tmp/android-platform-37.zip', '37.0'),
):
    unpacked = extract_zip(source, f'/tmp/platform-{api}')
    jars = list(unpacked.rglob('android.jar'))
    if len(jars) != 1:
        raise SystemExit(f'expected one android.jar for platform {api}, got {len(jars)}')
    copy_tree(jars[0].parent, android / 'platforms' / f'android-{api}')

for source, version in (
    ('/tmp/android-build-tools-36.zip', '36.0.0'),
    ('/tmp/android-build-tools-37.zip', '37.0.0'),
):
    unpacked = extract_zip(source, f'/tmp/build-tools-{version}')
    candidates = [path.parent for path in unpacked.rglob('aapt2') if path.is_file()]
    if len(candidates) != 1:
        raise SystemExit(f'expected one build-tools directory for {version}, got {len(candidates)}')
    copy_tree(candidates[0], android / 'build-tools' / version)

ndk = extract_zip('/tmp/android-ndk.zip', '/tmp/ndk')
ndk_roots = [path for path in ndk.iterdir() if path.is_dir()]
if len(ndk_roots) != 1:
    raise SystemExit(f'expected one NDK root, got {len(ndk_roots)}')
copy_tree(ndk_roots[0], android / 'ndk' / '28.2.13676358')
PY

RUN ln -sfn "android-${ANDROID_PLATFORM_PACKAGE_VERSION}" \
      "${ANDROID_HOME}/platforms/android-${ANDROID_PLATFORM_VERSION}" \
    && git config --system --add safe.directory /opt/flutter \
    && install -d -o runner -g runner /opt/runner-cache \
    && chown -R runner:runner /opt/android-sdk /opt/flutter /opt/runner-cache /home/runner/_work \
    && rm -rf /tmp/flutter.tar.xz /tmp/android-*.zip /tmp/cmdline /tmp/platform-tools /tmp/platform-* /tmp/build-tools-* /tmp/ndk

COPY --chmod=0755 smoke.sh /usr/local/bin/runner-image-smoke

USER runner
WORKDIR /home/runner
RUN /usr/local/bin/runner-image-smoke
