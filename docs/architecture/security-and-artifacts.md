# Security and artifact baseline

## Trust classes

Shared workflows classify execution as read-only validation, Agent State transport, trusted publication, trusted Flux reconciliation, or organization maintenance. Permissions, runners, secrets, source admission, and cleanup are reviewed independently for each class.

## Source and credentials

Use exact immutable source identities and `persist-credentials: false`. Privileged events never execute pull-request, fork, issue-comment, or caller-controlled source. Secrets are explicit and named; `secrets: inherit` is forbidden.

Agent State transport sends bounded event context to the API and projects only sanitized API results. Flux-authorized orchestration executes exact protected Flux policy source and cannot accept an arbitrary target or command.

## Artifact policy

Routine workflows retain zero Actions artifacts. The exception registry is `contracts/artifact-policy.json`. An exception requires an identifier, owner, purpose, exact paths, redaction/privacy rules, maximum retention, permitted workflows and trust modes, and removal criteria.

Generated logs, reports, image archives, packaged charts, APK/AAB files, result bundles, caches, credentials, and temporary state are removed unless a registered exception requires retention.

## Cleanup

Cleanup runs on every terminal path, removes registered state, and fails when required residue remains. Cleanup never follows untrusted symlinks or deletes outside normalized registered roots.
