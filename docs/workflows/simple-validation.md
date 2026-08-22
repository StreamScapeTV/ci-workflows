# Simple product-script validation

`validation.script` is the default low-ceremony validation building block for product repositories that already own a complete checked-in CI command.

The required path is intentionally short:

`product trigger -> reusable-script.yml -> checked-in product script`

The product repository owns the script, tool versions, build/test commands, schemes, Gradle tasks, playback assertions, Dockerfiles, chart values, and product cleanup. Central owns only exact caller checkout, bounded semantic runner placement, safe repository-relative script selection, direct zero-argument invocation, and a terminal clean-tree check.

## Inputs

- `execution_backend`: optional `organization` or `github-hosted`; default `organization`.
- `admitted_sha`: exact lowercase caller source commit.
- `validation_profile`: exactly `general`, `mobile`, or `apple`.
- `working_directory`: optional repository-relative working directory, default `.`.
- `script_path`: required repository-relative executable script. It must be a regular non-symlink file inside the checked-out repository.

The reusable workflow does not accept arbitrary shell text or caller-provided arguments: product scripts always receive zero injected arguments from Central. If a product needs multiple behaviors, its checked-in script owns that bounded selection.

## Semantic capacity and trust

The default `organization` backend preserves the existing semantic mapping:

- `general` -> `[linux, amd64, general, small]` for ordinary backend, Python, Node/Next, policy, and source work. Tokenless fork pull-request source may use this non-specialized capacity.
- `mobile` -> `[linux, amd64, mobile]` for Android/Gradle and other pre-provisioned mobile toolchains.
- `apple` -> `[macOS, ARM64]` for Xcode/iOS/tvOS/macOS validation.

`github-hosted` is deliberately narrower in this portable tranche:

- `general` -> `[ubuntu-latest]`;
- `mobile` and `apple` fail closed with the Central unsupported-profile boundary until their standard-hosted toolchain contracts are proven separately.

Planner placement follows the backend instead of introducing a hidden cross-backend dependency. An explicit hosted call performs trust/profile planning on `ubuntu-latest`; the default/explicit organization call uses `[linux, amd64, general, small]` for planning and then resolves the selected semantic profile through Central. The planners are mutually exclusive and the script executor consumes only the successful planner's selector. `mobile` and `apple` are admitted only for same-repository pull requests or exact `push`/`workflow_dispatch` source, so fork pull requests fail during the selected planner before specialized execution can be scheduled. Products never select concrete runner hosts or scale sets, and an explicit hosted request never falls back to organization capacity.

## Thin caller

```yaml
jobs:
  validate:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-script.yml@main
    with:
      execution_backend: github-hosted
      admitted_sha: ${{ github.event.pull_request.head.sha || github.sha }}
      validation_profile: general
      script_path: scripts/ci/validate.sh
```

Omit `execution_backend` to retain the organization/private default. The caller still owns its trigger, concurrency, and minimum permissions. During active Central development, `@main` is the normal shared-library reference.

## What is deliberately not required

The simple path has no product-specific central task registry, private-action checkpoint, action SHA lock, evidence manifest, provenance bundle, GitHub Actions cache, routine artifact upload, release canary, or rollback state machine. It retains explicit read-only permissions, exact source verification, credential-free checkout persistence, safe script-path validation, and clean-tree enforcement.

The existing specialized Python, Node, Android, Apple, Flutter, device, Helm/GitOps, and OCI workflows remain available when a product actually needs their additional behavior. They are not prerequisites for using `validation.script` or for the IPTV migrations tracked by #21-#25.
