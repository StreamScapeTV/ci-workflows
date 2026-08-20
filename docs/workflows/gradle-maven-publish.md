# Gradle Maven publication

`release.gradle-maven` is the bounded trusted-publication workflow for Gradle projects that publish Maven packages to a product-configured private Maven repository.

The reusable workflow checks out the exact admitted caller source, creates one private Gradle workspace, and runs exactly one Gradle publication command containing the caller-owned bounded publication task list. The product build owns the Maven repository URL and publication declarations; Central does not accept an arbitrary registry host or URL.

## Inputs

- `admitted_sha`: exact caller source SHA.
- `working_directory`: repository-relative Gradle project directory.
- `gradle_wrapper_path`: checked-in wrapper relative to the Gradle project directory.
- `version_file`: repository-relative stable `MAJOR.MINOR.PATCH` version file.
- `publication_channel`: `stable` or `develop`.
- `publication_tasks_json`: non-empty bounded JSON array of Gradle task identities.

The workflow accepts only the named `registry_username` and `registry_token` secrets. They reach only the publication action and are forwarded to Gradle as `CIW_MAVEN_REGISTRY_USERNAME` and `CIW_MAVEN_REGISTRY_TOKEN`. The credentials are never placed on the command line.

## Versioning

`stable` publishes the exact version from `version_file`.

`develop` derives an immutable source-specific Maven version:

`<base-version>-develop.<first-12-characters-of-source-sha>`

Development publication therefore does not overwrite a mutable `latest` package. A consumer can use the exact version emitted by the workflow while the source branch continues moving.

## Execution and cleanup

Central passes `-PciMavenPublicationVersion=<resolved-version>` to the one Gradle invocation. Product Gradle configuration may use that property to replace its normal stable version for development publication.

Routine publication uses zero GitHub Actions artifacts, no GitHub Actions cache, no OIDC token, and no caller-selected runner or registry endpoint. The exact caller checkout is verified tracked-clean after publication, ignored build outputs are removed, and the private Gradle workspace is deleted under terminal cleanup.
