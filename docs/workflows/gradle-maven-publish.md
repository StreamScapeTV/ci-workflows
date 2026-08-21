# Gradle Maven publication

`release.gradle-maven` is the bounded trusted-publication workflow for Gradle projects that publish Maven packages to a product-configured private Maven repository.

The reusable workflow checks out the exact admitted caller source, creates one private Gradle workspace, and runs exactly one Gradle publication command containing the caller-owned bounded publication task list. The product build owns the Maven repository URL and publication declarations; Central does not accept an arbitrary registry host or URL.

## Inputs

- `admitted_sha`: exact caller source SHA.
- `expected_branch`: literal `develop`; it is checked again so a caller cannot select another publication branch.
- `working_directory`: repository-relative Gradle project directory.
- `gradle_wrapper_path`: checked-in wrapper relative to the Gradle project directory.
- `version_file`: repository-relative stable `MAJOR.MINOR.PATCH` version file.
- `arguments_json`: non-empty bounded JSON array of Gradle Maven publication task identities.

The workflow accepts only the named `registry_username` and `registry_token` secrets. They reach only the execute phase and are forwarded to Gradle as `FORGEJO_REGISTRY_USERNAME` and `FORGEJO_REGISTRY_TOKEN`. The credentials are never placed on the command line, output, evidence, or cleanup phases.

## Version authority

Publication mode is derived from the caller ref rather than a caller-selected mode flag.

When the caller ref is exactly `refs/heads/develop`, Central derives an immutable source-specific version:

`<base-version>-develop.<first-12-characters-of-source-sha>`

When the caller ref is exactly `refs/tags/v<base-version>`, Central publishes `<base-version>`.

Any other caller ref fails closed. Development publication therefore does not overwrite a mutable `latest` Maven version, while stable tags map directly to stable Maven versions.

## Execution and cleanup

Central passes `-PciMavenPublicationVersion=<resolved-version>` to the one Gradle invocation. Product Gradle configuration may use that property to replace its normal stable version for development publication.

Routine publication uses zero GitHub Actions artifacts, no GitHub Actions cache, no OIDC token, and no caller-selected runner or registry endpoint. The exact caller checkout is verified at the admitted SHA, ignored build outputs are removed in an `always()` cleanup phase, source residue is rejected, and the private Gradle workspace is deleted under terminal cleanup—even when the publication phase fails.
