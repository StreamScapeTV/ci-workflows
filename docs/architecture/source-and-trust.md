# Exact source admission and trust

`source.resolve` is the organization’s metadata-only admission boundary. It turns a bounded caller event and optional exact assertions into one typed exact source record. It does not check out, import, install, build, or execute product source.

The public workflow is `.github/workflows/reusable-resolve-source.yml`. Its implementation is the immutable composite action `actions/resolve-source/action.yml`, backed by named typed functions in `src/ci_workflows/source.py`. Consumers use `actions/exact-checkout/action.yml` only after admission.

## State machine

Trust is derived from the current GitHub event and current repository metadata. There is no public `trust_mode` input, matrix override, callback, arbitrary ref, artifact path, remote URL, or command input.

| Event and admitted transition | Resulting trust | Exact source | Required current evidence |
|---|---|---|---|
| `pull_request` with `pr-head` | `untrusted-validation` | current PR head commit, including a fork repository | event head/base agree with the current PR API response; PR base is the asserted integration branch; an explicitly asserted merge SHA must still match, but an unselected synthetic merge ref may be regenerated independently |
| `pull_request` with `pr-merge` | `untrusted-validation` | current GitHub merge commit | event head/base/merge agree with the current PR API response; PR base is the asserted integration branch and the current merge SHA is non-null |
| protected branch `push` | `trusted-validation` | exact pushed commit | pushed branch is the asserted integration branch and its current tip still equals `github.sha` |
| `workflow_dispatch` | `trusted-validation` | exact requested full SHA, or the exact dispatch SHA | triggering actor has write, maintain, or admin permission and the commit exists |
| reusable call with `workflow-call` | event-derived validation trust | exact caller-provided full SHA | the original triggering event is still validated; a PR remains untrusted, a branch push remains branch-bound, and a tag remains tag-bound |
| tag `push` | `tag-release` | recursively dereferenced tag commit | exact tag ref and tag objects resolve to one commit and `github.sha` agrees with the tag object or resolved commit |
| trusted metadata or maintenance | `trusted-maintenance` | current default-branch helper commit only | authorized actor, exact current default branch, and current PR evidence when supplied |

`workflow_run`, `issue_comment`, and `pull_request_target` are metadata/coordination contexts only. They never admit a PR head, downloaded artifact, dependency, caller script path, or fork checkout as trusted executable source. Their admitted source is the current protected default-branch helper code.

## Typed admission record

The resolver returns:

- caller repository, current default branch, and asserted integration branch;
- event-derived trust mode;
- source repository and exact source SHA;
- requested versus resolved SHA;
- PR number, head repository/SHA, base branch/SHA, and merge SHA when applicable;
- tag name, exact tag object SHA, and dereferenced commit SHA for releases;
- bounded checkout history depth;
- a `requires_freshness` flag;
- stable `request_id` and `evidence_id` values containing only a safe prefix and SHA-256-derived suffix.

Empty values mean “not applicable”; they never contain tokens, private URLs, raw API responses, or artifact contents.

## Exact checkout

The checkout action accepts only:

- `repository` in `owner/name` form;
- the admitted lowercase 40-character `admitted_sha`;
- an empty normalized path below `GITHUB_WORKSPACE`;
- a bounded fetch depth from 1 through 1000;
- an optional read token.

It initializes a new repository, fetches the exact SHA with `--no-tags`, checks out detached `FETCH_HEAD`, and requires `git rev-parse HEAD` to equal the admitted SHA. Authentication is passed through transient process environment configuration and is not written to local Git configuration. A branch name, tag name, arbitrary ref, arbitrary remote URL, non-empty destination, or changed checkout fails closed.

The action does not preserve credentials. Its contract is the equivalent of `persist-credentials: false`; callers must not add a credential-bearing remote or rewrite the checked-out source before verification.

## Freshness and privileged follow-up

Admission is evidence, not permission to publish, merge, comment, set a privileged status, or reconcile a cluster forever. When `requires_freshness` is true, the privileged operation must call the typed `revalidate_admission` function or repeat source admission immediately before publication or mutation.

Revalidation checks:

- current PR head and base for every admitted PR source;
- current PR merge SHA when the merge candidate was selected, when a caller explicitly asserted the merge SHA, or when trusted maintenance depends on PR evidence;
- current integration-branch tip for protected pushes;
- current default-branch helper SHA for trusted maintenance.

A stale result fails with a stable instruction code. It must not be converted into a warning or reused from an earlier workflow artifact.

## Trusted helper separation

The reusable workflow executes the resolver through a full-SHA reference to `actions/resolve-source`. That immutable action archive contains only the central helper, contract, and CLI. Product source is not present in its workspace and is not checked out during admission.

After admission, a separate job or step may invoke `actions/exact-checkout` with `source_repository` and `source_sha`. Privileged metadata workflows must not invoke that checkout for untrusted PR source at all.

Downloaded workflow artifacts remain untrusted data. This contract has no artifact input and does not promote an artifact into trusted source. A future signed evidence transport requires a separate reviewed contract.

## Immutable private reusable-helper distribution

A reusable validator invoked from another private StreamScapeTV repository must not clone `StreamScapeTV/ci-workflows` with the caller-scoped `github.token` merely to reach central actions, scripts, or libraries. That token is scoped to the calling repository and a private central clone can fail before product validation begins.

Reusable validators instead compose reviewed central composite actions through exact full-SHA references. The immutable private action archive supplies its central scripts and Python modules relative to `GITHUB_ACTION_PATH`; the workflow therefore does not need a `.ciw` checkout, a central-repository PAT, `secrets: inherit`, a mutable helper ref, or a caller-selected helper version. Every remotely composed central action identity is recorded in `contracts/action-tool-lock.json` before the final candidate is eligible to merge.

This distribution rule does not weaken source authority. Product source remains separately admitted by `source.resolve`, checked out only through the exact-checkout contract, and reverified after cleanup where the validator requires it. Runner intent, public inputs and outputs, permissions, stable checks, cleanup, and zero-artifact policy remain owned by the reusable workflow and its checked-in contracts.

New reusable validators should use this immutable private-action model by default. A different mechanism requires a reviewed product-neutral reason and must still avoid caller-token central clones, generic credential inputs, mutable helper selection, and a second compatibility transport.

## Consumer patterns

Backend and Android manual validation pass an optional exact SHA with `source_mode: manual`; mutable branch or ref input is rejected. Apple and media callers assert `develop` or their exact integration branch. Web PR and push callers use PR or branch admission without duplicating event parsing. Agent State coordination is outside this workflow transport and uses approved direct Supabase RPCs. Tag-triggered image and chart publication consumes `tag_name` and `tag_commit_sha`, so a tag on a historical commit releases that exact historical source. Flux source validation may use ordinary exact admission, while cluster-authorized reconciliation remains a separate Flux-owned workflow using exact protected Flux policy source.

Product commands, status/comment formats, registry publication, cluster selection, and deployment are deliberately outside this resolver.

## Thin caller example

During the active-development/bootstrap phase, the public reusable workflow follows protected `@main` so reviewed central fixes propagate without a consumer repin. The separately composed private helper remains an immutable action reference.

```yaml
jobs:
  source:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-resolve-source.yml@main
    permissions:
      contents: read
      pull-requests: read
    with:
      source_mode: pr-head
      expected_branch: develop

  validate:
    needs: source
    runs-on: <centrally-selected-semantic-profile>
    steps:
      - uses: StreamScapeTV/ci-workflows/actions/exact-checkout@<immutable-helper-sha>
        with:
          repository: ${{ needs.source.outputs.source_repository }}
          admitted_sha: ${{ needs.source.outputs.source_sha }}
```

Full commit SHAs and stable `ci-workflows` tags remain supported fixed/rollback channels and may become the organization default after a later explicit stable-release cutover. Until that decision, they are not preferred or required over `@main` for privileged consumers. Exact product-source admission remains mandatory regardless of the central workflow reference.
