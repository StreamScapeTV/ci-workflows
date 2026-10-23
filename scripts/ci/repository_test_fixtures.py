"""Materialize the existing fixed test bundle after trusted capability admission."""
import json
import os
from pathlib import Path


FIELDS = (
    "STREAMSCAPE_API_BASE", "STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD",
    "STREAMSCAPE_DEMO_EMAIL", "STREAMSCAPE_DEMO_PASSWORD",
    "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD",
)


def main() -> None:
    path = Path(os.environ["RUNNER_TEMP"]) / "central-test-fixtures.json"
    # Provider-required fields and fixture semantics belong to the product.
    previous_umask = os.umask(0o077)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump({name: os.environ.get(name, "") for name in FIELDS}, stream)
            stream.write("\n")
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    main()
