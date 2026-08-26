# CI Broker

`ci-broker` is the standalone transport service for the opaque private-CI path.

It intentionally has one application source file, `app.py`, and only two HTTP
routes:

- `GET /healthz`
- `POST /hooks/agent-state`

For an Agent State `ci_runs` INSERT, the service authenticates the webhook,
claims the opaque request through the fixed Agent State RPC, validates the
closed reviewed `(workflow, host)` intent, and fire-and-forgets the fixed
Central `workflow_dispatch`. The public dispatch contains only `ci_run_id` and
the SHA-256 `active_key`.

The broker does **not** resolve or check out source, read `.github/central-ci.json`,
admit dependencies, choose product commands, run builds/tests, discover GitHub
runs, process/store logs, access R2, or serve diagnostics. Those responsibilities
belong to the trusted Central GitHub Actions implementation.

## Layout

- `app.py` — complete broker runtime
- `Containerfile` — minimal non-root image containing only `app.py`
- `smoke_test.py` — real loopback HTTP integration used before publication
- `deployment-values.yaml` — deployment-shape Helm release regression fixture
- `chart/` — broker Kubernetes chart

Release tags are `ci-broker-X.Y.Z`. The release workflow publishes:

- `git.faruqi.dev/mimranfaruqi/ci-workflows/ci-broker:X.Y.Z`
- `oci://git.faruqi.dev/mimranfaruqi/ci-workflows/helm-charts/ci-broker:X.Y.Z`

The chart receives matching `version` and `appVersion`, so the Deployment uses
the same immutable broker version without a mutable `latest` dependency.

The chart accepts `diagnostics.enabled=false` only as a temporary no-op upgrade
compatibility value. It has no diagnostics process, Service, Deployment, route,
or enable path; `true` is schema-invalid.
