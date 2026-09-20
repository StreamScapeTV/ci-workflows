# StreamScapeTV CI workflows

Shared reusable workflows.

## Public identity boundary

Reusable Central CI APIs, contracts, documentation, and examples describe capabilities and project classes, not concrete private consumer repositories or products. New shared behavior must stay repository-neutral and use non-identifying fixtures.

A small number of technology-specific workflows/actions still contain concrete consumer checks solely because live callers depend on those compatibility lanes. Those source files, and tests that must assert their exact compatibility behavior, carry a dedicated legacy-migration-residue marker. The marker is not an API or an allowlist: it documents temporary migration residue and must not be used to justify new identity-coupled behavior. Remove the identity coupling only after the private migration ledger proves that the affected legacy lane has zero live callers.

The generic repository-owned executor (`.github/workflows/repository.yml`) and its public contract (`contracts/repository-ci-v1.json`) must remain free of concrete consumer identity.
