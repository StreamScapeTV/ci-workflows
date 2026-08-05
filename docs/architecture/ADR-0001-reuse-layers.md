# ADR-0001: Shallow reusable workflow architecture

- Status: Accepted
- Date: 2026-08-05

## Context

The organization currently repeats event parsing, runner labels, tool setup, authentication, image/chart logic, cleanup, and security policy in many repositories. Large embedded shell/Python blocks and deeply nested reusable workflows are difficult to understand, test, and update.

## Decision

Use four layers with a maximum normal call path of consumer caller -> public reusable workflow -> composite action or named function.

Public reusable workflows own jobs, permissions, secrets, matrices, runner profiles, and outputs. They live directly under `.github/workflows/reusable-*.yml`.

One optional internal reusable-workflow leaf layer may be used only when multi-job orchestration cannot remain readable otherwise. Internal leaves use `.github/workflows/internal-*.yml` and do not call another workflow.

Composite actions contain bounded step glue. Non-trivial logic lives as named typed functions under `src/ci_workflows/` with unit tests, explicit side effects, redaction, and cleanup semantics.

Consumers keep event triggers, minimum permissions, concurrency/environments, bounded product/project identifiers, and product-owned scripts or policy.

## Authority boundaries

Central workflow implementation does not transfer domain authority. Agent State API still decides sessions and claims. Flux still owns desired state, allowlists, credentials, and live policy. Product repositories still own build commands and product assertions.

## Consequences

A shared change is made once. Workflow YAML remains an ordered narrative. Public API compatibility and call depth can be tested. Consumers cannot choose implementation details such as concrete runner labels or Docker versus Buildah.
