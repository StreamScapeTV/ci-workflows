# GitOps source validation

`validation.gitops` version `1.0.0` is the product-neutral, source-only API for
YAML, Helm, Kustomize, changed-tree, and checked-in policy validation. Its
stable check is **CI / GitOps validation**. Planning and execution use the
semantic `portable` general-Linux profile. A caller never selects a host,
concrete label, tool URL, command, registry, cluster, namespace, service
account, or deployment target.

## Profiles

| Profile | Contract-owned work |
|---|---|
| `source-audit` | Exact source and optional checked-in bounded policy script; no renderer. |
| `yaml` | UTF-8/style, duplicate-key, YAML syntax, bounded JSON-schema, Kubernetes identity, and SOPS ciphertext/reference structure. |
| `helm-render` | Exact dependency lock, vendored dependency identity, values schema, required values, strict lint, deterministic template, and expected-render checks. |
| `kustomize-render` | Local root-restricted Kustomize graph inspection and deterministic build/render. |
| `changed-tree` | Select only contract targets intersecting an exact Git diff base. |
| `full` | All targets and the optional contract-owned policy script. |

The reusable workflow accepts only an exact admitted SHA, one registered
consumer/profile, an exact base SHA for `changed-tree`, the profile's exact
policy identifier, and the reserved empty artifact-exception field. Roots,
values, schemas, tasks, scripts, tool identities, and assertions are checked-in
contract data.

## Exact tools

The initial Linux contract installs and verifies:

- Helm 3.18.6 from the fixed official HTTPS archive and SHA-256;
- Kustomize 5.8.1 from the fixed upstream release archive and SHA-256;
- PyYAML 6.0.3 from the exact CPython 3.12 manylinux wheel and SHA-256.

Archives are bounded, digest-verified before extraction, and inspected for
absolute paths, traversal, duplicate destinations, links, devices, and FIFO
members. The installed binary or Python module must resolve beneath the
registered issue-owned state root and report the exact contract version.

## Helm dependencies

Network dependency updates are not part of source validation. A chart with
dependencies must have a matching `Chart.lock`, exact dependency versions, and
contract-identified vendored dependency trees. The synthetic fixture uses a
checked-in `file://` library chart and a deterministic tree digest. Producer
adoption that currently downloads a dependency, including `iptv-backend`, must
first establish a reviewed immutable vendoring or equivalent content-digest
contract; this workflow does not silently reproduce mutable repository access.
The current `agent-state` chart shape has no dependency and is represented by a
bounded chart consumer entry.

## SOPS boundary

SOPS validation inspects ciphertext and reference structure only. It requires
an encrypted MAC and encrypted `data` or `stringData` values. It rejects
plaintext values, private key material, and command residue. It will never
decrypt a SOPS document, receive an age/PGP/KMS key, or validate live secret
contents.

## Flux and chart consumers

Flux remains authoritative for desired state, target allowlists, policy,
decryption, credentials, and live reconciliation. The `flux-source` consumer
shape can validate checked-in YAML and changed paths but receives no Flux or
Kubernetes authority. `iptv-backend` and `agent-state` remain owners of their
chart values and product assertions; central validation supplies only the
bounded source/render machinery. No consumer repository change is part of
issue #15.

## Cleanup and artifacts

Every archive, installed tool, Python package, HOME, cache, temporary file, log,
render, and result lives under one marker-bound registered root. Cleanup uses
`lstat`, never follows symlinks, never accepts a caller deletion path, and
checks zero residue. A primary validation failure and a cleanup failure are
both retained as a bounded combined error. Source SHA, Git cleanliness, and a
deterministic source tree digest are reverified after policy and render work.
Routine execution retains **zero routine artifacts**.
