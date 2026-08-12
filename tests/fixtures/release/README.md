# Release orchestration fixtures

These fixtures model only bounded public outputs from the registered OCI and Helm publication contracts. They intentionally contain no credentials, live registry access, Kubernetes state, or Actions artifact payloads.

`publications.json` covers both a single-image application release and the multi-target Flux runner image family so release tests preserve full target-to-digest identity rather than collapsing a family to one arbitrary digest.
