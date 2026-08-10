# GitOps validation fixtures

All fixtures are inert source data. They contain no Kubernetes endpoint,
credential, SOPS decryption key, registry login, or production reference.
The synthetic chart uses one checked-in `file://` library dependency whose
identity is covered by the contract. The policy script reads only the fixture
paths and emits a bounded result.
