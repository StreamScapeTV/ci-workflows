#!/usr/bin/env bash
set -euo pipefail

test "$(id -un)" = runner
test -x /home/runner/run.sh
python3 --version | grep -F 'Python 3.12.13'
node --version | grep -F 'v24.19.0'
command -v npm corepack git bash curl jq yq tar zstd gzip unzip zip helm kustomize kubectl >/dev/null
npm --version >/dev/null
corepack --version >/dev/null
git --version >/dev/null
jq --version >/dev/null
yq --version | grep -F 'v4.53.3'
helm version --short | grep -F 'v4.2.4'
kustomize version | grep -F 'v5.8.1'
kubectl version --client=true --output=yaml | grep -F 'gitVersion: v1.36.3'
for forbidden in docker dockerd containerd ctr runc buildah podman skopeo; do
  ! command -v "${forbidden}"
done
test ! -e /var/run/docker.sock
test ! -e /run/docker.sock
