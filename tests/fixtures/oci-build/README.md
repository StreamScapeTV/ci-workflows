# OCI build fixtures

The `smoke` product is a tracked scratch image with no network dependency, no
registry destination, no credential input, and no retained archive. It proves
the real Buildah runner path, local OCI layout inspection, contract-owned
metadata, bounded smoke, cleanup, residue detection, and zero-artifact policy.

Negative cases are generated in disposable test repositories so mutable bases,
symlinks, dirty contexts, caller-selected engines/runners/commands, secret
leakage, malformed layouts, digest drift, platform drift, unsupported product
families, and cleanup failures cannot become executable repository content.
