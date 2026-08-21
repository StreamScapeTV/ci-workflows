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

cmake_root="${ANDROID_HOME}/cmake/3.22.1"
test -x "${cmake_root}/bin/cmake"
test -x "${cmake_root}/bin/ninja"
test -s "${cmake_root}/package.xml"
"${cmake_root}/bin/cmake" --version | head -n1 | grep -Fx 'cmake version 3.22.1-g37088a8'
ninja_version="$("${cmake_root}/bin/ninja" --version)"
[[ "${ninja_version}" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]
IFS=. read -r ninja_major ninja_minor _ <<< "${ninja_version}"
(( ninja_major > 1 || (ninja_major == 1 && ninja_minor >= 10) ))

test -s "${ANDROID_HOME}/licenses/android-sdk-license"
for license_hash in \
  8933bad161af4178b1185d1a37fbf41ea5269c55 \
  d56f5187479451eabf01fb78af6dfcb131a6481e \
  24333f8a63b6825ea9c5514f83c2829b004d1fee; do
  grep -Fx "${license_hash}" "${ANDROID_HOME}/licenses/android-sdk-license"
done
test ! -w "${ANDROID_HOME}/cmake"
test ! -w "${ANDROID_HOME}/licenses/android-sdk-license"

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

if [[ "${CIW_RUNNER_IMAGE_BUILD_PHASE:-0}" != "1" ]]; then
  flutter_smoke_root=/home/runner/_work/runner-mobile-flutter-apk-smoke
  rm -rf "${flutter_smoke_root}"
  mkdir -p "${flutter_smoke_root}/tmp"
  chmod 0700 "${flutter_smoke_root}/tmp"
  cleanup_flutter_smoke() {
    rm -rf "${flutter_smoke_root}"
  }
  trap cleanup_flutter_smoke EXIT

  export TMPDIR="${flutter_smoke_root}/tmp"
  export PUB_CACHE="${flutter_smoke_root}/pub-cache"
  export GRADLE_USER_HOME="${flutter_smoke_root}/gradle"
  test -d "${TMPDIR}"
  test ! -L "${TMPDIR}"
  test -w "${TMPDIR}"
  test "$(stat -c '%a' "${TMPDIR}")" = "700"
  app_root="${flutter_smoke_root}/app"
  create_log="${flutter_smoke_root}/flutter-create.log"
  pub_log="${flutter_smoke_root}/flutter-pub-get.log"
  build_log="${flutter_smoke_root}/flutter-build-apk.log"

  if ! flutter create \
      --platforms=android \
      --project-name runner_mobile_smoke \
      --org dev.streamscapetv \
      --no-pub \
      "${app_root}" >"${create_log}" 2>&1; then
    cat "${create_log}" >&2
    exit 1
  fi

  if ! (cd "${app_root}" && flutter pub get >"${pub_log}" 2>&1); then
    cat "${pub_log}" >&2
    exit 1
  fi

  cmake_sha_before="$(sha256sum "${cmake_root}/bin/cmake" | awk '{print $1}')"
  ninja_sha_before="$(sha256sum "${cmake_root}/bin/ninja" | awk '{print $1}')"
  if ! (cd "${app_root}" && flutter build apk --debug --no-pub >"${build_log}" 2>&1); then
    cat "${build_log}" >&2
    exit 1
  fi

  test -s "${app_root}/build/app/outputs/flutter-apk/app-debug.apk"
  test "$(sha256sum "${cmake_root}/bin/cmake" | awk '{print $1}')" = "${cmake_sha_before}"
  test "$(sha256sum "${cmake_root}/bin/ninja" | awk '{print $1}')" = "${ninja_sha_before}"
  if grep -E 'Preparing "Install CMake|Installing CMake|LicenceNotAcceptedException|License for package CMake .* not accepted' "${build_log}"; then
    cat "${build_log}" >&2
    exit 1
  fi

  cleanup_flutter_smoke
  trap - EXIT
fi
