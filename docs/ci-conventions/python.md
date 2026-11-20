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
