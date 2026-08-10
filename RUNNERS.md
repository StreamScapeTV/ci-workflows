# Runner capability and selection guide

This is the organization-wide, agent-facing reference for choosing CI capacity.
`StreamScapeTV/organization-rules@main/AGENTS.md` and `RULES.md` route here
whenever a task needs runner selection.

Use **semantic workflow intent first**. Product and reusable-workflow callers do
not supply arbitrary runner names, labels, hosts, container engines, clusters,
namespaces, service accounts, or secret names. Central workflows validate the
bounded intent and choose the implementation.

`contracts/runner-profiles.json` is the semantic resolver authority, and
`generated/runner-mappings.json` is its deterministic projection. The label
inventory below documents the current organization capacity for maintainers of
central or infrastructure-owned workflows.

## Semantic profile IDs are not GitHub runner labels

The resolver keeps stable semantic API names such as `portable`, `mobile`,
`buildah-tiny`, and `buildah-small`. These values describe requested workflow
intent. They are not copied into `runs-on` and do not need to exist as live
GitHub runner labels.

A runner row in GitHub contains different concepts:

- **Runner name:** an ARC-generated operational identity or persistent-host
  name. Its length and wording do not define scheduling policy.
- **GitHub-managed system label:** for example `self-hosted`. It can appear on a
  runner without being a safe or sufficient selector.
- **Capability label:** one independent property such as `linux`, `amd64`,
  `android`, `buildah`, or `tiny`.

Every Linux ARC custom label is lowercase. GitHub requires one runner to contain
**all** labels listed by a job. Platform labels alone, such as
`[linux, amd64]`, are intentionally ambiguous because several classes share
them.

Never copy an internal ARC scale-set, pod, or runner name into workflow
selection.

## Current Linux ARC capacity

All listed Linux classes are ephemeral, one-job ARC runners. Every class carries
the platform labels:

- `linux`
- `x64`
- `amd64`

### Capability inventory

| Purpose | Semantic profile API | Direct selector for central/infrastructure workflows | Main tools and resources | Trust boundary |
|---|---|---|---|---|
| Ordinary source checks, policy, lint, Python/Node scripts, Helm, and GitOps validation | `portable` | `[linux, amd64, general]` | Actions runner 2.336.0; 256 Mi / 1 Gi memory; 4 Gi local storage; 2 Gi disposable workspace | Tokenless, one job, ephemeral; may run untrusted source only when the workflow exposes no secret or privileged authority |
| Android, Gradle, Flutter-on-Linux, JDK 25, or Node 24 validation | `mobile` | `[linux, amd64, mobile]`; narrower installed-tool selectors are listed below | JDK/Javac 25; Flutter 3.44.8; Dart 3.12.2; Node 24.18.0; Android API/Build Tools 36 and 37; NDK 28.2.13676358; 2 / 4 Gi memory; 6 Gi workspace; 20 Gi scratch; managed 20 Gi dependency cache | Tokenless, one job; trusted PR or exact source because the managed cache is shared; does not prove a physical Android device is attached |
| Very small daemonless OCI work | `buildah-tiny` | `[linux, amd64, buildah, tiny]` | Buildah 1.33.7, Skopeo 1.13.3, Podman 4.9.3; 512 Mi / 1 Gi memory; 6 Gi local storage; cap 10 | Privileged Buildah pod; trusted exact source only; no Docker daemon, DinD, Kubernetes token, or Agent State credential |
| Small daemonless OCI work | `buildah-small` | `[linux, amd64, buildah, small]` | Same OCI tools; 512 Mi / 2 Gi memory; 16 Gi local storage; cap 6 | Same trusted exact-source boundary |
| Medium daemonless OCI work | `buildah-medium` | `[linux, amd64, buildah, medium]` | Same OCI tools; 2 / 4 Gi memory; 32 Gi local storage; cap 3 | Same trusted exact-source boundary |
| High-memory or high-storage daemonless OCI work | `buildah-high` | `[linux, amd64, buildah, high]` | Same OCI tools; 4 / 8 Gi memory; 44 Gi local storage; cap 1 | Same trusted exact-source boundary; use only with measured need |
| Protected Flux and Kubernetes reconciliation | `flux-control` | `[linux, amd64, flux-control]` | Actions runner plus Flux/Kubernetes tooling and a restricted service account | Repository-scoped to Flux; protected source only; never product pull-request source or general builds |

### General Linux

The semantic profile remains `portable` for API compatibility. Its resolved
GitHub selector is:

```yaml
runs-on: [linux, amd64, general]
```

Do not use `runs-on: portable`. `portable` is no longer a registered Linux ARC
scheduling label.

The repository Central self-check requests the semantic `portable` profile. It
verifies its pre-provisioned Linux runtime before checkout and does not install,
elevate, or persist a host runtime. The former emergency macOS exception is
retired and must not be restored.

### Android and mobile tools

The mobile class advertises these independent lowercase capabilities:

- `mobile`
- `android`
- `flutter`
- `jdk-25`
- `node-24`
- `nodejs`
- `linux`, `x64`, `amd64`

The broad semantic profile resolves to:

```yaml
runs-on: [linux, amd64, mobile]
```

Infrastructure-owned jobs may select a narrower installed tool:

```yaml
runs-on: [linux, amd64, android]
```

```yaml
runs-on: [linux, amd64, flutter]
```

```yaml
runs-on: [linux, amd64, jdk-25]
```

```yaml
runs-on: [linux, amd64, node-24]
```

These labels describe installed build tools. They do not prove that a phone,
emulator, simulator, signing identity, provisioning profile, or store
credential is present.

### Buildah runtime and size

`buildah` is the runtime property. Every Buildah class carries exactly one size
property:

- `tiny`
- `small`
- `medium`
- `high`

A direct selector must combine Linux, architecture, runtime, and exactly one
size:

```yaml
runs-on: [linux, amd64, buildah, tiny]
```

```yaml
runs-on: [linux, amd64, buildah, small]
```

```yaml
runs-on: [linux, amd64, buildah, medium]
```

```yaml
runs-on: [linux, amd64, buildah, high]
```

Do not use bare `buildah` in `runs-on`; it is shared by all four classes and is
therefore ambiguous. The semantic resolver may accept the API alias `buildah`
and map it to semantic profile `buildah-small`, but the emitted selector always
contains `buildah` plus `small`.

The strings `buildah-tiny`, `buildah-small`, `buildah-medium`, and
`buildah-high` remain semantic profile IDs only. They are not live combined
runner labels.

Select the smallest tier whose memory and local-storage limits cover measured
peaks plus reviewed headroom. Escalation evidence records:

- peak memory bytes;
- peak local-storage bytes;
- exact source SHA;
- workflow API;
- product ID.

Buildah is privileged and daemonless. Linux ARC does not provide Docker,
Docker-in-Docker, or a Docker-socket capability.

### Flux control

The repository-scoped Flux class advertises:

- `flux-control`
- `flux`
- `control-plane`
- `linux`, `x64`, `amd64`

Protected Flux-owned workflows use:

```yaml
runs-on: [linux, amd64, flux-control]
```

This class mounts restricted Kubernetes authority and cannot execute product
pull-request source, arbitrary caller commands, or general build work.

## Hard-cutover status

No deprecated Linux ARC scheduling alias remains registered. Do not use:

- `portable` as a `runs-on` label;
- `buildah-tiny`, `buildah-small`, `buildah-medium`, or `buildah-high` as
  combined labels;
- any `homelab-*-linux-x64` infrastructure identity as a label;
- bare `buildah`;
- bare `self-hosted`.

Old workflow examples using those selectors are defects and must be migrated to
the capability arrays in this guide. Internal ARC resource names may still
contain `homelab-*`; resource identity is not scheduling policy.

## Organization-managed macOS capacity

Apple work uses the semantic profile `apple`. Persistent organization hosts
advertise case-sensitive platform labels:

- `macOS`
- `ARM64`

Some hosts additionally advertise host-managed capabilities such as `android`,
`flutter`, `ios`, `python`, or `docker`. Those optional labels are not
guaranteed on every Mac. A bounded Apple or cross-platform workflow must either
use the central semantic contract or explicitly require every capability it
needs and verify the runtime before checkout or execution.

Examples for infrastructure-owned direct jobs:

```yaml
runs-on: [macOS, ARM64]
```

```yaml
runs-on: [macOS, ARM64, ios]
```

Do not infer signing identities, provisioning profiles, store credentials,
notarization credentials, simulators, or attached physical devices from a
macOS label. Persistent hosts require deterministic cleanup of workspaces,
DerivedData, result bundles, simulators, temporary files, and credentials.

Linux ARC cannot provide macOS, Xcode, iOS, or tvOS capacity. A `docker` label
on an organization-managed Mac does not create Docker or DinD capacity on the
Linux ARC classes.

## Guarded physical-device capacity

`physical-device` is a guarded overlay rather than an ordinary selectable runner
label. Android device work uses the mobile host class; iOS/tvOS device work uses
Apple capacity. Before device access, the workflow must have:

1. trusted authorization;
2. an exclusive lock for the exact device;
3. deterministic device-family and discovered-device identity;
4. exact tested source SHA;
5. bounded evidence identity;
6. cleanup evidence and lock release in an `always()` path.

Selecting `mobile`, `android`, `apple`, `macOS`, or `ios` never proves that a
physical device is attached or authorized.

## Selection by intent

| Intent | Semantic request |
|---|---|
| Policy, lint, source admission, ordinary Python/Node, Helm, or GitOps validation | `portable`, resolved to `[linux, amd64, general]` |
| Android or Gradle validation | `mobile`, resolved to `[linux, amd64, mobile]` |
| Flutter on Linux | `mobile` |
| Flutter or native Apple validation on macOS | `apple` |
| OCI build or publication | the smallest measured `buildah-*` semantic tier |
| Physical Android/iOS/tvOS validation | `physical-device` guarded overlay with authorization and locking |
| Flux reconciliation | `flux-control`, resolved to `[linux, amd64, flux-control]` |

A reusable workflow with more than one possible profile uses a protected
planning job. The planner validates semantic intent and emits a JSON selector;
a dependent job consumes that selector in `runs-on`. A composite action cannot
change the runner after a job is scheduled.

## Mandatory rules

- Never use bare `self-hosted`.
- Never use bare `buildah` as a complete direct selector.
- Never use deprecated combined or `homelab-*` Linux ARC labels.
- Never introduce Docker-capable or DinD selection for Linux ARC.
- Never combine incompatible semantic profiles in one job.
- Never accept runner labels from a workflow caller, issue, pull request,
  arbitrary matrix, or untrusted JSON input.
- Never treat a runner name as a scheduling contract.
- Untrusted source receives no registry-write, Agent State, signing,
  physical-device, SOPS, Kubernetes, production-database, or deployment
  credential.
- Flux owns concrete ARC infrastructure and Kubernetes authority.
  `ci-workflows` owns semantic workflow selection and resolver policy.
- Stable outputs describe results, digests, receipts, evidence IDs, and cleanup;
  they do not expose host identity or private infrastructure details.

Validate the semantic resolver and generated mapping with:

```text
python3 scripts/ci/runner_contract.py validate
python3 scripts/ci/runner_contract.py generate --check
```
