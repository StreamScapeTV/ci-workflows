from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.dependency_cache import DependencyCacheError, plan_dependency_cache

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/dependency-cache/action.yml"
CONTRACT = ROOT / "contracts/cache-policy.json"


class DependencyCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.state = self.base / "state"
        self.home = self.state / "home"
        self.npm = self.state / "npm/cache"
        self.xdg = self.state / "xdg-cache"
        self.gradle = self.state / "gradle"
        self.pub = self.state / "pub"
        self.helm = self.state / "helm-cache"
        for path in (self.source, self.state, self.home, self.npm, self.xdg, self.gradle, self.pub, self.helm):
            path.mkdir(parents=True, exist_ok=True)
        self.event = self.base / "event.json"
        self.event.write_text(
            json.dumps({"repository": {"full_name": "StreamScapeTV/example", "default_branch": "main"}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def environment(self, *, event_name: str = "pull_request", ref: str = "refs/pull/7/merge", protected: str = "false") -> dict[str, str]:
        return {
            "GITHUB_REPOSITORY": "StreamScapeTV/example",
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(self.event),
            "GITHUB_REF": ref,
            "GITHUB_REF_PROTECTED": protected,
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "CI_WORKFLOW_ROOT": str(self.state),
            "HOME": str(self.home),
            "npm_config_cache": str(self.npm),
            "XDG_CACHE_HOME": str(self.xdg),
            "GRADLE_USER_HOME": str(self.gradle),
            "PUB_CACHE": str(self.pub),
            "HELM_CACHE_HOME": str(self.helm),
        }

    def plan(self, *, phase: str = "restore", family: str, trust: str = "trusted-pr", profile: str = "quality", succeeded: bool = False, environment: dict[str, str] | None = None):
        return plan_dependency_cache(
            contract_root=ROOT,
            source_root=self.source,
            working_directory=".",
            phase=phase,
            family=family,
            source_trust=trust,
            profile=profile,
            validation_succeeded=succeeded,
            environment=environment or self.environment(),
        )

    def test_npm_key_reuses_unchanged_lock_across_source_changes(self) -> None:
        (self.source / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
        first = self.plan(family="npm")
        (self.source / "unrelated.txt").write_text("new source commit content\n", encoding="utf-8")
        second = self.plan(family="npm")
        self.assertEqual(first.cache_key, second.cache_key)
        self.assertEqual(first.lock_digest, second.lock_digest)
        self.assertFalse(first.save_allowed)
        self.assertEqual(first.identity_file_count, 1)

    def test_exact_lock_change_changes_cache_key_without_restore_prefix(self) -> None:
        lock = self.source / "package-lock.json"
        lock.write_text('{"lockfileVersion":3,"packages":{}}\n', encoding="utf-8")
        first = self.plan(family="npm")
        lock.write_text('{"lockfileVersion":3,"packages":{"x":{}}}\n', encoding="utf-8")
        second = self.plan(family="npm")
        self.assertNotEqual(first.cache_key, second.cache_key)
        self.assertEqual(len(first.lock_digest), 64)
        action = ACTION.read_text(encoding="utf-8")
        self.assertNotIn("restore-keys:", action)

    def test_only_successful_protected_default_branch_push_may_save(self) -> None:
        (self.source / "package-lock.json").write_text("{}\n", encoding="utf-8")
        integration = self.environment(event_name="push", ref="refs/heads/main", protected="true")
        self.assertTrue(self.plan(phase="save", family="npm", trust="trusted-exact", succeeded=True, environment=integration).save_allowed)
        self.assertFalse(self.plan(phase="save", family="npm", trust="trusted-exact", succeeded=False, environment=integration).save_allowed)
        unprotected = self.environment(event_name="push", ref="refs/heads/main", protected="false")
        self.assertFalse(self.plan(phase="save", family="npm", trust="trusted-exact", succeeded=True, environment=unprotected).save_allowed)

    def test_save_rejects_pr_trust_and_unknown_phase(self) -> None:
        (self.source / "package-lock.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(DependencyCacheError, "cache_save_trust_forbidden"):
            self.plan(phase="save", family="npm", trust="trusted-pr", succeeded=True)
        with self.assertRaisesRegex(DependencyCacheError, "cache_phase_unsupported"):
            self.plan(phase="noop", family="npm")

    def test_cache_paths_are_marker_bound_dependency_state_not_source(self) -> None:
        (self.source / "package-lock.json").write_text("{}\n", encoding="utf-8")
        plan = self.plan(family="npm")
        self.assertEqual(plan.cache_paths, (str(self.npm),))
        for path in plan.cache_paths:
            self.assertTrue(str(path).startswith(str(self.state)))
            self.assertFalse(str(path).startswith(str(self.source)))

    def test_gradle_identity_covers_wrapper_and_dependency_build_files(self) -> None:
        wrapper = self.source / "gradle/wrapper/gradle-wrapper.properties"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("distributionUrl=https://example.invalid/gradle.zip\n", encoding="utf-8")
        build = self.source / "app/build.gradle.kts"
        build.parent.mkdir()
        build.write_text("dependencies {}\n", encoding="utf-8")
        first = self.plan(family="gradle", profile="android")
        build.write_text('dependencies { implementation("example:x:1") }\n', encoding="utf-8")
        second = self.plan(family="gradle", profile="android")
        self.assertNotEqual(first.cache_key, second.cache_key)
        self.assertEqual(first.identity_file_count, 2)
        self.assertEqual(len(first.cache_paths), 2)

    def test_python_pub_maven_and_helm_families_are_bounded(self) -> None:
        fixtures = {
            "pip": ("requirements.txt", "pytest==9.0.0\n"),
            "pub": ("pubspec.lock", "packages: {}\n"),
            "maven": ("pom.xml", "<project/>\n"),
            "helm": ("Chart.lock", "dependencies: []\n"),
        }
        for family, (relative, content) in fixtures.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            plan = self.plan(family=family)
            self.assertTrue(plan.cache_paths)
            self.assertEqual(len(plan.lock_digest), 64)
            path.unlink()

    def test_missing_identity_and_symlink_identity_fail_closed(self) -> None:
        with self.assertRaisesRegex(DependencyCacheError, "cache_identity_missing"):
            self.plan(family="npm")
        target = self.base / "outside-lock.json"
        target.write_text("{}\n", encoding="utf-8")
        (self.source / "package-lock.json").symlink_to(target)
        with self.assertRaisesRegex(DependencyCacheError, "cache_identity_symlink"):
            self.plan(family="npm")

    def test_cache_path_outside_workflow_state_fails_closed(self) -> None:
        (self.source / "package-lock.json").write_text("{}\n", encoding="utf-8")
        environment = self.environment()
        environment["npm_config_cache"] = str(self.base / "outside-npm-cache")
        with self.assertRaisesRegex(DependencyCacheError, "cache_path_outside_workflow_state"):
            self.plan(family="npm", environment=environment)

    def test_action_uses_separate_restore_and_save_at_reviewed_sha(self) -> None:
        source = ACTION.read_text(encoding="utf-8")
        sha = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
        self.assertIn(f"actions/cache/restore@{sha}", source)
        self.assertIn(f"actions/cache/save@{sha}", source)
        self.assertIn("INPUT_PHASE", source)
        self.assertIn("validation_succeeded", source)
        self.assertIn("restored_cache_hit", source)
        self.assertIn("steps.plan.outcome == 'success'", source)
        self.assertIn("GITHUB_STEP_SUMMARY", source)
        self.assertNotIn("upload-artifact", source)

    def test_contract_uses_github_retention_and_all_supported_families(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        native = contract["native_dependency_cache"]
        self.assertEqual(contract["retention_policy"], "github-native")
        self.assertFalse(contract["custom_eviction_workflow"])
        self.assertTrue(native["exact_key_only"])
        self.assertNotIn("source_sha", native["key_material"])
        self.assertEqual(set(native["families"]), {"npm", "gradle", "maven", "pip", "pub", "helm"})


if __name__ == "__main__":
    unittest.main()
