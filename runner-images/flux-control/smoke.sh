#!/usr/bin/env bash
set -euo pipefail

test "$(id -un)" = runner
test -x /home/runner/run.sh
flux --version | grep -F '2.9.4'
kubectl version --client=true --output=yaml | grep -F 'gitVersion: v1.34.8'
helm version --short | grep -F 'v4.2.4'
kustomize version | grep -F 'v5.8.1'
jq --version
yq --version | grep -F 'v4.53.3'
test -z "${KUBECONFIG:-}"
test ! -e /home/runner/.kube/config
test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token
! command -v docker
! command -v dockerd
! command -v buildah
! command -v podman
! command -v skopeo
