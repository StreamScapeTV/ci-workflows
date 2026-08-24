from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-tag-image-chart.yml"
SELF_CHECK = ROOT / ".github" / "workflows" / "self-check.yml"
README = ROOT / "README.md"
ACTION_LOCK = ROOT / "contracts" / "action-tool-lock.json"
HELPER_SHA = "2b0443fdad002d47625386a959ebe68545cfe022"


class ReusableTagImageChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.self_check = SELF_CHECK.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def _run_image_scenario(
        self,
        scenario: str,
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]], str, bytes, bytes]:
        step_start = self.text.index("      - id: image\n")
        run_marker = "        run: |\n"
        script_start = self.text.index(run_marker, step_start)
        step_end = self.text.index("\n      - id: chart\n", script_start)
        script = textwrap.dedent(
            self.text[script_start + len(run_marker):step_end]
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            state_root = root / "state"
            fake_bin.mkdir()
            state_root.mkdir()
            calls_path = root / "skopeo-calls.jsonl"
            output_path = root / "github-output"

            for name, source in (
                ("buildah", "#!/bin/sh\nexit 0\n"),
                ("git", "#!/bin/sh\nprintf '1700000000\\n'\n"),
                (
                    "sha256sum",
                    "#!/usr/bin/env python3\n"
                    "import hashlib, pathlib, sys\n"
                    "path = pathlib.Path(sys.argv[1])\n"
                    "print(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}')\n",
                ),
            ):
                executable = fake_bin / name
                executable.write_text(source, encoding="utf-8")
                executable.chmod(0o700)

            fake_skopeo = fake_bin / "skopeo"
            fake_skopeo.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import os
                    from pathlib import Path
                    import sys

                    args = sys.argv[1:]
                    with Path(os.environ["SKOPEO_CALLS"]).open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(args) + "\\n")
                    scenario = os.environ["IMAGE_SCENARIO"]
                    operation = args[0]
                    target = args[-1]
                    remote = target.startswith("docker://")

                    if operation == "copy":
                        raise SystemExit(0)
                    if operation == "list-tags":
                        if scenario == "list-tags-auth-failure":
                            raise SystemExit(1)
                        if scenario == "list-tags-transient-failure":
                            raise SystemExit(2)
                        if scenario == "list-tags-malformed":
                            sys.stdout.write("{")
                            raise SystemExit(0)
                        tags = [] if scenario == "publish-match" else ["1.0.4"]
                        sys.stdout.write(json.dumps({"Repository": target, "Tags": tags}))
                        raise SystemExit(0)
                    if operation != "inspect":
                        raise SystemExit(2)

                    if "--config" in args:
                        architecture = args[args.index("--override-arch") + 1]
                        config = {
                            "architecture": architecture,
                            "config": {
                                "Entrypoint": ["/app/start"],
                                "Labels": {
                                    "org.opencontainers.image.revision": os.environ["SOURCE_SHA"],
                                    "org.opencontainers.image.source": (
                                        f"{os.environ['GITHUB_SERVER_URL']}/"
                                        f"{os.environ['GITHUB_REPOSITORY']}"
                                    ),
                                    "org.opencontainers.image.version": os.environ["VERSION"],
                                },
                            },
                            "history": [{"created_by": "bounded-build"}],
                            "os": "linux",
                            "rootfs": {
                                "diff_ids": [f"sha256:{architecture}-rootfs"],
                                "type": "layers",
                            },
                        }
                        if remote and architecture == "arm64":
                            if scenario == "replay-runtime-change":
                                config["config"]["Entrypoint"] = ["/other/start"]
                            elif scenario == "replay-rootfs-change":
                                config["rootfs"]["diff_ids"] = ["sha256:changed-rootfs"]
                        if remote and scenario == "replay-reserialized-config":
                            sys.stdout.write(json.dumps(config, indent=2, sort_keys=False))
                        else:
                            sys.stdout.write(
                                json.dumps(
                                    config,
                                    separators=(",", ":"),
                                    sort_keys=True,
                                )
                            )
                        raise SystemExit(0)

                    manifests = [
                        {
                            "digest": "sha256:amd64-" + ("remote" if remote else "local"),
                            "platform": {"architecture": "amd64", "os": "linux"},
                        },
                        {
                            "digest": "sha256:arm64-" + ("remote" if remote else "local"),
                            "platform": {
                                "architecture": "arm64",
                                "os": "linux",
                                "variant": (
                                    "v9"
                                    if remote and scenario == "replay-platform-change"
                                    else "v8"
                                ),
                            },
                        },
                    ]
                    if remote and scenario == "replay-platform-set-change":
                        manifests[1]["platform"] = {
                            "architecture": "s390x",
                            "os": "linux",
                        }
                    sys.stdout.write(
                        json.dumps(
                            {
                                "manifests": manifests,
                                "normalization": "remote" if remote else "local",
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
                    """
                ),
                encoding="utf-8",
            )
            fake_skopeo.chmod(0o700)

            result = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "BUILD_CONTEXT": ".",
                    "CONTAINER_RUN_ROOT": str(root / "run"),
                    "CONTAINER_STORAGE_ROOT": str(root / "storage"),
                    "DOCKERFILE_PATH": "Dockerfile",
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_REPOSITORY": "StreamScapeTV/example-service",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_RUN_ID": "90",
                    "GITHUB_SERVER_URL": "https://github.com",
                    "IMAGE_REFERENCE": "registry.example/example-service:1.0.4",
                    "IMAGE_SCENARIO": scenario,
                    "OCI_LAYOUT": str(root / "oci"),
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "REGISTRY_AUTH_FILE": str(root / "auth.json"),
                    "SKOPEO_CALLS": str(calls_path),
                    "SOURCE_SHA": "a" * 40,
                    "STATE_ROOT": str(state_root),
                    "VERSION": "1.0.4",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            calls = [
                json.loads(line)
                for line in calls_path.read_text(encoding="utf-8").splitlines()
            ]
            output = (
                output_path.read_text(encoding="utf-8")
                if output_path.exists()
                else ""
            )
            local_path = state_root / "image-local-index.json"
            remote_path = state_root / "image-remote-index.json"
            local_index = local_path.read_bytes() if local_path.exists() else b""
            remote_index = remote_path.read_bytes() if remote_path.exists() else b""
            return result, calls, output, local_index, remote_index

    def _run_chart_scenario(
        self, scenario: str
    ) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
        step_start = self.text.index("      - id: chart\n")
        run_marker = "        run: |\n"
        script_start = self.text.index(run_marker, step_start)
        step_end = self.text.index(
            "\n      - name: Confirm zero Actions artifacts",
            script_start,
        )
        script = textwrap.dedent(
            self.text[script_start + len(run_marker):step_end]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            state = root / "state"
            chart_source = root / "chart-source"
            chart_local = root / "chart-local"
            chart_remote = root / "chart-remote"
            for path in (fake_bin, state, chart_source, chart_local, chart_remote):
                path.mkdir()
            (chart_source / "Chart.yaml").write_text(
                "apiVersion: v2\nname: example-service\nversion: 1.0.4\n"
                "appVersion: 1.0.4\n",
                encoding="utf-8",
            )
            calls_file = root / "calls.jsonl"
            pushed = root / "pushed"

            fake_helm = fake_bin / "helm"
            fake_helm.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, shutil, sys, tarfile, tempfile
                    args = sys.argv[1:]
                    with pathlib.Path(os.environ["CALLS_FILE"]).open("a") as stream:
                        stream.write(json.dumps({"tool": "helm", "args": args}) + "\\n")
                    command = args[0]
                    if command == "lint":
                        raise SystemExit(0)
                    if command == "template":
                        print("apiVersion: v1\\nkind: ConfigMap\\nmetadata:\\n  name: release")
                        raise SystemExit(0)
                    if command == "show" and args[1] == "chart":
                        print("name: example-service\\nversion: 1.0.4\\nappVersion: 1.0.4")
                        raise SystemExit(0)
                    if command == "package":
                        destination = pathlib.Path(args[args.index("--destination") + 1])
                        package = destination / "example-service-1.0.4.tgz"
                        with tempfile.TemporaryDirectory() as directory:
                            source = pathlib.Path(directory) / "example-service"
                            source.mkdir()
                            (source / "Chart.yaml").write_text(
                                "apiVersion: v2\\nname: example-service\\n"
                                "version: 1.0.4\\nappVersion: 1.0.4\\n"
                            )
                            with tarfile.open(package, "w:gz") as archive:
                                archive.add(source, arcname="example-service")
                        raise SystemExit(0)
                    if command == "push":
                        pathlib.Path(os.environ["PUSHED_FILE"]).write_text("pushed")
                        raise SystemExit(0)
                    if command == "pull":
                        destination = pathlib.Path(args[args.index("--destination") + 1])
                        source = pathlib.Path(os.environ["CHART_LOCAL"]) / "example-service-1.0.4.tgz"
                        target = destination / source.name
                        shutil.copyfile(source, target)
                        if os.environ["SCENARIO"] == "present-conflict":
                            with target.open("ab") as stream:
                                stream.write(b"conflict")
                        raise SystemExit(0)
                    raise SystemExit(3)
                    """
                ),
                encoding="utf-8",
            )
            fake_helm.chmod(0o700)

            fake_skopeo = fake_bin / "skopeo"
            fake_skopeo.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import hashlib, json, os, pathlib, sys
                    args = sys.argv[1:]
                    with pathlib.Path(os.environ["CALLS_FILE"]).open("a") as stream:
                        stream.write(json.dumps({"tool": "skopeo", "args": args}) + "\\n")
                    scenario = os.environ["SCENARIO"]
                    if args[0] == "list-tags":
                        if scenario == "listing-failure":
                            raise SystemExit(2)
                        if scenario == "listing-malformed":
                            print("{")
                            raise SystemExit(0)
                        present = scenario.startswith("present") or pathlib.Path(
                            os.environ["PUSHED_FILE"]
                        ).exists()
                        print(json.dumps({"Tags": ["1.0.4"] if present else []}))
                        raise SystemExit(0)
                    if args[0] == "inspect" and "--raw" in args:
                        package = pathlib.Path(os.environ["CHART_LOCAL"]) / "example-service-1.0.4.tgz"
                        digest = hashlib.sha256(package.read_bytes()).hexdigest()
                        if scenario.endswith("layer-mismatch"):
                            digest = "0" * 64
                        print(json.dumps({
                            "config": {"mediaType": "application/vnd.cncf.helm.config.v1+json"},
                            "layers": [{
                                "digest": "sha256:" + digest,
                                "mediaType": "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
                            }],
                        }))
                        raise SystemExit(0)
                    raise SystemExit(3)
                    """
                ),
                encoding="utf-8",
            )
            fake_skopeo.chmod(0o700)

            output = root / "github-output"
            result = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "CALLS_FILE": str(calls_file),
                    "CHART_DEPENDENCY_COUNT": "0",
                    "CHART_LOCAL": str(chart_local),
                    "CHART_NAME": "example-service",
                    "CHART_REFERENCE": "oci://registry.example/charts/example-service",
                    "CHART_REMOTE": str(chart_remote),
                    "CHART_SOURCE": str(chart_source),
                    "CHART_NAMESPACE": "charts",
                    "GITHUB_OUTPUT": str(output),
                    "HELM_BIN": str(fake_helm),
                    "HELM_REGISTRY_CONFIG": str(root / "helm-auth.json"),
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "PUSHED_FILE": str(pushed),
                    "REGISTRY": "registry.example",
                    "REGISTRY_AUTH_FILE": str(root / "registry-auth.json"),
                    "SCENARIO": scenario,
                    "STATE_ROOT": str(state),
                    "VERSION": "1.0.4",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            calls = (
                [json.loads(line) for line in calls_file.read_text().splitlines()]
                if calls_file.exists()
                else []
            )
            return result, calls

    def test_public_workflow_is_workflow_call_only(self) -> None:
        header = self.text.split("\npermissions:", 1)[0]
        self.assertRegex(header, re.compile(r"(?m)^on:\n  workflow_call:\n"))
        for forbidden in (
            "\n  push:",
            "\n  pull_request:",
            "\n  workflow_dispatch:",
            "\n  schedule:",
            "\n  issue_comment:",
            "\n  workflow_run:",
        ):
            self.assertNotIn(forbidden, header)

    def test_public_api_is_bounded_versioned_explicit_and_product_neutral(self) -> None:
        for input_name in (
            "release_mode",
            "release_version",
            "release_source_sha",
            "image_name",
            "chart_name",
            "chart_path",
            "dockerfile_path",
            "build_context",
        ):
            self.assertRegex(
                self.text,
                re.compile(rf"(?m)^      {re.escape(input_name)}:\n"),
            )
        self.assertRegex(
            self.text,
            re.compile(
                r"(?ms)^      release_mode:\n.*?^        default: tag-push$"
            ),
        )
        for secret_name in ("registry_username", "registry_token"):
            self.assertRegex(
                self.text,
                re.compile(rf"(?m)^      {secret_name}:\n"),
            )
        for forbidden in (
            "image_recovery_authority",
            "IMAGE_RECOVERY",
            "RECOVERY_IMAGE_DIGEST",
            "Revalidate recovery image",
            "partial-publication recovery",
            "StreamScapeTV/iptv-backend",
            "31312535710",
            "06e141b4c4fb6cad701606845883996ffe12a7f8",
        ):
            self.assertNotIn(forbidden, self.text)
        for forbidden_input in (
            "registry_host",
            "runner_label",
            "container_engine",
            "cluster",
            "kubeconfig",
            "command",
            "script",
            "secret_name",
            "ref",
            "branch",
        ):
            self.assertNotRegex(
                self.text,
                re.compile(rf"(?m)^      {forbidden_input}:\n"),
            )
        self.assertNotIn("secrets: inherit", self.text)

    def test_exact_tag_authority_checkout_and_freshness_are_required(self) -> None:
        action_reference = (
            "StreamScapeTV/ci-workflows/actions/resolve-release-tag@"
            + HELPER_SHA
        )
        self.assertEqual(3, self.text.count(action_reference))
        self.assertIn("Admit exact trusted release mode and tag tuple", self.text)
        self.assertIn("Revalidate exact release tag before checkout", self.text)
        self.assertIn(
            "Revalidate exact release tag immediately before publication",
            self.text,
        )
        checkout = self.text.index("Check out exact validated caller source")
        publication = self.text.index("Authenticate to fixed private OCI registry")
        prepublication = self.text.index(
            "Revalidate exact release tag immediately before publication"
        )
        self.assertLess(prepublication, publication)
        self.assertIn("ref: ${{ needs.admit.outputs.source_sha }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"', self.text)
        self.assertIn('test -z "$(git symbolic-ref -q HEAD || true)"', self.text)
        self.assertNotIn("${{ github.sha }}", self.text[checkout:])
        self.assertNotIn("${GITHUB_SHA}", self.text[checkout:])
        self.assertIn(
            "group: tag-image-chart-${{ github.repository }}-"
            "${{ needs.admit.outputs.version }}",
            self.text,
        )
        self.assertNotIn("github.ref_name", self.text)

    def test_helper_action_and_human_release_are_exactly_locked(self) -> None:
        lock = json.loads(ACTION_LOCK.read_text(encoding="utf-8"))
        matches = [
            entry
            for entry in lock["third_party_actions"]
            if entry["uses"]
            == "StreamScapeTV/ci-workflows/actions/resolve-release-tag"
        ]
        self.assertEqual(1, len(matches))
        self.assertEqual(HELPER_SHA, matches[0]["sha"])
        self.assertEqual(
            "issue #59 immutable helper checkpoint",
            matches[0]["release"],
        )
        self.assertEqual("composite", matches[0]["runtime"])

    def test_version_and_input_validator_accepts_only_bounded_values(self) -> None:
        marker = "          python3 - <<'PY'\n"
        start = self.text.index(
            marker,
            self.text.index("Validate bounded product inputs"),
        )
        script = textwrap.dedent(
            self.text[start + len(marker):].split("\n          PY", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "chart").mkdir()
            (root / "chart" / "Chart.yaml").write_text(
                "apiVersion: v2\nname: example\nversion: 0.0.0\n",
                encoding="utf-8",
            )
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            good_env = {
                **os.environ,
                "VERSION": "1.2.3-rc.1",
                "IMAGE_NAME": "example-service",
                "CHART_NAME": "example-service",
                "CHART_PATH": "chart",
                "DOCKERFILE_PATH": "Dockerfile",
                "BUILD_CONTEXT": ".",
            }
            result = subprocess.run(
                [sys.executable, "-S", "-c", script],
                cwd=root,
                env=good_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            for name, value in (
                ("VERSION", "v1.2.3"),
                ("VERSION", "1.2.3+build"),
                ("VERSION", "latest"),
                ("IMAGE_NAME", "../escape"),
                ("CHART_NAME", "UPPER"),
                ("CHART_PATH", "../chart"),
                ("DOCKERFILE_PATH", "/Dockerfile"),
                ("BUILD_CONTEXT", ".."),
            ):
                with self.subTest(name=name, value=value):
                    env = dict(good_env)
                    env[name] = value
                    result = subprocess.run(
                        [sys.executable, "-S", "-c", script],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)

    def test_registry_runner_and_engine_are_central_fixed_contracts(self) -> None:
        self.assertIn("REGISTRY: git.faruqi.dev", self.text)
        self.assertIn("REGISTRY_NAMESPACE: mimranfaruqi", self.text)
        self.assertIn("CHART_NAMESPACE: mimranfaruqi/helm-charts", self.text)
        self.assertEqual(
            2,
            self.text.count(
                "    runs-on: [linux, amd64, buildah, high]"
            ),
        )
        self.assertNotIn("runs-on: buildah-high", self.text)
        self.assertNotIn("self-hosted", self.text)
        for marker in (
            "! command -v docker",
            "! command -v dockerd",
            "test ! -e /var/run/docker.sock",
            "buildah --version",
            "skopeo --version",
            "podman --version",
            "--storage-driver vfs",
        ):
            self.assertIn(marker, self.text)

    def test_both_modes_share_identical_publication_stages_and_outputs(self) -> None:
        publish = self.text[self.text.index("  publish:"):]
        for marker in (
            "Verify daemonless publication runner and credentials",
            "Stage pinned Helm from immutable OCI image",
            "Prepare isolated publication and authentication state",
            "Prepare locked Helm chart dependencies",
            "Authenticate to fixed private OCI registry",
            "Build publish and verify exact-tag image",
            "Package publish and verify exact-tag Helm chart",
            "Confirm zero Actions artifacts",
            "Clean publication credentials and state",
        ):
            self.assertEqual(1, publish.count(marker))
        for output in (
            "version",
            "source_sha",
            "image_reference",
            "image_digest",
            "chart_reference",
            "chart_package_sha256",
        ):
            self.assertRegex(
                self.text,
                re.compile(rf"(?m)^      {re.escape(output)}:\n"),
            )
        publication_start = publish.index(
            "Verify daemonless publication runner and credentials"
        )
        publication_body = publish[publication_start:]
        self.assertNotIn("inputs.release_mode", publication_body)
        self.assertNotIn("github.event_name", publication_body)

    def test_image_is_exact_tag_multi_platform_and_independently_read_back(self) -> None:
        self.assertIn("for architecture in amd64 arm64; do", self.text)
        self.assertIn('--platform "linux/${architecture}"', self.text)
        self.assertIn('--timestamp "${source_epoch}"', self.text)
        self.assertIn("org.opencontainers.image.source", self.text)
        self.assertIn("org.opencontainers.image.revision=${SOURCE_SHA}", self.text)
        self.assertIn("org.opencontainers.image.version", self.text)
        self.assertIn('"oci:${OCI_LAYOUT}:${VERSION}"', self.text)
        self.assertIn('"docker://${IMAGE_REFERENCE}"', self.text)
        self.assertIn("skopeo copy --all", self.text)
        self.assertIn("image platforms", self.text)
        self.assertIn("--override-arch", self.text)
        self.assertIn("--config", self.text)
        self.assertIn("--raw", self.text)
        self.assertIn('cmp -s "${local_config}" "${remote_config}"', self.text)
        self.assertNotIn("local_digest=", self.text)
        self.assertNotIn('test "${remote_digest}" = "${local_digest}"', self.text)

    def test_normalized_indexes_accept_only_identical_raw_platform_configs(self) -> None:
        result, calls, output, local_index, remote_index = self._run_image_scenario(
            "replay-match"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotEqual(local_index, remote_index)
        self.assertEqual(0, sum(call[0] == "copy" for call in calls))
        tag_lists = [call for call in calls if call[0] == "list-tags"]
        self.assertEqual(1, len(tag_lists))
        self.assertEqual(
            "docker://registry.example/example-service",
            tag_lists[0][-1],
        )
        self.assertIn("replayed=true\n", output)
        self.assertIn(
            f"digest=sha256:{hashlib.sha256(remote_index).hexdigest()}\n",
            output,
        )

        for scenario in (
            "replay-runtime-change",
            "replay-rootfs-change",
            "replay-reserialized-config",
        ):
            with self.subTest(scenario=scenario):
                result, calls, _, _, _ = self._run_image_scenario(scenario)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("image config does not match", result.stderr)
                self.assertEqual(0, sum(call[0] == "copy" for call in calls))

        result, calls, _, _, _ = self._run_image_scenario(
            "replay-platform-change"
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("platform metadata differs", result.stderr)
        self.assertEqual(0, sum(call[0] == "copy" for call in calls))

        result, calls, _, _, _ = self._run_image_scenario(
            "replay-platform-set-change"
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("image platforms", result.stderr)
        self.assertEqual(0, sum(call[0] == "copy" for call in calls))

    def test_absent_remote_tag_is_copied_once_then_verified(self) -> None:
        result, calls, output, local_index, remote_index = self._run_image_scenario(
            "publish-match"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotEqual(local_index, remote_index)
        self.assertEqual(1, sum(call[0] == "copy" for call in calls))
        self.assertEqual(1, sum(call[0] == "list-tags" for call in calls))
        self.assertIn("replayed=false\n", output)

    def test_tag_listing_failures_never_attempt_publication(self) -> None:
        for scenario in (
            "list-tags-auth-failure",
            "list-tags-transient-failure",
            "list-tags-malformed",
        ):
            with self.subTest(scenario=scenario):
                result, calls, output, _, remote_index = self._run_image_scenario(
                    scenario
                )
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(1, sum(call[0] == "list-tags" for call in calls))
                self.assertEqual(0, sum(call[0] == "copy" for call in calls))
                self.assertEqual("", output)
                self.assertEqual(b"", remote_index)

    def test_chart_builds_locked_dependencies_before_lint_and_package(self) -> None:
        prepare = self.text.index("Prepare locked Helm chart dependencies")
        dependency_build = self.text.index(
            '"${HELM_BIN}" dependency build "${CHART_SOURCE}"',
            prepare,
        )
        chart_step = self.text.index(
            "Package publish and verify exact-tag Helm chart",
            dependency_build,
        )
        lint = self.text.index('"${HELM_BIN}" lint "${CHART_SOURCE}"', chart_step)
        package = self.text.index('"${HELM_BIN}" package "${CHART_SOURCE}"', chart_step)
        self.assertLess(prepare, dependency_build)
        self.assertLess(dependency_build, lint)
        self.assertLess(lint, package)

        for marker in (
            "HELM_REPOSITORY_CONFIG",
            "HELM_REPOSITORY_CACHE",
            "Chart.lock is required when Chart.yaml declares dependencies",
            "Unsupported Helm dependency repository scheme",
            "dependency build",
            "--skip-refresh",
            'test "${lock_sha_after}" = "${lock_sha_before}"',
            "CHART_DEPENDENCY_COUNT",
            "local_dependency_entries",
            "remote_dependency_entries",
        ):
            self.assertIn(marker, self.text)

    def test_dependency_validator_accepts_locked_https_and_oci_only(self) -> None:
        marker = "          python3 - <<'PY'\n"
        start = self.text.index(
            marker,
            self.text.index("Prepare locked Helm chart dependencies"),
        )
        script = textwrap.dedent(
            self.text[start + len(marker):].split("\n          PY", 1)[0]
        )

        def run_case(
            directory: Path,
            dependency_output: str,
            *,
            with_lock: bool,
        ) -> subprocess.CompletedProcess[str]:
            chart = directory / "chart"
            chart.mkdir(parents=True, exist_ok=True)
            if with_lock:
                (chart / "Chart.lock").write_text(
                    "dependencies: []\ndigest: sha256:test\ngenerated: now\n",
                    encoding="utf-8",
                )
            dependency_file = directory / "dependencies.txt"
            repository_file = directory / "repositories.txt"
            count_file = directory / "count.txt"
            dependency_file.write_text(dependency_output, encoding="utf-8")
            env = {
                **os.environ,
                "CHART_SOURCE": str(chart),
                "DEPENDENCY_LIST": str(dependency_file),
                "DEPENDENCY_REPOSITORIES": str(repository_file),
                "DEPENDENCY_COUNT_FILE": str(count_file),
            }
            return subprocess.run(
                [sys.executable, "-S", "-c", script],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        header = "NAME VERSION REPOSITORY STATUS\n"
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            result = run_case(
                directory / "https",
                header + "valkey 0.11.0 https://valkey.io/valkey-helm/ missing\n",
                with_lock=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / "https" / "repositories.txt").read_text(),
                "https://valkey.io/valkey-helm/\n",
            )
            self.assertEqual(
                (directory / "https" / "count.txt").read_text(),
                "1\n",
            )

            result = run_case(
                directory / "oci",
                header + "shared 1.2.3 oci://registry.example/charts missing\n",
                with_lock=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / "oci" / "repositories.txt").read_text(),
                "",
            )

            result = run_case(
                directory / "no-lock",
                header + "valkey 0.11.0 https://valkey.io/valkey-helm/ missing\n",
                with_lock=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Chart.lock is required", result.stderr)

            for repository in (
                "http://valkey.example/charts",
                "file://../escape",
                "git+https://example.invalid/charts",
                "https://user:password@example.invalid/charts",
                "https://example.invalid/charts?token=secret",
            ):
                with self.subTest(repository=repository):
                    result = run_case(
                        directory / ("bad-" + str(abs(hash(repository)))),
                        header + f"bad 1.2.3 {repository} missing\n",
                        with_lock=True,
                    )
                    self.assertNotEqual(result.returncode, 0)

            result = run_case(
                directory / "none",
                header,
                with_lock=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / "none" / "count.txt").read_text(),
                "0\n",
            )

    def test_chart_uses_tag_version_and_remote_package_readback(self) -> None:
        self.assertIn('--version "${VERSION}"', self.text)
        self.assertIn('--app-version "${VERSION}"', self.text)
        self.assertIn('--set-string image.tag="${VERSION}"', self.text)
        self.assertIn('"${HELM_BIN}" push "${package}"', self.text)
        self.assertIn('"${HELM_BIN}" pull "${CHART_REFERENCE}"', self.text)
        self.assertIn('test "$(sha256sum "${remote_package}"', self.text)
        self.assertIn('grep -Fqx "version: ${VERSION}"', self.text)
        self.assertIn("Rendered chart contains forbidden latest identity", self.text)

    def test_chart_listing_present_absent_and_one_push_are_fail_closed(self) -> None:
        result, calls = self._run_chart_scenario("present")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            0,
            sum(
                call["tool"] == "helm" and call["args"][0] == "push"
                for call in calls
            ),
        )
        self.assertEqual(
            1,
            sum(
                call["tool"] == "skopeo" and call["args"][0] == "list-tags"
                for call in calls
            ),
        )

        result, calls = self._run_chart_scenario("absent")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            1,
            sum(
                call["tool"] == "helm" and call["args"][0] == "push"
                for call in calls
            ),
        )
        self.assertEqual(
            2,
            sum(
                call["tool"] == "skopeo" and call["args"][0] == "list-tags"
                for call in calls
            ),
        )

        for scenario in (
            "listing-failure",
            "listing-malformed",
            "present-conflict",
            "present-layer-mismatch",
        ):
            with self.subTest(scenario=scenario):
                result, calls = self._run_chart_scenario(scenario)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(
                    0,
                    sum(
                        call["tool"] == "helm" and call["args"][0] == "push"
                        for call in calls
                    ),
                )

    def test_replay_is_idempotent_and_conflicts_fail_closed(self) -> None:
        image_start = self.text.index("      - id: image\n")
        image_end = self.text.index("\n      - id: chart\n", image_start)
        image = self.text[image_start:image_end]
        self.assertIn("skopeo list-tags", image)
        self.assertIn(
            'print("true" if os.environ["VERSION"] in tags else "false")',
            image,
        )
        self.assertNotIn("if skopeo inspect", image)
        self.assertIn('if ! cmp -s "${local_config}" "${remote_config}"', image)
        self.assertIn('test "$(sha256sum "${remote_package}"', self.text)
        self.assertIn("replayed=%s", self.text)

    def test_no_latest_manual_release_github_release_or_deployment(self) -> None:
        lower = self.text.lower()
        for forbidden in (
            "workflow_dispatch:",
            ":latest",
            "github release",
            "/releases",
            "repository_dispatch",
            "flux",
            "kubectl",
            "kubeconfig",
            "sops",
            "helm upgrade",
            "helm install",
            "rollout restart",
            "actions/upload-artifact",
            "buildx",
            "dind",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lower)
        self.assertIn("Deployment/rollout: not performed", self.text)

    def test_zero_artifacts_auth_isolated_and_cleanup_fail_closed(self) -> None:
        self.assertIn("actions: read", self.text)
        self.assertIn("contents: read", self.text)
        self.assertIn("chmod 0600", self.text)
        self.assertIn("zero artifacts", self.text)
        self.assertIn("if: always()", self.text)
        self.assertIn("cleanup_failed=0", self.text)
        self.assertIn('exit "${cleanup_failed}"', self.text)
        self.assertNotIn("exit 0", self.text)
        self.assertIn("containers -q", self.text)
        self.assertIn("images -q", self.text)
        self.assertIn("registry logout", self.text)
        self.assertIn('"${STATE_ROOT:-${RUNNER_TEMP}/central-tag-release}"', self.text)
        self.assertIn('"${buildah_cmd[@]}" rm --all', self.text)
        self.assertIn('"${buildah_cmd[@]}" rmi --all --force', self.text)

    def test_state_prepares_valid_private_json_auth_files(self) -> None:
        prepare_start = self.text.index(
            "Prepare isolated publication and authentication state"
        )
        prepare_end = self.text.index(
            "\n      - name: Prepare locked Helm chart dependencies",
            prepare_start,
        )
        prepare = self.text[prepare_start:prepare_end]
        self.assertIn('registry_auth="${state_root}/auth/containers.json"', prepare)
        self.assertIn('helm_auth="${state_root}/auth/helm.json"', prepare)
        self.assertEqual(
            2,
            prepare.count('"$(dirname "${registry_auth}")"'),
        )
        self.assertIn("chmod 0700", prepare)
        self.assertIn("printf '{}\\n' > \"${registry_auth}\"", prepare)
        self.assertIn("printf '{}\\n' > \"${helm_auth}\"", prepare)
        self.assertIn('chmod 0600 "${registry_auth}" "${helm_auth}"', prepare)
        self.assertNotIn(': > "${registry_auth}"', prepare)
        self.assertNotIn(': > "${helm_auth}"', prepare)
        self.assertNotIn('touch "${registry_auth}"', prepare)
        self.assertNotIn('touch "${helm_auth}"', prepare)

        script_marker = "        run: |\n"
        script_start = self.text.index(script_marker, prepare_start)
        script = textwrap.dedent(
            self.text[script_start + len(script_marker):prepare_end]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner_temp = root / "runner"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_buildah = fake_bin / "buildah"
            fake_buildah.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_buildah.chmod(0o700)
            fake_timeout = fake_bin / "timeout"
            fake_timeout.write_text(
                "#!/bin/sh\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    --signal=*|--kill-after=*) shift ;;\n"
                "    *s) shift; break ;;\n"
                "    *) break ;;\n"
                "  esac\n"
                "done\n"
                "exec \"$@\"\n",
                encoding="utf-8",
            )
            fake_timeout.chmod(0o700)
            github_env = root / "github-env"
            result = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "GITHUB_ENV": str(github_env),
                    "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_RUN_ID": "83",
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "RUNNER_TEMP": str(runner_temp),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            auth_root = runner_temp / "central-tag-release" / "auth"
            self.assertEqual(0o700, auth_root.stat().st_mode & 0o777)
            for filename in ("containers.json", "helm.json"):
                auth_file = auth_root / filename
                with self.subTest(filename=filename):
                    self.assertEqual({}, json.loads(auth_file.read_text()))
                    self.assertEqual(0o600, auth_file.stat().st_mode & 0o777)

        login_start = self.text.index("Authenticate to fixed private OCI registry")
        login_end = self.text.index(
            "Build publish and verify exact-tag image",
            login_start,
        )
        login = self.text[login_start:login_end]
        self.assertIn("umask 077", login)
        buildah_login = login.index("buildah login")
        registry_nonempty = login.index('test -s "${REGISTRY_AUTH_FILE}"')
        registry_mode = login.index('chmod 0600 "${REGISTRY_AUTH_FILE}"')
        helm_login = login.index('"${HELM_BIN}" registry login')
        helm_nonempty = login.index('test -s "${HELM_REGISTRY_CONFIG}"')
        helm_mode = login.index('chmod 0600 "${HELM_REGISTRY_CONFIG}"')
        self.assertLess(buildah_login, registry_nonempty)
        self.assertLess(registry_nonempty, registry_mode)
        self.assertLess(helm_login, helm_nonempty)
        self.assertLess(helm_nonempty, helm_mode)

    def test_self_check_and_documented_callers_are_thin(self) -> None:
        self.assertIn(
            '"${VERIFIED_PYTHON}" -m unittest discover -s tests -p \'test_*.py\' -v',
            self.self_check,
        )
        self.assertNotIn(
            '"${VERIFIED_PYTHON}" -m unittest -v tests/test_reusable_tag_image_chart.py',
            self.self_check,
        )
        self.assertIn("Confirm zero Actions artifacts", self.self_check)
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/"
            "reusable-tag-image-chart.yml@",
            self.readme,
        )
        self.assertIn("tags:", self.readme)
        self.assertIn("release_mode: existing-tag", self.readme)
        self.assertIn("release_version:", self.readme)
        self.assertIn("release_source_sha:", self.readme)
        self.assertIn("registry_username:", self.readme)
        self.assertIn("registry_token:", self.readme)
        self.assertIn("git tag 1.2.3 <commit>", self.readme)
        self.assertNotIn("image_recovery_authority", self.readme)
        self.assertNotIn("secrets: inherit\n", self.readme)


if __name__ == "__main__":
    unittest.main()
