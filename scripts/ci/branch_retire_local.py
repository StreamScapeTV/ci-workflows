#!/usr/bin/env python3
"""Local paired retirement using the reviewed workflow guard and Drive Trash.

No Actions admission, arbitrary command, Drive ID, or API endpoint is accepted.
Credentials come only from the existing local gh session and Drive access token.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.parse
import urllib.request

import yaml
from source_snapshot_delete import DriveClient, SnapshotDeleteError, delete_snapshot, validate_request

ROOT = Path(__file__).resolve().parents[2]


def workflow_guard() -> str:
    """Use the production guard verbatim, rather than a drifting local copy."""
    workflow = yaml.safe_load((ROOT / '.github/workflows/branch-delete.yml').read_text())
    steps = workflow['jobs']['delete']['steps']
    matches = [step['run'] for step in steps if step.get('name') == 'Delete exact eligible branch']
    if len(matches) != 1:
        raise SnapshotDeleteError('reviewed branch guard is missing or ambiguous')
    prefix = "set -Eeuo pipefail\npython3 - <<'PY'\n"
    script = matches[0]
    if not script.startswith(prefix) or not script.endswith('\nPY\n'):
        raise SnapshotDeleteError('reviewed branch guard layout changed; local route requires review')
    return script[len(prefix):-len('\nPY\n')]


class PreflightDrive:
    """Run the existing namespace/content validation without performing Trash."""
    def __init__(self, client):
        self.client = client
        self.validated_folder = None

    def exact_folders(self, parent, name):
        return self.client.exact_folders(parent, name)

    def children(self, parent):
        return self.client.children(parent)

    def media(self, file_id):
        return self.client.media(file_id)

    def trash(self, file_id):
        self.validated_folder = file_id


def branch_absent(repository, branch, token):
    encoded = urllib.parse.quote(branch, safe='')
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}/branches/{encoded}',
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28'})
    import urllib.error
    try:
        with urllib.request.urlopen(request, timeout=20):
            return False
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return True
        raise SnapshotDeleteError('GitHub absence readback was refused') from None
    except urllib.error.URLError:
        raise SnapshotDeleteError('GitHub absence readback failed') from None


def retire(repository, branch, expected_head, *, github_token, drive_token, root_folder_id):
    validate_request(repository, branch, expected_head)
    if not expected_head or not github_token or not drive_token or not root_folder_id:
        raise SnapshotDeleteError('local GitHub session, Drive access token, configured repositories root and exact head are required')
    subprocess.run(['git', 'check-ref-format', '--branch', branch], check=True, capture_output=True)
    guard = workflow_guard()
    client = DriveClient(drive_token)
    kwargs = dict(root_folder_id=root_folder_id, repository=repository, ref=branch,
                  allow_checkpoint_only_without_manifest=True)
    preflight = PreflightDrive(client)
    delete_snapshot(preflight, **kwargs)  # Both systems must be accessible before mutation.
    env = dict(os.environ, TARGET_REPOSITORY=repository, TARGET_BRANCH=branch,
               EXPECTED_HEAD=expected_head, TARGET_TOKEN=github_token)
    with tempfile.TemporaryDirectory(prefix='branch-retire-') as temporary:
        env['GITHUB_OUTPUT'] = str(Path(temporary) / 'output')
        # Fixed repository-owned production guard only; no caller script or command.
        run = subprocess.run([os.sys.executable, '-c', guard], env=env, capture_output=True, text=True)
    if run.returncode:
        raise SnapshotDeleteError('reviewed GitHub branch guard refused retirement; Drive was not changed')
    if not branch_absent(repository, branch, github_token):
        raise SnapshotDeleteError('GitHub branch absence is unconfirmed; Drive was not changed')
    # Revalidate the live namespace immediately before recoverable Trash.
    result = delete_snapshot(client, **kwargs)
    if not branch_absent(repository, branch, github_token):
        raise SnapshotDeleteError('GitHub branch reappeared; paired retirement is incomplete')
    repository_folders = client.exact_folders(root_folder_id, repository.split('/')[1])
    if len(repository_folders) != 1 or client.exact_folders(repository_folders[0]['id'], branch):
        raise SnapshotDeleteError('same-ref Drive absence is unconfirmed')
    if preflight.validated_folder:
        value = client._json('/files/' + urllib.parse.quote(preflight.validated_folder, safe='') + '?fields=id,trashed')
        if value.get('id') != preflight.validated_folder or value.get('trashed') is not True:
            raise SnapshotDeleteError('exact Drive folder Trash readback is unconfirmed')
    return dict(repository=repository, branch=branch, branch_absent=True,
                drive_result=result, drive_verified=True, actions_requested=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    try:
        token = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True, check=True).stdout.strip()
        result = retire(args.repository, args.branch, args.expected_head, github_token=token,
            drive_token=os.environ.get('GOOGLE_DRIVE_ACCESS_TOKEN', ''),
            root_folder_id=os.environ.get('GOOGLE_DRIVE_ROOT_FOLDER_ID', ''))
    except (SnapshotDeleteError, subprocess.SubprocessError, OSError):
        raise SystemExit('Local paired retirement failed or remains unconfirmed; do not infer success or retry a mutation without readback.') from None
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
