# ci-broker

Small transport service for Agent State-triggered Central CI.

The Agent State INSERT webhook wakes the broker. The broker claims the CI row, computes repository/ref concurrency identity, and dispatches `.github/workflows/central-ci-dispatch.yml` with only `ci_run_id` and the concurrency key.

The broker does not execute product commands, resolve product profiles, read product manifests, handle logs, or access R2.
