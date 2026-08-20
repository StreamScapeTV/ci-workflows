from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-android.yml"


def test_compile_scope_runs_dependency_warm_before_validation() -> None:
    text = WORKFLOW.read_text()

    assert "inputs.validation_scope == 'protected-full' || inputs.validation_scope == 'compile'" in text
    assert "name: Resolve Gradle dependency graph before Android validation" in text
    assert "uses: StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies@" in text
    assert "uses: StreamScapeTV/ci-workflows/actions/upload-gradle-seed@" in text

    warm_index = text.index("- id: dependency_warm")
    upload_index = text.index("- id: dependency_warm_seed")
    execute_index = text.index("- id: execute")
    assert warm_index < upload_index < execute_index


def test_compile_scope_requires_successful_warm_before_execute() -> None:
    text = WORKFLOW.read_text()

    execute_block = text[text.index("- id: execute") : text.index("- id: evidence")]
    assert "inputs.validation_scope != 'compile'" in execute_block
    assert "steps.dependency_warm.outcome == 'success'" in execute_block

    terminal_block = text[text.index("- id: terminal") :]
    assert "inputs.validation_scope == 'compile'" in terminal_block
    assert "WARM_OUTCOME" in terminal_block
