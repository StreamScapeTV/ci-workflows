# Consumer caller skeletons

These files are **copy/adapt examples** for product repositories. They live under
`docs/` so GitHub does not execute them in this repository. Copy the relevant
file into the consumer repository's `.github/workflows/` directory and replace
each `TODO_...` value with reviewed product-owned configuration.

Before adapting a caller, consult the organization `CI_WORKFLOWS.yml` navigation
index and the Central `contracts/public-workflows.json` contract. Presence of a
workflow file is not a reason to create a parallel caller or shared capability.

## Central reference model

During active Central development, normal consumers call the reviewed reusable
workflows at `@main`. That is ordinary shared-library consumption. A consumer
does not register a Central product ID, maintain a Central commit SHA, exchange
request IDs, copy image digests, run an initialization workflow, or perform a
bootstrap/synchronization handshake before it can call Central.

A later human-readable compatibility tag such as `@v1` can replace `@main`
without changing caller structure. Full commit SHAs remain normal GitHub
functionality for exceptional pinning or historical evidence, but they are not the normal adoption ceremony shown here.

## Trigger, source and concurrency ownership

Product repositories own:

- `pull_request` / `push` events and protected branches;
- `paths` or `paths-ignore`;
- stable source-repository + logical-branch concurrency;
- minimum caller permissions;
- product paths, scripts, schemes, Gradle plans and checked-in contracts.

Validation callers first use `reusable-resolve-source.yml@main` and then pass
the admitted `source_sha` to the technology workflow. The examples intentionally
run push validation only on `TODO_PROTECTED_BRANCH`; feature branches use the
pull-request path unless that repository has a separately reviewed source model.

The validation examples use
`${{ github.event.pull_request.head.repo.full_name || github.repository }}:${{ github.head_ref || github.ref_name }}`
with `cancel-in-progress: true`. A pull request therefore groups by its actual
head repository and head branch; an ordinary branch push falls back to the
current repository and ref name. The group intentionally has no workflow-name
prefix, so a newer validation workflow for the same logical product branch
cancels the obsolete run even when the technology workflow differs. The
admitted SHA is evidence identity, not concurrency identity.

## Execution backend

`source.resolve`, `validation.node`, `validation.python`, `validation.script`
and `validation.gitops` expose the bounded
`execution_backend: organization | github-hosted` choice.

The Node, Python, script and Helm/GitOps examples pass the same
`TODO_EXECUTION_BACKEND` to source admission and validation so an explicit
hosted request does not silently depend on organization planner capacity.
`organization` is the backwards-compatible default. `github-hosted` never
falls back to organization capacity.

Do not replace this input with raw runner labels, groups, hosts, ARC identities
or engines. Keep the validation profile compatible with the chosen backend.
The script example deliberately uses the portable `general` profile, the Python
example uses `host`, and the Helm example uses the GitOps `helm-render` profile.

Apple and Android use their reviewed semantic capacity and do not expose
`execution_backend`; their source-admission call therefore keeps the normal
organization default.

## Helm validation

The historical public `helm.validate` facade is no longer in the supported
Central catalogue. `helm.yml` demonstrates the current supported Helm source
validation path through `validation.gitops` with `validation_profile:
helm-render` and a checked-in `consumer_contract`.

That consumer contract and chart paths are product-owned configuration. They are
not Central registration keys.

## Release ownership

`release.yml` is the normal private-registry release shape: a product SemVer tag
push calls `reusable-native-image-chart.yml@main`; Central uses that product tag
as release-version authority and receives only the fixed registry credentials
required by the public contract.

The normal caller does not expose `workflow_dispatch`, existing-tag recovery,
request IDs, tag-cut helpers, a consumer-maintained Central SHA, digest-copy
steps, or a Flux handoff ceremony. Public repositories that need the dedicated
credential-free publication boundary should use the indexed
`release.public-native-image-chart` API instead of weakening this private
release caller.

## Templates

- `apple.yml` — Apple protected validation.
- `android.yml` — Android / Gradle validation.
- `node.yml` — Node validation with bounded backend selection.
- `python.yml` — product-owned Python validation script with bounded backend selection.
- `script.yml` — generic checked-in script validation with bounded backend selection.
- `helm.yml` — Helm render validation through the supported GitOps capability.
- `release.yml` — normal SemVer tag-push native image + Helm chart publication.

These examples stay GitHub-native and intentionally introduce no planner
service, queue, database, custom CI command language or organization run
protocol. Native `[skip ci]` / `[ci skip]` markers remain available for coherent
intermediate commits; a final candidate still uses whatever non-skipped
validation the owning repository requires.
