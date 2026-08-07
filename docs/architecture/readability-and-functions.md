# Readability and named functions

Generated from `contracts/readability-policy.json`. Do not edit directly.

## Reviewed limits

| Constraint | Maximum |
|---|---:|
| Public reusable-workflow depth | 1 |
| Internal leaf reusable children | 0 |
| Local composite-action depth | 1 |
| Public workflow jobs | 7 |
| Inline non-empty `run:` lines | 40 |
| Duplicate-block threshold lines | 8 |
| Complex-loop threshold lines | 12 |
| Matrix jobs | 16 |

Shell function definitions in workflow YAML are forbidden. Non-trivial behavior belongs in typed, tested Python functions.

## Stable execution shape

`consumer trigger → public reusable workflow → named composite action or ciw function`

Public reusable-workflow depth remains one. Internal leaf workflows may not call another reusable workflow. Local composite actions do not nest another local composite action.

## Before and after

Avoid opaque embedded programs:

```yaml
- name: Run
  run: |
    # dozens of lines of parsing, branching, cleanup and output logic
```

Prefer an intent-named adapter:

```yaml
- name: Verify deterministic repository policy
  uses: StreamScapeTV/ci-workflows/actions/verify-repository-policy@<immutable-ref>
```

## Rejected generic control surfaces

`arbitrary_command`, `callback`, `callback_url`, `container_engine`, `deletion_path`, `function_name`, `handler`, `module_name`, `runner`, `runner_labels`, `runs_on`, `secret_name`, `shell`, `workspace_root`.

## Reviewed exceptions

### `issue-34-bootstrap-publisher`

- Issue: #34
- Path: `.github/workflows/reusable-tag-image-chart.yml`
- Rules: `caller-cancelling-concurrency`, `complex-yaml-logic`, `oversized-inline-run`, `public-api-doc-drift`
- Reason: The bounded bootstrap publisher predates the named-function architecture and retains only its existing reviewed exceptions.
- Removal condition: Remove with the contracted replacement for the deprecated bootstrap publication workflow.
- Regression tests: `tests/test_reusable_tag_image_chart.py`, `tests/test_validation_harness.py`

### `issue-37-legacy-agent-state-run`

- Issue: #37
- Path: `src/ci_workflows/agent_state_command.py`
- Rules: `opaque-function-name`
- Reason: The protected temporary Agent State compatibility transport predates the named-function convergence and exposes its tested legacy run entry point. Issue #31 does not rename or absorb Agent State transport semantics.
- Removal condition: Remove when the temporary #37 compatibility transport is retired after canonical Agent State cutover and legacy-consumer retirement.
- Regression tests: `tests/test_agent_state_command.py`, `tests/test_readability_contract.py`
