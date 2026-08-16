#!/usr/bin/env bash
set -Eeuo pipefail

test "$(id -un)" = runner
test -x /home/runner/run.sh

test "${BUILDAH_ISOLATION}" = chroot
test "${STORAGE_DRIVER}" = vfs
test "${XDG_RUNTIME_DIR}" = /home/runner/.local/run
test "${REGISTRY_AUTH_FILE}" = /home/runner/.config/containers/auth.json

buildah --version | grep -F '1.33.7'
skopeo --version | grep -F '1.13.3'
podman --version | grep -F '4.9.3'
podman-compose --version | grep -F '1.0.6'
crun --version >/dev/null
python3 --version >/dev/null
jq --version >/dev/null

test -r /home/runner/.config/containers/storage.conf
test -r /home/runner/.config/containers/containers.conf
test -w /home/runner/.local/share/containers/storage
test -w /home/runner/.local/share/containers/runroot
test -w /home/runner/_work
grep -Fx 'runner:100000:65536' /etc/subuid >/dev/null
grep -Fx 'runner:100000:65536' /etc/subgid >/dev/null
grep -F 'driver = "vfs"' /home/runner/.config/containers/storage.conf >/dev/null
grep -F 'runtime = "crun"' /home/runner/.config/containers/containers.conf >/dev/null

! command -v docker
! command -v dockerd
! command -v containerd
! test -e /var/run/docker.sock
! test -e /run/docker.sock
