from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ServiceRunnerDnsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = (ROOT / "runner-images/service/Dockerfile").read_text(
            encoding="utf-8"
        )
        cls.smoke = (ROOT / "runner-images/service/smoke.sh").read_text(
            encoding="utf-8"
        )
        cls.lock = json.loads(
            (ROOT / "runner-images/service/toolchain.lock.json").read_text(
                encoding="utf-8"
            )
        )

    def test_noble_dns_backend_is_explicit_and_version_locked(self) -> None:
        self.assertEqual(
            self.lock["packages"],
            {
                "aardvark-dns": "1.4.0-5",
                "netavark": "1.4.0-4",
                "podman": "4.9.3+ds1-1ubuntu0.2",
                "podman-compose": "1.0.6-1",
            },
        )
        for expected in (
            "NETAVARK_PACKAGE_VERSION=1.4.0-4",
            "AARDVARK_DNS_PACKAGE_VERSION=1.4.0-5",
            'aardvark-dns="${AARDVARK_DNS_PACKAGE_VERSION}"',
            'netavark="${NETAVARK_PACKAGE_VERSION}"',
            "apt-mark hold aardvark-dns netavark podman podman-compose",
            'network_backend = "netavark"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.dockerfile)
        for expected in (
            "test -x /usr/lib/podman/netavark",
            "test -x /usr/lib/podman/aardvark-dns",
            "podman info --format '{{.Host.NetworkBackend}}'",
            "= netavark",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.smoke)

    def test_full_image_smoke_proves_alias_and_ip_connectivity(self) -> None:
        self.assertIn(
            "RUN CIW_RUNNER_IMAGE_BUILD_PHASE=1 /usr/local/bin/runner-image-smoke",
            self.dockerfile,
        )
        for expected in (
            "4294967295",
            "live probe deferred inside nested validation user namespace",
            'network_name="ciw-service-smoke-$$"',
            'podman network create "${network_name}"',
            "podman network inspect --format '{{.DNSEnabled}}'",
            "service_alias=backend",
            '--network-alias "${service_alias}"',
            "backend_ip=",
            "fetch_exact http://backend:8080/health",
            'fetch_exact "http://${BACKEND_IP}:8080/health"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.smoke)

    def test_full_image_smoke_cleans_run_owned_network_state_on_every_exit(self) -> None:
        for expected in (
            "trap cleanup_service_network_smoke EXIT",
            "trap 'exit 130' INT",
            "trap 'exit 143' TERM",
            'podman rm --force "${client_name}"',
            'podman rm --force "${backend_name}"',
            'podman network rm "${network_name}"',
            'podman image rm --force "${fixture_image}"',
            'rm -rf -- "${smoke_root}"',
            'podman container exists "${client_name}" && failed=1',
            'podman container exists "${backend_name}" && failed=1',
            'podman network exists "${network_name}" && failed=1',
            'podman image exists "${fixture_image}" && failed=1',
            'test ! -e "${smoke_root}" || failed=1',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.smoke)

    def test_dns_fix_does_not_broaden_service_runner_privilege(self) -> None:
        combined = self.dockerfile + "\n" + self.smoke
        for forbidden in (
            "--privileged",
            "--network host",
            "--network=host",
            "/var/run/docker.sock:",
            "/run/docker.sock:",
            "\nbuildah ",
            "\nskopeo ",
            "kubectl ",
            "KUBECONFIG=",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
