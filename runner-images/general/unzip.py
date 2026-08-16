#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import zipfile


def usage() -> int:
    print("usage: unzip [-oqt] [-d DIR] ARCHIVE [MEMBER...]", file=sys.stderr)
    return 2


def safe_destination(root: Path, member: str) -> Path:
    relative = Path(member)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(member)
    destination = (root / relative).resolve()
    root_resolved = root.resolve()
    if destination != root_resolved and root_resolved not in destination.parents:
        raise ValueError(member)
    return destination


def main(argv: list[str]) -> int:
    if argv in (["--version"], ["-v"]):
        print("ci-workflows unzip 1.0 (Python zipfile)")
        return 0

    quiet = False
    overwrite = False
    test_only = False
    destination = Path(".")
    positional: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            positional.extend(argv[index + 1 :])
            break
        if argument == "-d":
            index += 1
            if index >= len(argv):
                return usage()
            destination = Path(argv[index])
        elif argument.startswith("-") and len(argument) > 1:
            for option in argument[1:]:
                if option == "q":
                    quiet = True
                elif option == "o":
                    overwrite = True
                elif option == "t":
                    test_only = True
                else:
                    return usage()
        else:
            positional.append(argument)
        index += 1

    if not positional:
        return usage()
    archive_path = Path(positional[0])
    requested = positional[1:]
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = requested or archive.namelist()
            for name in names:
                info = archive.getinfo(name)
                if test_only:
                    with archive.open(info) as stream:
                        while stream.read(1024 * 1024):
                            pass
                    continue
                output = safe_destination(destination, info.filename)
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists() and not overwrite:
                    print(f"unzip: target exists: {output}", file=sys.stderr)
                    return 1
                with archive.open(info) as source, output.open("wb") as target:
                    while block := source.read(1024 * 1024):
                        target.write(block)
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    output.chmod(mode)
                if not quiet:
                    print(f"  inflating: {output}")
    except (FileNotFoundError, KeyError, ValueError, zipfile.BadZipFile) as error:
        print(f"unzip: {error}", file=sys.stderr)
        return 9
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
