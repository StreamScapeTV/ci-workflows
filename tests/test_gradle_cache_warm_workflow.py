from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-android.yml"


def test_gradle_maintenance_scope_is_one_warm_and_one_required_promotion() -> None:
    text = WORKFLOW.read_text()

    assert "inputs.validation_scope == 'gradle'" in text
    assert "steps.plan.outputs.private_dependency_used == 'true'" in text
    assert "name: Resolve Gradle dependency graph before protected build or cache maintenance" in text
    assert "uses: StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies@" in text
    assert "uses: StreamScapeTV/ci-workflows/actions/upload-gradle-seed@" in text

    warm_index = text.index("- id: dependency_warm")
    upload_index = text.index("- id: dependency_warm_seed")
    cleanup_index = text.index("- id: android_cleanup")
    assert warm_index < upload_index < cleanup_index

    terminal_block = text[text.index("- id: terminal") :]
    assert "MAINTENANCE_MODE" in terminal_block
    assert "WARM_SEED_OUTCOME" in terminal_block
    assert 'test "${WARM_SEED_OUTCOME}" = "success" || warm_ok=false' in terminal_block


def test_gradle_maintenance_scope_skips_product_execute_and_second_sync() -> None:
    text = WORKFLOW.read_text()

    execute_block = text[text.index("- id: execute") : text.index("- id: evidence")]
    evidence_block = text[text.index("- id: evidence") : text.index("- id: android_cleanup")]
    final_sync_block = text[text.index("- id: gradle_seed") : text.index("- id: workspace_cleanup")]

    maintenance_exclusion = "inputs.validation_scope != 'gradle' || steps.plan.outputs.private_dependency_used != 'true'"
    assert maintenance_exclusion in execute_block
    assert maintenance_exclusion in evidence_block
    assert maintenance_exclusion in final_sync_block

    terminal_block = text[text.index("- id: terminal") :]
    assert "EXECUTE_REQUIRED" in terminal_block
    assert "execute_ok=true" in terminal_block
    assert 'test "${EXECUTE_OUTCOME}" = "success" || execute_ok=false' in terminal_block


def test_compile_scope_does_not_force_a_second_dependency_resolution_pass() -> None:
    text = WORKFLOW.read_text()
    warm_block = text[text.index("- id: dependency_warm") : text.index("- id: dependency_warm_seed")]
    assert "inputs.validation_scope == 'compile'" not in warm_block
