#!/usr/bin/env bash
set -Eeuo pipefail

test "$(id -un)" = runner
test -x /home/runner/run.sh

test "${JAVA_HOME}" = /opt/java/openjdk
test "${ANDROID_HOME}" = /opt/android-sdk
test "${ANDROID_SDK_ROOT}" = /opt/android-sdk
test "${FLUTTER_HOME}" = /opt/flutter

java -version 2>&1 | grep -F '25.'
javac -version 2>&1 | grep -F 'javac 25'
flutter --version | grep -F 'Flutter 3.44.8'
dart --version 2>&1 | grep -F 'Dart SDK version: 3.12.2'
node --version | grep -F 'v24.18.0'
npm --version >/dev/null
corepack --version >/dev/null
sdkmanager --version >/dev/null
adb version >/dev/null

test -s "${ANDROID_HOME}/platforms/android-36/android.jar"
test -s "${ANDROID_HOME}/platforms/android-37.0/android.jar"
test -s "${ANDROID_HOME}/platforms/android-37/android.jar"
test -d "${ANDROID_HOME}/build-tools/36.0.0"
test -d "${ANDROID_HOME}/build-tools/37.0.0"
test -d "${ANDROID_HOME}/ndk/28.2.13676358"
test -w /home/runner/_work

! command -v docker
! command -v dockerd
! command -v buildah
! command -v podman
! command -v skopeo
! test -e /var/run/docker.sock
! test -e /run/docker.sock
