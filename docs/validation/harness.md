# Canonical validation harness

`python3 scripts/ci/validation_harness.py --root .` is the repository-wide static gate for GitHub Actions workflows, composite actions, named Python functions, shell/Python adapters, public API contracts, caller fixtures, event fixtures, and mocked service scenarios.

The harness uses PyYAML through a narrowed YAML 1.2-style boolean resolver so the GitHub Actions `on` key remains the literal string `on`. Both `.yml` and `.yaml` are discovered. Source-shape checks are reserved for action release comments, exact expressions, and readability limits that semantic parsing cannot retain.

## Policy surface

The gate fails closed on unpinned or unapproved actions, missing release comments, unapproved internal action paths, workflow/composite cycles, missing nested dependencies, excessive reusable-workflow depth, internal leaves that call another workflow, oversized or duplicated inline implementations, opaque names, dynamic matrices, generic runners, missing timeouts, implicit permissions, `secrets: inherit`, unsafe high-risk event checkout, mutable checkout, missing exact-head assertions, pull-request publication, `latest`, missing remote read-back, unregistered artifacts, missing cleanup, public API drift, and invalid thin callers.

`contracts/action-tool-lock.json` owns action SHAs, human-readable releases, runtime generations, approved internal-action roots, the exact parser version, source digest, and runtime-specific artifact URLs and digests. `contracts/validation-harness.json` owns thresholds, semantic runner bootstrap profiles, required fixture coverage, and narrowly documented temporary exceptions. Runner contract files containing `runner` in their filename are discovered automatically so issue-specific capability work extends the gate without changing `self-check.yml`.

## Automatic tests

The self-check runs `python3 -m unittest discover -s tests -p 'test_*.py' -v`. New focused suites under `tests/test_*.py` or nested `tests/**/test_*.py` are therefore included without workflow edits. Tests must be hermetic and must not require registry, Agent State mutation, device, signing, SOPS, Kubernetes, or product credentials.

## Extending the harness

A new reusable workflow must add its public API record, checked-in documentation, named implementation component, positive caller fixture, negative policy fixture, cleanup assertion, and focused `test_*.py` suite. Add policy behavior as a named tested function in `src/ci_workflows/validation_harness.py`; do not grow `self-check.yml` with capability-specific commands.

The portable runner is not required to provide `pip`, `ensurepip`, or `venv`. A standard-library bootstrap downloads the exact runtime artifact from the approved Python package host, verifies SHA-256, rejects unsafe archive members, and extracts it under the runner temporary directory. The normal self-check retains no artifacts; its JSON summary and parser runtime are removed by the `always()` cleanup path.

## Temporary cross-platform parser bootstrap

The normal Linux `portable` runtime remains locked to the existing CPython 3.12 manylinux x86_64 PyYAML 6.0.3 wheel. The temporary ci-workflows #60 emergency path selects an already installed CPython 3.12 runtime on the organization-managed `macOS` capability before checkout. The selector checks a bounded list of standard Homebrew, framework, and current-path locations; accepts only CPython 3.12 on Darwin arm64 or x86_64; and exposes it through one temporary `python3` symlink beneath `RUNNER_TEMP`.

The emergency path does not install Python, invoke `sudo`, mutate the persistent host tool cache, run Homebrew, or use an arbitrary caller-provided executable. After checkout, the standard-library parser bootstrap downloads the exact approved PyYAML 6.0.3 source archive from `files.pythonhosted.org` and verifies its SHA-256 before opening it.

For the source archive the bootstrap rejects absolute or traversal paths, duplicate destinations, symbolic and hard links, devices, FIFOs, unsupported member types, excessive member counts, and excessive expanded size. It verifies `PKG-INFO`, extracts only `lib/yaml` into the isolated validation root beneath `RUNNER_TEMP`, and never invokes pip, build isolation, `setup.py`, a compiler, or Homebrew. The workflow verifies `yaml/__init__.py` before validation and removes both the parser root and temporary Python symlink in its existing `if: always()` cleanup.

Ordinary Python validation remains a `portable` responsibility. Only `.github/workflows/self-check.yml` may use this temporary `macOS` exception while Flux #268 is open. It grants no Apple signing, simulator, device, notarization, or store capability and must be removed by a later bounded change after portable ARC recovery.
