#!/usr/bin/env bash
set -euo pipefail

test "$(id -un)" = runner
test -x /home/runner/run.sh
bash -n /home/runner/run.sh

docker --version | grep -F 'Docker version 29.8.0'
docker buildx version | grep -F 'v0.37.0'
docker compose version | grep -F 'v5.5.1'
docker-compose version | grep -F 'v5.5.1'

for forbidden in dockerd containerd ctr runc docker-proxy docker-init; do
  ! command -v "${forbidden}"
done

test ! -e /var/run/docker.sock
test ! -e /run/docker.sock
test ! -e /home/runner/.docker/config.json
test ! -e /home/runner/.kube/config
test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token
test -z "${KUBECONFIG:-}"
