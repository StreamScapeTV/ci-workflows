from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-android.yml"


def test_gradle_maintenance_scope_warms_private_dependency_before_validation() -> None:
    text = WORKFLOW.read_text()

    assert "inputs.validation_scope == 'gradle'" in text
    assert "steps.plan.outputs.private_dependency_used == 'true'" in text
    assert "name: Resolve Gradle dependency graph before Android validation" in text
    assert "uses: StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies@" in text
    assert "uses: StreamScapeTV/ci-workflows/actions/upload-gradle-seed@" in text

    warm_index = text.index("- id: dependency_warm")
    upload_index = text.index("- id: dependency_warm_seed")
    execute_index = text.index("- id: execute")
    assert warm_index < upload_index < execute_index


def test_gradle_maintenance_scope_requires_successful_warm_before_execute() -> None:
    text = WORKFLOW.read_text()

    execute_block = text[text.index("- id: execute") : text.index("- id: evidence")]
    assert "inputs.validation_scope != 'gradle'" in execute_block
    assert "steps.plan.outputs.private_dependency_used != 'true'" in execute_block
    assert "steps.dependency_warm.outcome == 'success'" in execute_block

    terminal_block = text[text.index("- id: terminal") :]
    assert "inputs.validation_scope == 'gradle'" in terminal_block
    assert "steps.plan.outputs.private_dependency_used == 'true'" in terminal_block
    assert "WARM_OUTCOME" in terminal_block


def test_compile_scope_does_not_force_a_second_dependency_resolution_pass() -> None:
    text = WORKFLOW.read_text()
    warm_block = text[text.index("- id: dependency_warm") : text.index("- id: dependency_warm_seed")]
    assert "inputs.validation_scope == 'compile'" not in warm_block
