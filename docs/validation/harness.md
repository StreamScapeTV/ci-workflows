# Canonical validation harness

`python3 scripts/ci/validation_harness.py --root .` is the repository-wide static gate for GitHub Actions workflows, composite actions, named Python functions, shell/Python adapters, public API contracts, caller fixtures, event fixtures, and mocked service scenarios.

The harness uses PyYAML through a narrowed YAML 1.2-style boolean resolver so the GitHub Actions `on` key remains the literal string `on`. Both `.yml` and `.yaml` are discovered. Source-shape checks are reserved for action release comments, exact expressions, and readability limits that semantic parsing cannot retain.

## Policy surface

The gate fails closed on unpinned or unapproved actions, missing release comments, unapproved internal action paths, workflow/composite cycles, missing nested dependencies, excessive reusable-workflow depth, internal leaves that call another workflow, oversized or duplicated inline implementations, opaque names, dynamic matrices, generic runners, missing timeouts, implicit permissions, `secrets: inherit`, unsafe high-risk event checkout, mutable checkout, missing exact-head assertions, pull-request publication, `latest`, missing remote read-back, unregistered artifacts, missing cleanup, public API drift, and invalid thin callers.

`contracts/action-tool-lock.json` owns action SHAs, human-readable releases, runtime generations, approved internal-action roots, the exact parser version, source digest, the retained Linux wheel, the exact macOS source artifact, and the pre-provisioned emergency host-runtime contract. `contracts/validation-harness.json` owns the original parser/security thresholds, semantic runner bootstrap profiles, required fixture coverage, and narrowly documented temporary exceptions. Runner contract files containing `runner` in their filename are discovered automatically so issue-specific capability work extends the gate without changing `self-check.yml`.

## Named command and readability contracts

`contracts/ciw-commands.json` is the checked-in command registry. It must agree exactly with the direct handlers in `ci_workflows.ciw`; callers cannot select arbitrary modules, functions, callbacks, runners, engines, secrets, or deletion targets. `docs/reference/ciw.md` is rendered deterministically from that registry.

`contracts/readability-policy.json` owns the reviewed readability limits and exception format:

- public reusable-workflow depth `1`;
- internal leaf reusable-workflow children `0`;
- local composite-action depth `1`;
- public workflow job guidance `7`;
- inline `run:` maximum `40` non-empty lines;
- duplicate-block threshold `8` non-empty lines;
- complex-loop threshold `12` lines;
- statically bounded matrix maximum `16`.

The second-pass readability validator also rejects callback-like generic inputs, intent-free job or public-function names, composite nesting, malformed or untested exceptions, stale generated command/readability documentation, and drift between the public API, readability policy, and canonical harness limits. Every exception records an issue, path, exact rules, rationale, removal condition, and checked-in regression tests. `docs/architecture/readability-and-functions.md` is generated from the policy.

## Automatic tests

The self-check runs `"${VERIFIED_PYTHON}" -m unittest discover -s tests -p 'test_*.py' -v`. New focused suites under `tests/test_*.py` or nested `tests/**/test_*.py` are therefore included without workflow edits. Tests must be hermetic and must not require registry, Agent State mutation, device, signing, SOPS, Kubernetes, or product credentials.

Issue #31 adds focused dispatch, typed-result, error-projection, wrapper, action-output, redaction, cleanup-registration, compatibility, call-graph, threshold, exception, mutation, and generated-document tests. Existing pre-#31 hermetic harness fixtures remain valid because the readability pass is activated only when their repository contains `contracts/readability-policy.json`.

## Extending the harness

A new reusable workflow must add its public API record, checked-in documentation, named implementation component, positive caller fixture, negative policy fixture, cleanup assertion, and focused `test_*.py` suite. Add policy behavior as a named tested function under `src/ci_workflows/`; do not grow `self-check.yml` with capability-specific commands.

A new `ciw` command must add one checked-in registry record, one direct handler, typed inputs/results, stable domain error projection, deterministic documentation, positive/negative dispatch tests, and any required cleanup registration. Future namespace names in the command contract are reservations only and do not authorize unimplemented behavior.

The portable runner is not required to provide `pip`, `ensurepip`, or `venv`. A standard-library bootstrap downloads the exact runtime artifact from the approved Python package host, verifies SHA-256, rejects unsafe archive members, and extracts it under the runner temporary directory. The normal self-check retains no artifacts; its JSON summary and parser runtime are removed by the `always()` cleanup path.

## Temporary cross-platform parser bootstrap

The normal Linux `portable` runtime remains locked to the existing CPython 3.12 manylinux x86_64 PyYAML 6.0.3 wheel. The temporary ci-workflows #60 emergency path uses the organization-managed `macOS` host's pre-provisioned CPython 3.12.13. Before checkout, the workflow resolves the candidate to an absolute executable path and verifies CPython, exact version 3.12.13, Darwin, and arm64 or x86_64. It exports that verified path and invokes every later Python script, subprocess entry point, inline check, and recursive unittest discovery through the same executable.

The emergency workflow never installs Python, invokes `sudo`, uses Homebrew, pip, virtualenv, pyenv, Conda, build isolation, `setup.py`, or a compiler. The verified host interpreter downloads only the exact approved PyYAML 6.0.3 source archive from `files.pythonhosted.org`.

The standard-library bootstrap verifies SHA-256 before opening either artifact. For the source archive it rejects absolute or traversal paths, duplicate destinations, file/directory collisions, symbolic and hard links, devices, FIFOs, unsupported member types, excessive member counts, and excessive expanded size. It verifies `PKG-INFO`, extracts only `lib/yaml` into the isolated validation root beneath `RUNNER_TEMP`, and removes the entire root in the existing `if: always()` cleanup.

Ordinary Python validation remains a `portable` responsibility. Only `.github/workflows/self-check.yml` may use this temporary `macOS` exception while Flux #268 is open. It grants no Apple signing, simulator, device, notarization, or store capability and must be removed by a later bounded change after portable ARC recovery.
