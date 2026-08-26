from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_DOC = ROOT / "docs/architecture/android-validation.md"
WORKFLOW_DOC = ROOT / "docs/workflows/android.md"
WORKFLOW = ROOT / ".github/workflows/reusable-android.yml"

VALIDATE_ANDROID = "StreamScapeTV/ci-workflows/actions/validate-android"
WARM_GRADLE = "StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies"
UPLOAD_GRADLE_SEED = "StreamScapeTV/ci-workflows/actions/upload-gradle-seed"
EXACT_CHECKOUT = "StreamScapeTV/ci-workflows/actions/exact-checkout"
PREPARE_WORKSPACE = "StreamScapeTV/ci-workflows/actions/prepare-workspace"
PRIVATE_DEPENDENCY = "StreamScapeTV/ci-workflows/actions/checkout-private-dependency"
RENDER_EVIDENCE = "StreamScapeTV/ci-workflows/actions/render-evidence"
CLEANUP_WORKSPACE = "StreamScapeTV/ci-workflows/actions/cleanup-workspace"


class AndroidArchitectureDocumentationTests(unittest.TestCase):
    def test_helper_guidance_uses_shared_main_library_and_keeps_functional_boundaries(self) -> None:
        required = {
            VALIDATE_ANDROID,
            WARM_GRADLE,
            UPLOAD_GRADLE_SEED,
            EXACT_CHECKOUT,
            PREPARE_WORKSPACE,
            PRIVATE_DEPENDENCY,
            RENDER_EVIDENCE,
            CLEANUP_WORKSPACE,
        }
        guide = ARCHITECTURE_DOC.read_text(encoding="utf-8")
        workflow_guide = WORKFLOW_DOC.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")

        for action_name in required:
            self.assertIn(f"uses: {action_name}@main", workflow)

        self.assertIn("current Central library through `@main`", guide)
        self.assertIn("no per-action checkpoint registry or action lock", guide)
        self.assertNotIn("## Helper checkpoints", guide)
        self.assertNotIn("action-tool-lock", guide)
        self.assertNotIn("immutable private-action checkpoint", guide)

        self.assertNotIn("id-token", workflow)
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST", workflow)
        self.assertIn("GRADLE_RO_DEP_CACHE", guide)
        self.assertIn("private writable Gradle home", guide)
        self.assertIn("dependency-only bootstrap used only by cache maintenance", guide)
        self.assertIn("one best-effort post-execution cache-sync call", guide)
        self.assertIn("does not invoke Gradle", guide)
        self.assertIn("Registered workspace cleanup always", guide)
        self.assertIn("Central owns the maintenance promotion and normal post-execution sync", guide)
        self.assertIn("Central owns the cache-maintenance warm/promotion and the normal post-execution sync", workflow_guide)
        self.assertIn("Android product repositories", workflow_guide)
        self.assertIn("do **not** run a separate dependency-warm Gradle invocation first", workflow_guide)
        self.assertNotIn("first best-effort cache-sync call", guide)
        self.assertNotIn("second best-effort cache-sync call", guide)
        self.assertNotIn("warms every `protected-full`", guide)


if __name__ == "__main__":
    unittest.main()
