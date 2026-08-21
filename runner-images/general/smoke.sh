#!/usr/bin/env bash
set -euo pipefail

test "$(id -un)" = runner
test "$(id -u)" = 1001
test -x /home/runner/run.sh
test -x /home/runner/bin/Runner.Listener
bash -n /home/runner/run.sh
grep -Fx 'ID=debian' /etc/os-release
grep -Fx 'VERSION_CODENAME=trixie' /etc/os-release
! grep -Eiq 'ubuntu|alpine|fedora|rhel|suse' /etc/os-release

/home/runner/bin/Runner.Listener --version | grep -F '2.336.0'
test -e /usr/lib/x86_64-linux-gnu/libatomic.so.1

test -s /etc/ssl/certs/ca-certificates.crt
grep -Fq -- '-----BEGIN CERTIFICATE-----' /etc/ssl/certs/ca-certificates.crt

python3 --version | grep -F 'Python 3.12.14'
venv_root="$(mktemp -d)"
trap 'rm -rf "${venv_root}"' EXIT
python3 -m venv "${venv_root}/venv"
"${venv_root}/venv/bin/python" -m pip --version >/dev/null

node --version | grep -F 'v24.19.0'
npm --version >/dev/null
corepack --version >/dev/null

command -v git bash curl jq yq tar zstd gzip unzip zip helm kustomize kubectl \
  cmake ctest gcc g++ make >/dev/null
git --version >/dev/null
test -x "$(git --exec-path)/git-remote-https"
test -d /usr/share/git-core/templates
git init -q "${venv_root}/git-repository"
git -C "${venv_root}/git-repository" status --short >/dev/null
bash --version | head -n1 | grep -F 'GNU bash'
curl --version | head -n1 | grep -F 'curl '
jq --version | grep -F 'jq-1.8.2'
yq --version | grep -F 'v4.53.3'
tar --version | head -n1 >/dev/null
zstd --version | grep -F 'v1.5.7'
gzip --version | head -n1 >/dev/null
unzip -v | head -n1 >/dev/null
zip --version | grep -F 'ci-workflows zip 1.0'
helm version --short | grep -F 'v4.2.4'
kustomize version | grep -F 'v5.8.1'
kubectl version --client=true --output=yaml | grep -F 'gitVersion: v1.36.2'
cmake --version | head -n1 | grep -Fx 'cmake version 4.4.2'
test "$(gcc -dumpfullversion)" = '14.2.0'
test "$(g++ -dumpfullversion)" = '14.2.0'
make --version | head -n1 | grep -Fx 'GNU Make 4.4.1'

native_source="${venv_root}/native-source"
native_build="${venv_root}/native-build"
mkdir -p "${native_source}"
cat > "${native_source}/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.20)
project(ciw_native_smoke LANGUAGES CXX)
enable_testing()
add_executable(native-smoke main.cpp)
add_test(NAME native-smoke COMMAND native-smoke)
CMAKE
cat > "${native_source}/main.cpp" <<'CPP'
#include <iostream>
int main() {
    std::cout << "native-ready" << std::endl;
    return 0;
}
CPP
cmake -S "${native_source}" -B "${native_build}" -G 'Unix Makefiles' -DCMAKE_BUILD_TYPE=Release
cmake --build "${native_build}" --parallel 2
ctest --test-dir "${native_build}" --output-on-failure
"${native_build}/native-smoke" | grep -Fx 'native-ready'

for forbidden in docker dockerd containerd ctr runc buildah podman skopeo sudo apt apt-get dpkg; do
  ! command -v "${forbidden}"
done
test ! -e /var/run/docker.sock
test ! -e /run/docker.sock
test ! -e /home/runner/.docker
test ! -e /home/runner/.kube
test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token
test -z "${KUBECONFIG:-}"
