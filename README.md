# ci-workflows

`ci-workflows` provides reusable GitHub Actions execution and infrastructure for projects in the [StreamScapeTV](https://github.com/StreamScapeTV) organization. The repository's GitHub address is [`StreamScapeTV/ci-workflows`](https://github.com/StreamScapeTV/ci-workflows); its Agent State project key is `ci-workflows`.

The goal is a **small, predictable, product-neutral CI layer**. Each consuming repository owns its build commands and tests. Central CI handles trusted request admission, host selection, source checkout, permitted infrastructure credentials, bounded evidence, and cleanup.

> **Availability:** The source is MIT-licensed, but some execution paths depend on organization-operated Agent State, private GitHub/registry permissions, macOS devices, or other owner-configured services. The repository is not a plug-and-play hosted CI service. Contributors do not need access to those services to propose source or documentation changes.

## How repository-owned CI works

For ordinary validation, a project checks in executable entrypoints under its own `.ci/` directory:

| Operation | Tracked product-owned entrypoint |
| --- | --- |
| Build | `.ci/build.sh` |
| Focused tests | `.ci/test.sh` |
| Full validation | `.ci/test-full.sh` |
| UI validation | `.ci/test-ui.sh` |
| Authorized release | `.ci/release.sh` |
| Authorized physical device testing, where supported | `.ci/device-test.sh` |

The normal `validation.repository` route executes a reviewed operation, not an arbitrary caller-provided command. A tracked `.ci/execution-plan.json` can select a finite approved OS set when one operation needs Linux and macOS. Central chooses the actual runner class and injects only capabilities authorized by trusted configuration. Product repositories do not choose runner labels, secret names, raw shell commands, or credential mappings.

See [Repository CI documentation](docs/repository-ci.md) for the exact consumer contract. The workflow YAML and [machine-readable contract](contracts/repository-ci-v1.json) define the current runtime behavior; this README is orientation, not a competing API catalogue.

## Why does the Actions graph look large?

[`central-ci-dispatch.yml`](.github/workflows/central-ci-dispatch.yml) is a single admission front door with *conditional routes* for the different supported operations. GitHub's visualization draws potential branches, including jobs that are **skipped** on a particular run. It does not mean all routes execute each time.

The default route for ordinary build/test/full/UI work is:

`Agent State request → central-ci-dispatch → repository-plan → repository → product .ci/* → private evidence / cleanup / settlement`

Some separately governed paths remain because they have a genuinely different trust, platform, or release boundary:

- **Specialized evidence:** Apple candidate/screenshot review and other currently authorized native/device-validation exceptions, until live consumers migrate safely.
- **Release/publication:** tag-controlled image, chart, library, and app distribution paths, with publication-specific permissions and readback.
- **Infrastructure:** source snapshot/checkpoint publication, branch maintenance, approved inspection, and private evidence retention.
- **Repository self-validation:** workflow tests and runner-image checks, independent of a product's `.ci/` scripts.

These paths are not a reason to keep legacy *ordinary* product build/test profiles. Those were already pruned as repositories adopted `.ci/`. Further simplification—including reducing visible dispatch complexity and retiring unused specialist routes—is tracked in [#1076](https://github.com/StreamScapeTV/ci-workflows/issues/1076), with existing consumer and dependency gates. Do not remove a workflow purely because its job appears in the Actions graph.

The authoritative **path inventory** is [`INVENTORY.yaml`](INVENTORY.yaml). We intentionally do not maintain another list of workflow filenames in this README.

## Repository layout

| Location | Responsibility |
| --- | --- |
| `.github/workflows/` | GitHub-required dispatcher, callable workflows, and distinct event/permission boundaries |
| `actions/` | Reusable Central actions where repeated mechanics justify them |
| `scripts/ci/` | Source validation, bounded CI helpers, private evidence and lifecycle mechanics |
| `contracts/` | Reviewed machine-readable consumer contracts |
| `tests/` | Source, workflow, security and regression checks |
| `docs/` | Consumer guidance and capability audits |
| `feature-specs/` | Owner-controlled canonical feature specifications |
| `AGENTS.md` | Repository-specific engineering and authority rules |

## Developing and contributing

You can inspect workflows and run focused checks without connecting to private StreamScapeTV services. For example, with Python 3 and PyYAML installed:

```bash
python3 -m pip install PyYAML
python3 -m unittest tests.test_repository_workflow tests.test_repository_execution_plan
```

GitHub's [CI workflows self-check](.github/workflows/self-check.yml) runs the broader source and contract suite. A passing source check does **not** prove a real device, registry publication, private-network path, or deployed application.

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. For help, see [SUPPORT.md](SUPPORT.md); report security concerns through [SECURITY.md](SECURITY.md). Participants follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Released under the [MIT License](LICENSE). The existing copyright and license terms remain unchanged.
