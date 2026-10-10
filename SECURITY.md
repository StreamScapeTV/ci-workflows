# Security policy

`ci-workflows` handles trusted CI orchestration, source access, ephemeral authentication, private evidence and release-related boundaries. Please treat security findings about those boundaries as sensitive.

## Supported versions

Security fixes are evaluated against the current protected `main` source. Historical branches and completed workflow runs are not separately maintained release lines unless a maintainer documents an explicit exception. Repository visibility does not imply public access to the configured Central infrastructure.

## Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** / private vulnerability reporting feature for this repository **if it is enabled**. This route permits sharing details privately with the maintainers. If it is not available, create only a *non-sensitive* issue asking the maintainers for an appropriate private reporting channel; do not include the exploit, secret, private CI evidence, vulnerable private URLs or affected user data in that public issue.

Include, in the private report only, the affected file/commit, prerequisite capabilities, a minimal sanitized reproduction, impact, and any proposed mitigation. Do not attempt unapproved exploitation of connected runners, projects or user accounts.

## Handling and disclosure

Maintainers will assess reports as capacity permits and coordinate a fix and disclosure timing appropriate to the risk. We do not currently publish a guaranteed response-time SLA or promise that private vulnerability reporting is enabled in every repository setting.

Never attach raw Actions secrets, P12/P8 files, private registry credentials, kubeconfigs, CI log archives, or identity-bearing artifacts to a public issue or pull request. Source/test fixes should use synthetic fixtures and fixed failure categories. Public disclosure should wait until the affected maintainers have had an opportunity to address the issue.
