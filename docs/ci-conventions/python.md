# Convention: Python — workflow `ci-python.yml`

A repository chosen for the Python workflow needs **no `.ci` directory** if it provides a tracked
`pyproject.toml` or `requirements.txt` at the repository root. The workflow runs on `ubuntu-latest`
with the system `python3`, in an isolated virtual environment (`.ci-venv`), with the private package
registry reached through the ephemeral `.netrc` (Tailscale joined by the shared setup action).

| Stage (operation) | Command | When it runs |
| --- | --- | --- |
| `lint` | `ruff check .` | `[tool.ruff]` in `pyproject.toml`, or a tracked `ruff.toml`; otherwise skipped (green, with the reason in the log) |
| `build` | `python -m build` | `pyproject.toml` exists; otherwise skipped |
| `test` | `python -m pytest -q`, or `python -m unittest discover -s tests` when pytest is not declared | a tracked `tests/` directory exists; otherwise skipped |

Dependencies: `requirements-dev.txt`, else `requirements.txt`, else `pip install -e '.[dev]'` (falling
back to `pip install -e .`). Pin versions in the manifest; the workflow keeps **no Python cache**
(owner decision: only Gradle, Xcode DerivedData, SwiftPM and Maven are cached).

Transition: a tracked executable `.ci/lint.sh`, `.ci/build.sh` or `.ci/test.sh` wins over the
convention until the project follows the standard. Product specifics belong in the project's own
tooling (pyproject), never in a CI script.

## Where the output goes

The tool output of every stage is written only to the S3 stage log (`logs/<repo>/<request_id>/<stage>/<seq>.log`, `final.log`) and never to the Actions log of this repository. The Actions step shows only status lines: `== stage <name> started s3=<prefix> ==` and `== stage <name> finished result=ok|failed|skipped exit=N s3=<prefix> ==`. Tool `::error::` lines stay inside the S3 log. A log stream that cannot start or finish fails the job (the log would be lost).

## One job, parallel stages

All requested stages run in ONE job (`python / stages`) and do not wait for each other. Python: one venv with the dependencies and the lint/build tools is created once, then `ruff` and the tests/build run as parallel processes of the same job (each with its own S3 log stream). A stage without a convention is `skipped`. A failed install fails every stage that needed it. A failed `lint` is a warning (reported, findings in the S3 log) and never fails a `test` or `build` request; `test` or `build` failing fails the request; when `lint` itself is the requested operation a failure fails it. With transition scripts (`.ci/*.sh`) the stages run one after the other, build first, and a failed stage never stops the next.
