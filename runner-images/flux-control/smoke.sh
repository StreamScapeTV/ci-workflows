#!/usr/bin/env bash
set -euo pipefail

test "$(id -un)" = runner
test -x /home/runner/run.sh
bash -n /home/runner/run.sh

flux --version | grep -F '2.9.4'
kubectl version --client=true --output=yaml | grep -F 'gitVersion: v1.34.8'
helm version --short | grep -F 'v4.2.4'
kustomize version | grep -F 'v5.8.1'
jq --version >/dev/null
yq --version | grep -F 'v4.53.3'

for forbidden in docker dockerd containerd ctr runc buildah podman skopeo; do
  ! command -v "${forbidden}"
done

test ! -e /var/run/docker.sock
test ! -e /run/docker.sock
test ! -e /home/runner/.docker/config.json
test ! -e /home/runner/.config/containers/auth.json
test ! -e /home/runner/.kube/config
test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token
test ! -e /home/runner/.config/sops/age/keys.txt

test -z "${KUBECONFIG:-}"
test -z "${SOPS_AGE_KEY:-}"
test -z "${SOPS_AGE_KEY_FILE:-}"
test -z "${GITHUB_TOKEN:-}"
test -z "${GH_TOKEN:-}"
test -z "${REGISTRY_AUTH_FILE:-}"
test -z "${DOCKER_CONFIG:-}"
