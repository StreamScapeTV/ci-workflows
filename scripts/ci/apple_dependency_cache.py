"""Identity and paths for native SwiftPM binary artifact caching on macOS."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import pwd
import re
import subprocess


def fingerprint(root: Path) -> str | None:
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    records = []
    for name in sorted(filter(None, tracked)):
        path = root / name
        if name.endswith('.xcodeproj/project.pbxproj'):
            text = path.read_text()
            for section in ('XCRemoteSwiftPackageReference', 'XCLocalSwiftPackageReference'):
                match = re.search(r'/\* Begin ' + section + r' section \*/(.*?)/\* End ' + section + r' section \*/', text, re.S)
                if match:
                    records.append((name + ':' + section, ' '.join(match.group(1).split()).encode()))
        elif path.name in ('Package.resolved', 'Package.swift') or (path.name.startswith('Package@swift-') and path.suffix == '.swift'):
            records.append((name, path.read_bytes()))
    if not records:
        return None
    digest = hashlib.sha256()
    for name, data in records:
        digest.update(name.encode() + b'\0' + data + b'\0')
    return digest.hexdigest()


def main() -> None:
    identity = fingerprint(Path.cwd())
    repository = os.environ['SOURCE_REPOSITORY']
    # Cache downloaded artifact bytes only, never netrc, Git config or build products.
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    artifact_path = home / 'Library/Caches/org.swift.swiftpm/artifacts'
    values = {'enabled': str(identity is not None).lower(), 'path': str(artifact_path)}
    if identity is not None:
        repository_key = hashlib.sha256(repository.encode()).hexdigest()[:16]
        values['key'] = f'apple-binary-artifacts-v1-{repository_key}-{platform.machine()}-{identity}'
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in values.items():
            output.write(f'{key}={value}\n')


if __name__ == '__main__':
    main()
