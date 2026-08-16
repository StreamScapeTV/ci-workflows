#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import zipfile


def usage() -> int:
    print("usage: zip [-qr] ARCHIVE.zip PATH...", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if argv in (["--version"], ["-v"]):
        print("ci-workflows zip 1.0 (Python zipfile)")
        return 0

    quiet = False
    recursive = False
    positional: list[str] = []
    for argument in argv:
        if argument.startswith("-") and argument != "-":
            for option in argument[1:]:
                if option == "q":
                    quiet = True
                elif option == "r":
                    recursive = True
                else:
                    return usage()
        else:
            positional.append(argument)
    if len(positional) < 2:
        return usage()

    archive_path = Path(positional[0])
    sources = [Path(value) for value in positional[1:]]
    if archive_path.suffix.lower() != ".zip":
        archive_path = archive_path.with_suffix(archive_path.suffix + ".zip")

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            if not source.exists():
                print(f"zip: missing path: {source}", file=sys.stderr)
                return 12
            if source.is_dir():
                if not recursive:
                    print(f"zip: {source} is a directory (use -r)", file=sys.stderr)
                    return 12
                for path in sorted(source.rglob("*")):
                    if path.is_symlink():
                        continue
                    archive.write(path, path.as_posix())
                    if not quiet:
                        print(f"  adding: {path}")
            else:
                archive.write(source, source.as_posix())
                if not quiet:
                    print(f"  adding: {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
