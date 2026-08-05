# Private repository access and references

The `ci-workflows` repository is private. In repository settings, Actions access must allow workflows and actions from this repository to be used by supported private repositories in the `StreamScapeTV` organization. Consumer Actions policies must also allow the exact pinned third-party actions used by the shared implementation.

GitHub provides a short-lived read token to download private shared workflow/action source for a caller run. Collaborators who can view the caller run may see shared implementation details in logs; shared workflows must therefore never print secrets or private infrastructure values.

## Initial reference policy

During the first organization rollout, consumers may reference `@main` so fixes are immediately available without updating every repository. Tags and full SHAs remain valid alternatives.

After the first stable tag, privileged or production callers may move to immutable tags or full SHAs as a separate reviewed migration. A release of this repository is a Git tag only; no GitHub Release or attached file is required.

## Failure and rollback

If shared access is revoked or `main` regresses, a consumer can point its thin caller at a known-good tag or full SHA without changing product source. Never delete a tag still referenced by a supported consumer.
