# Canonical validation harness

`python3 scripts/ci/validation_harness.py --root .` is the repository-wide static gate for GitHub Actions workflows, composite actions, named Python functions, shell/Python adapters, public API contracts, caller fixtures, event fixtures, and mocked service scenarios.

The harness uses PyYAML through a narrowed YAML 1.2-style boolean resolver so the GitHub Actions `on` key remains the literal string `on`. Both `.yml` and `.yaml` are discovered. Source-shape checks are reserved for action release comments, exact expressions, and readability limits that semantic parsing cannot retain.

## Policy surface

The gate fails closed on unpinned or unapproved actions, missing release comments, unapproved internal action paths, workflow/composite cycles, missing nested dependencies, excessive reusable-workflow depth, internal leaves that call another workflow, oversized or duplicated inline implementations, opaque names, dynamic matrices, generic runners, missing timeouts, implicit permissions, `secrets: inherit`, unsafe high-risk event checkout, mutable checkout, missing exact-head assertions, pull-request publication, `latest`, missing remote read-back, unregistered artifacts, missing cleanup, public API drift, and invalid thin callers.

`contracts/action-tool-lock.json` owns action SHAs, human-readable releases, runtime generations, approved internal-action roots, the exact parser version, and the parser source digest. `contracts/validation-harness.json` owns thresholds, semantic runner bootstrap profiles, required fixture coverage, and narrowly documented temporary exceptions. Runner contract files containing `runner` in their filename are discovered automatically so issue-specific capability work extends the gate without changing `self-check.yml`.

## Automatic tests

The self-check runs `python -m unittest discover -s tests -p 'test_*.py' -v`. New focused suites under `tests/test_*.py` or nested `tests/**/test_*.py` are therefore included without workflow edits. Tests must be hermetic and must not require registry, Agent State mutation, device, signing, SOPS, Kubernetes, or product credentials.

## Extending the harness

A new reusable workflow must add its public API record, checked-in documentation, named implementation component, positive caller fixture, negative policy fixture, cleanup assertion, and focused `test_*.py` suite. Add policy behavior as a named tested function in `src/ci_workflows/validation_harness.py`; do not grow `self-check.yml` with capability-specific commands.

The normal self-check retains no artifacts. Its JSON summary is written under the runner temporary directory, printed to the log, and removed with the temporary virtual environment.
