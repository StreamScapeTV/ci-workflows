# Contributing to ci-workflows

Thanks for improving `ci-workflows`. Contributions should make shared CI easier to use and audit without turning Central into a second owner of each product's build logic.

## Before opening a change

1. Read [README.md](README.md), [AGENTS.md](AGENTS.md) and the relevant [Repository CI guide](docs/repository-ci.md). Workflow YAML owns executable interfaces within the constraints of [feature specifications](feature-specs/README.md).
2. Search [existing issues](https://github.com/StreamScapeTV/ci-workflows/issues) and pull requests. For architectural simplification, use [#1076](https://github.com/StreamScapeTV/ci-workflows/issues/1076) rather than duplicating its roadmap.
3. Open a bounded proposal describing the observed problem, affected consumers, trust boundary, intended behavior and evidence required. Use the existing implementation issue form for a change requiring owner/Agent State coordination.
4. Check ownership. Product commands belong in the consuming repository's tracked `.ci/` files, not inside Central workflow-specific script branches. Do not change `feature-specs/**` without explicit owner authorization.

## Preparing a pull request

- Start from the repository's current protected integration branch and create a separate issue branch. Do not push changes directly to protected `main`.
- Keep changes narrow: reuse the generic Repository CI executor, existing actions and reviewed permissions before proposing a new workflow/profile. New capability routes need explicit issue scope and consumer proof.
- Preserve exact-source checkout, host-policy enforcement, secret isolation, private evidence redaction and workspace cleanup. Avoid unbounded caller-defined commands, environment maps, runner labels, secret names, and arbitrary URLs.
- Add negative/fail-closed tests when changing validation, trust, admission, cleanup or release behavior. Keep fixed contract fixtures in source tests instead of creating a separate GitHub Actions workflow per regression.
- Update documentation only where supported behavior actually changes. [`INVENTORY.yaml`](INVENTORY.yaml) is the sole local path inventory; don't create a second workflow directory/registry.
- Use the existing [pull-request template](.github/pull_request_template.md). Describe impacted consumers, source head, CI evidence, security boundaries and any required real-consumer acceptance.

The connected StreamScapeTV agent/CI infrastructure has additional review, branch publication and Agent State requirements. Those are maintainer-operated; external contributors should not attempt to recreate private keys, production credentials or a live Agent State session. Maintainers coordinate the protected merge and evidence checks.

## Local development and tests

Install Python 3 and PyYAML, then run only the modules relevant to your edit:

```bash
python3 -m pip install PyYAML
python3 -m unittest tests.test_repository_workflow tests.test_repository_execution_plan
```

If you change workflow YAML, parse it and the custom action YAML before submitting:

```bash
python3 - <<'PY'
from pathlib import Path
import yaml
for path in [*Path('.github/workflows').glob('*.yml'), *Path('actions').glob('*/action.yml')]:
    yaml.safe_load(path.read_text(encoding='utf-8'))
print('YAML parsed')
PY
```

The [self-check workflow](.github/workflows/self-check.yml) runs a wider source suite on pull requests. Some Mac, Android, device, registry and release proofs require authorized infrastructure; mention missing real-consumer evidence instead of claiming a local unit test proves it. Do not run a tag/release workflow merely to validate a documentation change.

## Security and privacy

Never commit or post GitHub Actions secrets, certificates, Apple signing identities, JWTs, private service endpoints, Tailscale credentials, device identifiers, protected source, or raw private CI logs. Use fixed nonsensitive failure categories and sanitized reproductions. Read [SECURITY.md](SECURITY.md) before disclosing a security defect.

## Review and compatibility

Maintainers may ask for a smaller PR, consumer compatibility evidence, or an independent review. Keep older public interfaces until their current callers are verified migrated; avoid aliases that merely preserve retired generic validation profiles. A PR merged to Central is not proof of successful deployment or product certification until that actual run takes place.

The project is provided under [MIT](LICENSE). By submitting a contribution, you agree it may be distributed under that license.
