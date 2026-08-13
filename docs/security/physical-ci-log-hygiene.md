# Physical CI log and durable-evidence hygiene

Physical-device CI runs on persistent infrastructure, so diagnostics must distinguish **stable product evidence** from **GitHub platform logs**.

## Stable product evidence

Durable evidence is the small machine-readable record that may be referenced from issues, reviews, release evidence, or another approved evidence store. It is validated by `contracts/physical-log-policy.json` and `ci_workflows.physical_log_policy` before publication.

The stable identity is deliberately limited to **repository + source SHA + run ID + job ID** plus the bounded device family, request/evidence IDs, result, cleanup result, and reviewed validation/toolchain profile IDs. Durable evidence must not contain raw runner or machine names, hostnames, provider identifiers, private workspace/home paths, device serials/UDIDs, credentials, endpoints, environment dumps, or copied command output.

Durable identifier fields also reject runner, machine, host, provider, serial, UDID, device-ID, and homelab-style fragments. This prevents a private runner/provider identity from being disguised as an otherwise syntactically safe request or evidence ID.

Raw log excerpts and raw log attachments are forbidden as durable evidence. Diagnostics that must survive the run use bounded structured markers and stable instruction/result codes instead of copying process output. The normal rule remains **zero routine artifacts**; any future retained diagnostic exception must be separately named, reviewed, bounded, redacted, and justified by its owning issue.

## GitHub platform logs

GitHub Actions may generate setup/bootstrap lines before repository workflow code executes. Those **GitHub platform logs** can include runner-identification or host/workspace information that central workflow source cannot reliably suppress. Workflow code must not copy those lines into issue comments, summaries, artifacts, evidence packets, or external systems.

Because the platform-generated copy cannot be eliminated by workflow code, the repository-administration boundary is:

- configure the **shortest repository-supported retention** for Actions logs/artifacts that still satisfies operational recovery requirements;
- restrict access to **repository maintainers** and other explicitly authorized repository administrators;
- never widen repository visibility or expose logs through a public evidence mirror;
- never quote platform runner/machine/path lines into durable evidence;
- keep product-created Actions artifacts at zero by default.

The exact repository retention setting is an administrative GitHub control, not a caller input, workflow secret, or source-controlled credential. Central workflow code records the requirement but does not attempt to acquire repository-administration authority merely to change it.

## Physical-device integration

`validation.device` may keep richer values in memory while selecting a device, fencing a physical resource, executing tests, restoring state, and cleaning up. Before any result becomes durable, that result must be projected through `physical_log_policy.validate_stable_evidence` or `physical_log_policy.render_stable_evidence`.

The central self-check contains a forward compatibility gate: when `src/ci_workflows/device_evidence.py` lands on the integration branch, its durable-publication path must explicitly use this boundary. That lets issue #14 continue on its separately owned branch while making later reconciliation fail closed if host/path-sensitive evidence bypasses the organization policy.

## Incident handling

If a future run exposes new private infrastructure metadata, do not preserve the sensitive line as evidence. Record the affected repository, exact source SHA, run ID, job ID, stable failure code, and remediation issue. If raw platform logs must be examined for incident response, keep that access inside the repository's authorized maintainer boundary and existing minimum-retention window.
