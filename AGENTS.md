# AGENTS.md — StreamScapeTV/ci-workflows

## Repository authority

- Repository: `StreamScapeTV/ci-workflows`
- Agent State project key: `ci-workflows`
- Protected integration branch: `main`
- Shared organization policy: `StreamScapeTV/organization-rules@main/AGENTS.yaml`
- Local path inventory: `INVENTORY.yaml`
- Google Drive repository folder ID: `1--JcV6RK8jdIIP3ONWw420QDVpNTQ7L8`

Read the shared organization entry point and the authorities it routes. It owns
generic working-copy, Agent State, Google Drive, branch/PR, cross-project,
validation, merge, and cleanup rules. This file adds only `ci-workflows`-specific
constraints.

## Workflow architecture

Keep Central CI small, conventional, and fixed-profile.

- Workflow YAML is the source of truth for each workflow's inputs, secrets,
  outputs, permissions, jobs, and behavior.
- Product repositories remain thin callers. Expose only bounded semantic
  selectors and parameters required by a reviewed profile.
- Never expose arbitrary commands, script paths, environment maps, runner or
  host selectors, secret names, registry/network configuration, or equivalent
  escape hatches to callers.
- Keep GitHub-required workflow entrypoints in `.github/workflows/`. Do not
  mirror workflow APIs into generated contracts, compatibility registries, or
  parallel inventories.
- `INVENTORY.yaml` is the only local path inventory; keep it path-oriented.
- Put custom actions in `actions/` only for genuinely repeated Central mechanics
  that are awkward to express directly in the workflows. Do not create wrapper
  actions or frameworks for one workflow step or one technology command.
- Keep product command detail product-owned. Central selects fixed reviewed
  profiles rather than accepting caller-supplied execution detail.

## Validation

Keep self-validation proportional to the changed behavior:

1. parse affected workflow/action YAML;
2. run the smallest focused repository tests needed by the change;
3. self-review the complete affected Central path;
4. when runtime behavior materially changes, prove it with a real product
   consumer before treating the candidate as final.

During iterative Android/Apple real-consumer proof of a Central change, prefer
the deployed `targeted-tests` profile with only the relevant safe selectors and
fixed platform when that evidence covers the changed behavior. Reserve broad
native `full` validation for the final exact-head candidate/acceptance gate,
unless issue or product authority explicitly requires broader evidence earlier.
Read the exact profile/input contract from the selected workflow YAML; do not
copy it into this file or `INVENTORY.yaml`.

A green self-check proves Central source consistency, not an external product,
publication, deployment, signing, or physical-device result that did not run.

## Simplicity

Prefer deletion and direct workflow behavior over compatibility machinery. Do
not add a framework, adapter, contract mirror, inventory, workflow, action, or
test layer unless a current Central capability genuinely needs it.
