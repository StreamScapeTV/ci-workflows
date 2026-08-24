# Security and artifact baseline

## Trust classes

`contracts/public-workflow-types.json` is the machine authority for public workflow trust classes. The security baseline mirrors its current privilege and caller-source execution boundaries:

| Trust class | Privileged | Executes caller source | Security boundary |
|---|---|---|---|
| `source-admission` | no | no | Resolves and verifies source identity without executing caller source or receiving product privilege. |
| `read-only-validation` | no | yes | Executes admitted caller source only under validation permissions with no publication, device, Flux, or maintenance authority. |
| `physical-device-validation` | yes | yes | Executes exact trusted caller source only after the separate guarded device authorization, fencing, runner, evidence, restoration, and cleanup boundaries admit it. |
| `trusted-publication` | yes | yes | Executes exact approved release source with explicitly named publication credentials; publication remains separate from deployment. |

Permissions, runners, secrets, source admission, evidence, and cleanup are reviewed independently for each class. A privileged class is not an escalation of ordinary read-only validation: its event, source, credential, target, runner, and mutation authority remain separately bounded by the public workflow contract and domain policy.

## Source and credentials

Use exact immutable source identities and `persist-credentials: false`. Privileged events never execute pull-request, fork, issue-comment, or caller-controlled source. Secrets are explicit and named; `secrets: inherit` is forbidden.

This repository stores no Agent State endpoint or credential and exposes no Agent State transport. The retained public catalogue exposes no Flux-authorized or organization-maintenance workflow class; those retired facades cannot be used to widen a retained public API's source or credential boundary.

## Artifact policy

Routine workflows retain zero Actions artifacts. The exception registry is `contracts/artifact-policy.json`. An exception requires an identifier, owner, purpose, exact paths, redaction/privacy rules, maximum retention, permitted workflows and trust modes, and removal criteria.

Generated logs, reports, image archives, packaged charts, APK/AAB files, result bundles, caches, credentials, and temporary state are removed unless a registered exception requires retention.

## Cleanup

Cleanup runs on every terminal path, removes registered state, and fails when required residue remains. Cleanup never follows untrusted symlinks or deletes outside normalized registered roots.
