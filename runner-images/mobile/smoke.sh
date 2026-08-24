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

test -s "${ANDROID_HOME}/platforms/android-36/android.jar"
test -s "${ANDROID_HOME}/platforms/android-37.0/android.jar"
test -L "${ANDROID_HOME}/platforms/android-37"
test "$(readlink "${ANDROID_HOME}/platforms/android-37")" = "android-37.0"
compat_platform_properties="${ANDROID_HOME}/platforms/android-36/source.properties"
platform37_properties="${ANDROID_HOME}/platforms/android-37.0/source.properties"
platform37_package_xml="${ANDROID_HOME}/platforms/android-37.0/package.xml"
test -s "${compat_platform_properties}"
test -s "${platform37_properties}"
test -s "${platform37_package_xml}"
grep -Fx 'Pkg.Revision=2' "${compat_platform_properties}"
grep -Fx 'AndroidVersion.ApiLevel=36' "${compat_platform_properties}"
grep -Fx 'Pkg.Revision=2' "${platform37_properties}"
grep -Fx 'AndroidVersion.ApiLevel=37.0' "${platform37_properties}"
grep -Fx 'AndroidVersion.ExtensionLevel=22' "${platform37_properties}"
grep -F '<localPackage path="platforms;android-37.0" obsolete="false">' "${platform37_package_xml}"
grep -F '<api-level>37.0</api-level>' "${platform37_package_xml}"
grep -F '<extension-level>22</extension-level>' "${platform37_package_xml}"
grep -F '<base-extension>true</base-extension>' "${platform37_package_xml}"
grep -F '<major>2</major>' "${platform37_package_xml}"
test -d "${ANDROID_HOME}/build-tools/36.0.0"
test -d "${ANDROID_HOME}/build-tools/37.0.0"
test -d "${ANDROID_HOME}/ndk/28.2.13676358"
test ! -e "${ANDROID_HOME}/platforms/android-37.0-2"
test ! -w "${ANDROID_HOME}"
test ! -w "${ANDROID_HOME}/platforms"
test ! -w "${ANDROID_HOME}/platforms/android-36"
test ! -w "${ANDROID_HOME}/platforms/android-37.0"
test ! -w "${ANDROID_HOME}/cmake"
test ! -w "${ANDROID_HOME}/licenses/android-sdk-license"
test -z "$(find "${ANDROID_HOME}" -xdev \( -type f -o -type d \) -perm /222 -print -quit)"
test -w /home/runner/_work
test "$(stat -c '%a:%u:%g' /tmp)" = "1777:0:0"

command -v git bash curl tar gzip zip unzip >/dev/null
git --version >/dev/null
test -x "$(git --exec-path)/git-remote-https"
test -d /usr/share/git-core/templates
curl --version | head -n1 | grep -F 'curl '
tar --version | head -n1 >/dev/null
gzip --version | head -n1 >/dev/null
zip --version | grep -F 'ci-workflows zip 1.0'
unzip --version | grep -F 'ci-workflows unzip 1.0'

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
  mkdir -p "${flutter_smoke_root}/tmp" "${flutter_smoke_root}/jvm-tmp"
  chmod 0700 "${flutter_smoke_root}/tmp" "${flutter_smoke_root}/jvm-tmp"
  cleanup_flutter_smoke() {
    rm -rf "${flutter_smoke_root}"
  }
  trap cleanup_flutter_smoke EXIT

  export TMPDIR="${flutter_smoke_root}/tmp"
  export TMP="${TMPDIR}"
  export TEMP="${TMPDIR}"
  export JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=${flutter_smoke_root}/jvm-tmp"
  export PUB_CACHE="${flutter_smoke_root}/pub-cache"
  export GRADLE_USER_HOME="${flutter_smoke_root}/gradle"
  for private_tmp in "${TMPDIR}" "${flutter_smoke_root}/jvm-tmp"; do
    test -d "${private_tmp}"
    test ! -L "${private_tmp}"
    test -w "${private_tmp}"
    test "$(stat -c '%a' "${private_tmp}")" = "700"
  done
  java -XshowSettings:properties -version 2>&1 \
    | grep -F "java.io.tmpdir = ${flutter_smoke_root}/jvm-tmp"
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

  app_gradle="${app_root}/android/app/build.gradle.kts"
  test -s "${app_gradle}"
  grep -F 'compileSdk = flutter.compileSdkVersion' "${app_gradle}"
  sed -i 's/compileSdk = flutter\.compileSdkVersion/compileSdk = 37/' "${app_gradle}"
  grep -F 'compileSdk = 37' "${app_gradle}"

  if ! (cd "${app_root}" && flutter pub get >"${pub_log}" 2>&1); then
    cat "${pub_log}" >&2
    exit 1
  fi

  cmake_sha_before="$(sha256sum "${cmake_root}/bin/cmake" | awk '{print $1}')"
  ninja_sha_before="$(sha256sum "${cmake_root}/bin/ninja" | awk '{print $1}')"
  compat_platform_jar_sha_before="$(sha256sum "${ANDROID_HOME}/platforms/android-36/android.jar" | awk '{print $1}')"
  platform37_jar_sha_before="$(sha256sum "${ANDROID_HOME}/platforms/android-37.0/android.jar" | awk '{print $1}')"
  platform37_properties_sha_before="$(sha256sum "${platform37_properties}" | awk '{print $1}')"
  platform37_package_xml_sha_before="$(sha256sum "${platform37_package_xml}" | awk '{print $1}')"
  sdk_tree_before="$(find "${ANDROID_HOME}" -xdev -printf '%P|%y|%s|%m|%u|%g\n' | LC_ALL=C sort | sha256sum | awk '{print $1}')"
  if ! (cd "${app_root}" && flutter build apk --debug --no-pub >"${build_log}" 2>&1); then
    cat "${build_log}" >&2
    exit 1
  fi

  test -s "${app_root}/build/app/outputs/flutter-apk/app-debug.apk"
  test "$(sha256sum "${cmake_root}/bin/cmake" | awk '{print $1}')" = "${cmake_sha_before}"
  test "$(sha256sum "${cmake_root}/bin/ninja" | awk '{print $1}')" = "${ninja_sha_before}"
  test "$(sha256sum "${ANDROID_HOME}/platforms/android-36/android.jar" | awk '{print $1}')" = "${compat_platform_jar_sha_before}"
  test "$(sha256sum "${ANDROID_HOME}/platforms/android-37.0/android.jar" | awk '{print $1}')" = "${platform37_jar_sha_before}"
  test "$(sha256sum "${platform37_properties}" | awk '{print $1}')" = "${platform37_properties_sha_before}"
  test "$(sha256sum "${platform37_package_xml}" | awk '{print $1}')" = "${platform37_package_xml_sha_before}"
  test "$(find "${ANDROID_HOME}" -xdev -printf '%P|%y|%s|%m|%u|%g\n' | LC_ALL=C sort | sha256sum | awk '{print $1}')" = "${sdk_tree_before}"
  test ! -e "${ANDROID_HOME}/platforms/android-37.0-2"
  if grep -E 'Preparing "Install (Android SDK|CMake)|Installing (Android SDK|CMake)|Downloading https://dl\.google\.com/android/repository/' "${build_log}"; then
    cat "${build_log}" >&2
    exit 1
  fi
  if grep -E 'LicenceNotAcceptedException|License for package .* not accepted' "${build_log}"; then
    cat "${build_log}" >&2
    exit 1
  fi

  cleanup_flutter_smoke
  trap - EXIT
fi
