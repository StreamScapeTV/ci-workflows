# Canonical validation harness

`python3 scripts/ci/validation_harness.py --root .` is the repository-wide static gate for GitHub Actions workflows, composite actions, named Python functions, public API contracts, caller fixtures, event fixtures, and mocked service scenarios.

The harness uses PyYAML through a narrowed YAML 1.2-style boolean resolver so the GitHub Actions `on` key remains literal. Validation dependencies are declared in `requirements/validation.txt` and installed into a run-local target by self-CI. There is no action/tool SHA registry, digest-locked parser bootstrap, release-comment checkpoint registry, or approved-local-action prefix registry.

## Policy surface

The gate keeps functional and privacy-sensitive checks: valid workflow/action syntax, bounded runner selection, public API agreement, explicit permissions and secrets, no `secrets: inherit`, protection against privileged execution of untrusted source, credential-safe checkout, required cleanup for credentials/private state, call-graph correctness, source identity where a workflow functionally requires it, release-tag/read-back correctness for publication, and deterministic repository contracts.

Ordinary action references are not required to appear in a global allowlist or to use a repository-registered SHA/comment. First-party reusable workflows may follow the repository's active `@main` channel during development. A specific release workflow may still require immutable source where that is part of the release's functional identity.

There is no blanket Central ban on Actions artifacts for public/non-private workflows. A workflow may retain an artifact when the feature requires it. Private-source Central runs remain strict: private source, configuration and command output must not be exposed through public logs, summaries, or Actions artifacts.

## Named command and readability contracts

`contracts/ciw-commands.json` remains the checked-in command registry. `contracts/readability-policy.json` keeps maintainability bounds for workflow/action structure. These are implementation/navigation contracts, not supply-chain registries.

## Automatic tests

The self-check runs `"${VERIFIED_PYTHON}" -m unittest discover -s tests -p 'test_*.py' -v`. New focused suites under `tests/test_*.py` or nested `tests/**/test_*.py` are included without workflow edits. Tests remain hermetic and do not require private credentials, Agent State mutation, Kubernetes authority, signing, or devices unless a dedicated workflow owns that capability.

## Extending the harness

A new reusable workflow adds its public API record, documentation, implementation component, representative fixtures, cleanup behavior where functionally required, and focused tests. It does not add a row to a global action/tool/checkpoint registry. Reuse existing domain providers and prefer one coherent implementation owner over parallel policy layers.
