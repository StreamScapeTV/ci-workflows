# Privacy and functional CI boundaries

## Mandatory privacy boundary

Central CI must prevent outsiders from reading private repository source, credentials, private configuration, dependency identities intentionally kept opaque by the private-CI architecture, or private command output. Private-source detailed logs stay out of public GitHub logs/summaries and Actions artifacts and use the bounded private R2 path owned by #495. Credentials are passed only through explicit named secret/environment boundaries and are cleaned with their run-owned state. `secrets: inherit` remains forbidden.

Privileged jobs must not execute untrusted fork or metadata-event source with private credentials or private checkout authority. Checkout credentials are not persisted after checkout. These rules are retained because violating them can expose private source or credentials.

## Ordinary action and dependency references

There is no repository-wide action SHA allowlist, release-comment registry, runtime-generation registry, or first-party checkpoint carrier. Ordinary development may use normal GitHub action/reusable-workflow references such as a maintained release tag or `@main` where that channel is the intended compatibility surface. A specific workflow may require an immutable identity only when the identity is part of that workflow's functional release or source contract.

The validation harness dependency is declared conventionally in `requirements/validation.txt` and installed into run-local state. It is not a supply-chain policy registry and carries no artifact digest ceremony.

## Artifacts

Public and otherwise non-private workflows may retain Actions artifacts when the feature actually needs them. There is no global zero-artifact registry. Private-source Central runs remain different: their detailed private output must never become a public Actions artifact and continues to use private R2 storage/read-back.

## Release correctness

Exact Git tags, immutable product versions, and remote read-back remain required where they are the functional release identity or where read-back proves that publication actually succeeded. Those checks are release correctness, not a global action-pinning program.

## Cleanup

Cleanup remains mandatory for credentials, private checkout/authentication state, private logs, and other run-owned state whose residue could expose private data or interfere with later executions. Other temporary state is cleaned when required for deterministic functional execution.
