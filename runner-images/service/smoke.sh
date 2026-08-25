#!/usr/bin/env bash
set -Eeuo pipefail

test "$(id -un)" = runner
test -x /home/runner/run.sh

test "${STORAGE_DRIVER}" = vfs
test "${XDG_RUNTIME_DIR}" = /home/runner/.local/run

podman --version | grep -F '4.9.3'
podman-compose --version | grep -F '1.0.6'
test "$(dpkg-query -W -f='${Version}' netavark)" = '1.4.0-4'
test "$(dpkg-query -W -f='${Version}' aardvark-dns)" = '1.4.0-5'
test -x /usr/lib/podman/netavark
test -x /usr/lib/podman/aardvark-dns
/usr/lib/podman/netavark --version >/dev/null
/usr/lib/podman/aardvark-dns --version >/dev/null
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
grep -F 'network_backend = "netavark"' /home/runner/.config/containers/containers.conf >/dev/null

! command -v docker
! command -v dockerd
! command -v containerd
! command -v buildah
! command -v skopeo
! test -e /var/run/docker.sock
! test -e /run/docker.sock
! test -e /home/runner/.kube
! test -e /var/run/secrets/kubernetes.io/serviceaccount/token
test -z "${KUBECONFIG:-}"

if [[ "${CIW_RUNNER_IMAGE_BUILD_PHASE:-0}" = "1" ]]; then
  exit 0
fi

# The shared GitHub-hosted image builder executes this finished image inside an
# existing rootless Buildah user namespace. That environment cannot create the
# second user namespace required by rootless Podman (newuidmap is denied by the
# outer namespace). Keep that packaging probe static; the same default smoke on
# a normal service-small runner executes the live network/DNS proof below.
if ! grep -Eq '^[[:space:]]*0[[:space:]]+0[[:space:]]+4294967295[[:space:]]*$' /proc/self/uid_map; then
  echo 'service runner network smoke: live probe deferred inside nested validation user namespace'
  exit 0
fi

test "$(podman info --format '{{.Host.NetworkBackend}}')" = netavark

smoke_root=/home/runner/_work/runner-service-network-smoke
network_name="ciw-service-smoke-$$"
backend_name="${network_name}-peer"
client_name="${network_name}-client"
service_alias=backend
fixture_image=docker.io/library/alpine:3.20.3

rm -rf "${smoke_root}"
mkdir -m 0700 -p "${smoke_root}"

cleanup_service_network_smoke() {
  original_status=$?
  trap - EXIT INT TERM
  set +e
  failed=0

  if podman container exists "${client_name}"; then
    podman rm --force "${client_name}" >/dev/null 2>&1 || failed=1
  fi
  if podman container exists "${backend_name}"; then
    podman rm --force "${backend_name}" >/dev/null 2>&1 || failed=1
  fi
  if podman network exists "${network_name}"; then
    podman network rm "${network_name}" >/dev/null 2>&1 || failed=1
  fi
  if podman image exists "${fixture_image}"; then
    podman image rm --force "${fixture_image}" >/dev/null 2>&1 || failed=1
  fi
  rm -rf -- "${smoke_root}" || failed=1

  podman container exists "${client_name}" && failed=1
  podman container exists "${backend_name}" && failed=1
  podman network exists "${network_name}" && failed=1
  podman image exists "${fixture_image}" && failed=1
  test ! -e "${smoke_root}" || failed=1

  if (( failed != 0 )); then
    echo "service runner network smoke cleanup left run-owned residue" >&2
    exit 1
  fi
  exit "${original_status}"
}
trap cleanup_service_network_smoke EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

podman network create "${network_name}" >/dev/null
test "$(podman network inspect --format '{{.DNSEnabled}}' "${network_name}")" = true

podman pull "${fixture_image}" >/dev/null
podman run \
  --detach \
  --name "${backend_name}" \
  --network "${network_name}" \
  --network-alias "${service_alias}" \
  "${fixture_image}" \
  sh -ceu 'mkdir -p /www; printf "ok\n" > /www/health; exec httpd -f -p 8080 -h /www' \
  >/dev/null

backend_ip="$(
  podman inspect \
    --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
    "${backend_name}"
)"
[[ "${backend_ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]

podman run \
  --name "${client_name}" \
  --network "${network_name}" \
  --env "BACKEND_IP=${backend_ip}" \
  "${fixture_image}" \
  sh -ceu '
    fetch_exact() {
      endpoint="$1"
      attempt=0
      while [ "${attempt}" -lt 20 ]; do
        response="$(wget -qO- "${endpoint}" 2>/dev/null || true)"
        if [ "${response}" = "ok" ]; then
          return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
      done
      printf "failed to reach %s\n" "${endpoint}" >&2
      return 1
    }
    fetch_exact http://backend:8080/health
    fetch_exact "http://${BACKEND_IP}:8080/health"
  '

cleanup_service_network_smoke
