# Generic package publication

`reusable-package-publish.yml` builds, validates, and publishes one Python, npm, or JVM package from the source selected by a normal product Git-tag push. The reusable owns package-manager process mechanics, semantic runner selection, exact tagged-source isolation, and cleanup. Product repositories own their tag trigger, package metadata/configuration, project path, package name, publication destination configuration, registry secrets, and caller concurrency.

## Public inputs

| Input | Required | Meaning |
|---|---:|---|
| `ecosystem` | yes | `python`, `npm`, or `jvm`. |
| `working_directory` | no | Relative package-project directory; defaults to `.`. |
| `package_name` | yes | Expected package name validated against built metadata. |
| `package_group` | JVM only | Optional Maven group expected in the produced POM. |
| `publication_plan_json` | yes | Small bounded ecosystem-specific publication plan described below. |

The workflow deliberately does not accept a release SHA or package-version input. For a normal release, the human-pushed stable SemVer product tag such as `1.2.3` is the source/version authority. Central resolves that tag to its exact commit, checks out that source, and verifies that the package artifact being published actually reports the same version. That metadata equality is package-artifact validation, not a second release authority.

The workflow exposes only the existing named registry secrets `registry_username` and `registry_token`. It never accepts a secret-variable name, concrete runner label, container engine, arbitrary command, or raw registry host. `registry_token` is token auth when no username is supplied. For Python/PyPI-compatible endpoints, supplying `registry_username` maps that same secret into the primitive's fixed username/password credential pair; no extra public password secret is required. npm remains token-authenticated, and Maven may reference the fixed `CI_PACKAGE_USERNAME` / `CI_PACKAGE_TOKEN` environment from product-owned checked-in settings.

## Publication plans

The JSON plan is limited to 16 KiB and rejects unknown fields. Python and npm require exactly one destination mechanism: either a built-in technology `registry_profile` or a relative `registry_config_path` to a checked-in, non-secret JSON destination file. A plan cannot specify both.

A private compatible registry can therefore remain product-owned without exposing an unrestricted registry host as a reusable-workflow input. For example, a product can commit `ci/package-registry.json`:

```json
{"registry_url":"https://packages.example.test/repository/sdk/"}
```

That file is limited to one `registry_url` field, is read only from the exact tagged package project, must be a regular non-symlink file within the project, and is size-bounded. Credentials do not belong in this file; they remain named workflow secrets.

### Python

A built-in PyPI destination:

```json
{"output_directory":"dist","registry_profile":"pypi"}
```

A product-owned private PyPI-compatible destination:

```json
{"output_directory":"dist","registry_config_path":"ci/package-registry.json"}
```

Built-in profiles are `pypi` and `test-pypi`. The general runner supplies CPython/pip, while Central installs fixed `build` and `twine` publication frontends into the marker-bound run-owned temporary tree with pip input/version checks and pip caching disabled. That tool tree is removed with the rest of the package run state. Central then runs `python -m build` for wheel and sdist output, verifies embedded package name/version metadata against the product tag, and publishes those validated files with Twine using the fixed package credential environment. The existing package primitive validates compatible registry URLs before network publication.

Python build and tool-bootstrap phases do not receive package registry credentials. The fixed named credentials are restored only for the publication primitive.

### npm

A built-in npm destination:

```json
{"output_directory":".ciw-package-output","registry_profile":"npmjs"}
```

A product-owned private npm-compatible destination uses the same `registry_config_path` mechanism. Built-in profiles are `npmjs` and `github-packages`. Central runs `npm pack --json` without package registry credentials, validates `package/package.json` from the tarball against the product tag, then publishes exactly that tarball with the fixed publication credentials. npm authentication uses the existing temporary mode-0600 user config from the package primitive and removes it on both success and failure.

### JVM / Maven

```json
{
  "output_directory":"build/publication-repository",
  "maven_actions":["deploy"],
  "maven_options":["-B"],
  "maven_executable":"mvnw"
}
```

The JVM path intentionally uses the existing Maven publication primitive only; this issue does not change Android or Gradle validation. `maven_executable` is either a checked-in Maven wrapper whose basename is `mvnw`, or the runner-provided `mvn`. Actions remain bounded Maven goals and options reject registry URLs and secret-bearing options. The product's checked-in Maven configuration owns the publication repository/destination. Central inventories the configured output directory and requires a POM matching `package_name`, the product-tag version, and optional `package_group`. Maven receives the fixed named package credentials because a configured Maven `deploy` action performs build and publication in one invocation.

Central additionally prepends a run-owned `maven.repo.local` location and supplies run-owned Maven home/user-home state. A product that needs a checked-in Maven settings file may still pass its bounded non-secret `-s`/`--settings` option through `maven_options`; credentials should be referenced from the fixed `CI_PACKAGE_*` environment rather than committed into settings.

## Release and cleanup behavior

Package publication is privileged, but the normal UX is intentionally small: a product caller is triggered by a stable SemVer tag push and invokes the reusable. Central resolves that exact tag to its exact source commit and revalidates the same tag object/commit immediately before registry publication. Existing-tag replay/manual-dispatch recovery is not part of this reusable's normal package release interface.

The package project is copied from the clean exact tagged checkout into marker-bound run-owned workspace state before package-manager execution. Source symlinks are accepted only when their resolved target remains inside that exact package project; a dangling or escaping symlink fails before package execution. Build output therefore cannot dirty the admitted checkout or use a copied path to escape into unrelated runner files.

Package-manager ambient state is replaced with run-owned `HOME`, temporary, XDG, npm cache/prefix/config, Python cache, Maven user-home, and Maven local-repository paths beneath the same package run root. The adapter removes its source/output/tool/cache copy even when the package command fails, and the reusable workflow then runs the normal `cleanup-workspace` path under `if: always()` and verifies that the tagged source remains at the exact resolved SHA with no tracked or untracked changes.

This reuses existing tag authority and package primitives only. It does not add provenance, signing, attestation, OIDC, immutable-registry read-back, or another supply-chain framework. No GitHub Actions cache is used and no routine workflow artifact is uploaded.

## Outputs

The reusable returns `result`, `ecosystem`, `package_name`, `package_version`, and `cleanup_result`. `package_version` is the exact stable SemVer product tag validated against the package metadata. `result=success` requires successful package publication plus workspace cleanup and clean-source verification.

## Caller shape

The product repository keeps the tag trigger and policy in its own thin caller. A representative caller shape is:

```yaml
on:
  push:
    tags:
      - "*.*.*"

jobs:
  package:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-package-publish.yml@main
    with:
      ecosystem: npm
      package_name: "@example/sdk"
      publication_plan_json: '{"registry_profile":"npmjs"}'
    secrets:
      registry_token: ${{ secrets.PACKAGE_REGISTRY_TOKEN }}
```

During active Central development the reusable may be referenced at `@main`; a later human-readable Central compatibility tag can replace that reference without adding a consumer-maintained Central SHA or manual release ceremony.
