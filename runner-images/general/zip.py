#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import sys
import zipfile


def usage() -> int:
    print("usage: zip [-rjq0-9] ARCHIVE FILE...", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if argv in (["--version"], ["-v"]):
        print("ci-workflows zip 1.0 (Python zipfile)")
        return 0

    recursive = False
    junk_paths = False
    quiet = False
    compression = zipfile.ZIP_DEFLATED
    compresslevel = 6
    positional: list[str] = []
    for argument in argv:
        if argument == "--":
            continue
        if argument.startswith("-") and len(argument) > 1:
            for option in argument[1:]:
                if option == "r":
                    recursive = True
                elif option == "j":
                    junk_paths = True
                elif option == "q":
                    quiet = True
                elif option == "0":
                    compression = zipfile.ZIP_STORED
                    compresslevel = None
                elif option.isdigit() and option != "0":
                    compresslevel = int(option)
                else:
                    return usage()
            continue
        positional.append(argument)

    if len(positional) < 2:
        return usage()

    archive = Path(positional[0])
    sources = [Path(value) for value in positional[1:]]
    archive.parent.mkdir(parents=True, exist_ok=True)
    files: list[tuple[Path, str]] = []
    for source in sources:
        if source.is_dir():
            if not recursive:
                continue
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                arcname = path.name if junk_paths else os.fspath(path)
                files.append((path, arcname))
        elif source.is_file():
            arcname = source.name if junk_paths else os.fspath(source)
            files.append((source, arcname))
        else:
            print(f"zip: missing input: {source}", file=sys.stderr)
            return 12

    with zipfile.ZipFile(
        archive,
        mode="w",
        compression=compression,
        compresslevel=compresslevel,
    ) as output:
        for path, arcname in files:
            output.write(path, arcname)
            if not quiet:
                print(f"  adding: {arcname}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
