#!/usr/bin/env bash
set -Eeuo pipefail

test "$(id -un)" = runner
test "$(id -u)" = 1001
test -x /home/runner/run.sh
test -x /home/runner/bin/Runner.Listener
bash -n /home/runner/run.sh
grep -Fx 'ID=debian' /etc/os-release
grep -Fx 'VERSION_CODENAME=trixie' /etc/os-release

/home/runner/bin/Runner.Listener --version | grep -F '2.336.0'
test -e /usr/lib/x86_64-linux-gnu/libatomic.so.1
test -e /usr/lib/x86_64-linux-gnu/libstdc++.so.6
test -e /usr/lib/x86_64-linux-gnu/libgcc_s.so.1

test "${JAVA_HOME}" = /opt/java/openjdk
test "${ANDROID_HOME}" = /opt/android-sdk
test "${ANDROID_SDK_ROOT}" = /opt/android-sdk
test "${FLUTTER_HOME}" = /opt/flutter

java -version 2>&1 | grep -F '25.0.3'
javac -version 2>&1 | grep -F 'javac 25.0.3'
python3 --version | grep -F 'Python 3.12.14'
node --version | grep -F 'v24.19.0'
npm --version >/dev/null
corepack --version >/dev/null
flutter --version | grep -F 'Flutter 3.44.8'
dart --version 2>&1 | grep -F 'Dart SDK version: 3.12.2'
sdkmanager --version >/dev/null
adb version >/dev/null
"${ANDROID_HOME}/build-tools/37.0.0/aapt2" version >/dev/null
"${ANDROID_HOME}/ndk/28.2.13676358/toolchains/llvm/prebuilt/linux-x86_64/bin/clang" --version >/dev/null

command -v git bash curl tar gzip zip unzip >/dev/null
git --version >/dev/null
test -x "$(git --exec-path)/git-remote-https"
test -d /usr/share/git-core/templates
curl --version | head -n1 | grep -F 'curl '
tar --version | head -n1 >/dev/null
gzip --version | head -n1 >/dev/null
zip --version | grep -F 'ci-workflows zip 1.0'
unzip --version | grep -F 'ci-workflows unzip 1.0'

test -s "${ANDROID_HOME}/platforms/android-36/android.jar"
test -s "${ANDROID_HOME}/platforms/android-37.0/android.jar"
test -L "${ANDROID_HOME}/platforms/android-37"
test -d "${ANDROID_HOME}/build-tools/36.0.0"
test -d "${ANDROID_HOME}/build-tools/37.0.0"
test -d "${ANDROID_HOME}/ndk/28.2.13676358"
test -w /home/runner/_work

for forbidden in docker dockerd containerd ctr runc buildah podman skopeo sudo apt apt-get dpkg; do
  ! command -v "${forbidden}"
done
test ! -e /var/run/docker.sock
test ! -e /run/docker.sock
test ! -e /home/runner/.docker
test ! -e /home/runner/.kube
test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token
test -z "${KUBECONFIG:-}"
