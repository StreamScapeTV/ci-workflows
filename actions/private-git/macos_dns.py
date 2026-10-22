"""Preserve hosted macOS DNS across the upstream Tailscale action's rewrite."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def read_settings(option: str) -> list[str]:
    result = subprocess.check_output(['networksetup', option, 'Ethernet'], text=True).strip()
    # networksetup reports DHCP/default settings as a sentence, not an empty list.
    return [] if result.startswith("There aren't any ") else result.splitlines()


def capture() -> None:
    settings = {'dns': read_settings('-getdnsservers'), 'search': read_settings('-getsearchdomains')}
    descriptor, name = tempfile.mkstemp(prefix='central-runner-dns-', suffix='.json', dir=os.environ['RUNNER_TEMP'])
    with os.fdopen(descriptor, 'w') as output:
        json.dump(settings, output)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'state_path={name}\n')


def restore() -> None:
    path = Path(os.environ['DNS_STATE'])
    settings = json.loads(path.read_text())
    for key, option in [('dns', '-setdnsservers'), ('search', '-setsearchdomains')]:
        subprocess.run(['sudo', 'networksetup', option, 'Ethernet', *(settings[key] or ['Empty'])], check=True)
    path.unlink()
    print('Original hosted macOS DNS and search domains restored; Tailscale connection retained')


if __name__ == '__main__':
    {'capture': capture, 'restore': restore}[sys.argv[1]]()
