#!/usr/bin/env python3
"""Run canonical duplicate recovery with the existing publisher's Drive principal."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.parse

from scripts.ci.source_checkpoint_publish import (
    CheckpointPublishError, DriveClient, GitHubClient, SHA40, _checkpoint_ref_folder,
    select_latest_checkpoint_file,
)


def verify_remote(github, repository, branch, head, tree):
    repo = github._json(github.repo_path)
    value = github._json(f"{github.repo_path}/branches/{urllib.parse.quote(branch, safe='')}")
    if (repo.get('full_name') != repository or repo.get('default_branch') == branch
            or value.get('name') != branch or value.get('protected') is True
            or value.get('commit', {}).get('sha') != head
            or value.get('commit', {}).get('commit', {}).get('tree', {}).get('sha') != tree):
        raise CheckpointPublishError('checkpoint recovery requires unchanged exact non-protected GitHub head/tree')


def recover(drive, github, *, root_folder_id, repository, branch, head, tree, operation, rules):
    if operation not in {'inspect', 'settle'} or not SHA40.fullmatch(head) or not SHA40.fullmatch(tree):
        raise CheckpointPublishError('checkpoint recovery operation/head/tree is invalid')
    rules.safe_branch(branch, 'branch')
    if not rules.REPO.fullmatch(repository):
        raise CheckpointPublishError('checkpoint recovery repository is invalid')
    issue = re.match(r'issue-([1-9][0-9]*)-', branch)
    if issue is None:
        raise CheckpointPublishError('checkpoint recovery requires its existing issue branch')
    verify_remote(github, repository, branch, head, tree)
    folder = _checkpoint_ref_folder(drive, root_folder_id=root_folder_id, repository=repository, branch=branch)
    children = drive.children(folder['id'])
    groups = rules.checkpoint_rows(children, repository, branch)
    records = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        listing = root / 'listing.json'
        listing.write_text(json.dumps({'files': children}))
        command = [sys.executable, rules.__file__, repository, branch, '--issue', issue[1],
                   '--expected-head', head, '--expected-tree', tree, '--drive-listing', str(listing)]
        for sequence, items in sorted(groups.items()):
            for index, item in enumerate(items):
                data = drive.media(item['id'])
                path = root / f'{sequence}-{index}.zip'
                path.write_bytes(data)
                actual_tree, metadata = rules.materialize_and_tree(path)
                if metadata is not None and (
                        metadata.get('repository') != repository or metadata.get('branch') != branch
                        or metadata.get('sequence') != sequence or metadata.get('tree_sha') != actual_tree):
                    raise CheckpointPublishError('checkpoint recovery embedded identity mismatch')
                expected_parent = metadata.get('expected_head') if metadata else None
                if expected_parent is not None and (not isinstance(expected_parent, str) or not SHA40.fullmatch(expected_parent)):
                    raise CheckpointPublishError('checkpoint recovery embedded expected parent is invalid')
                records.append({'id': item['id'], 'sequence': sequence,
                                'archive_size_bytes': len(data), 'archive_sha256': rules.sha256(path),
                                'tree_sha': actual_tree, 'expected_parent': expected_parent,
                                'embedded_ref': metadata.get('branch') if metadata else None,
                                'relationship': 'github-current' if actual_tree == tree else 'noncurrent'})
                command += ['--archive', f"{item['id']}={path}"]
        duplicates = any(len(items) > 1 for items in groups.values())
        plan = {'ok': True, 'connector_sequence': []}
        if duplicates:
            result = subprocess.run(command, text=True, capture_output=True)
            if result.returncode not in {0, 2}:
                raise CheckpointPublishError('canonical checkpoint recovery comparison failed')
            try:
                plan = json.loads(result.stdout)
            except json.JSONDecodeError:
                raise CheckpointPublishError('canonical checkpoint recovery returned no comparison receipt') from None
        receipt = {'repository': repository, 'branch': branch, 'head_sha': head, 'tree_sha': tree,
                   'operation': operation, 'checkpoints': records, 'deleted_ids': [],
                   'safe_to_settle': plan.get('ok') is True,
                   'rules_sha': subprocess.check_output(['git', '-C', str(Path(rules.__file__).parents[2]),
                                                         'rev-parse', 'HEAD'], text=True).strip()}
        # Publish evidence before mutation, including when conflicting archives must be retained.
        print(json.dumps(receipt, sort_keys=True), flush=True)
        if not receipt['safe_to_settle']:
            raise CheckpointPublishError('conflicting or unbound checkpoint trees preserved; inspect comparison receipt')
        if operation == 'settle':
            verify_remote(github, repository, branch, head, tree)
            before = rules.checkpoint_rows(drive.children(folder['id']), repository, branch)
            identities = lambda values: sorted((seq, item['id'], item['name'], item.get('size'))
                                               for seq, items in values.items() for item in items)
            if identities(before) != identities(groups):
                raise CheckpointPublishError('checkpoint listing changed before recovery; nothing deleted')
            observed = {record['id'] for record in records}
            for step in plan.get('connector_sequence', []):
                # The canonical helper emits exact redundant object URLs, never caller-selected IDs.
                match = re.fullmatch(r'https://drive.google.com/file/d/([A-Za-z0-9_-]+)/view', step.get('payload', {}).get('url', ''))
                if step.get('action') != 'Google_Drive.delete_file' or match is None or match[1] not in observed:
                    raise CheckpointPublishError('canonical recovery returned an unexpected action')
                verify_remote(github, repository, branch, head, tree)
                drive.delete(match[1])
                receipt['deleted_ids'].append(match[1])
        after = drive.children(folder['id'])
        after_groups = rules.checkpoint_rows(after, repository, branch)
        after_ids = {item['id'] for items in after_groups.values() for item in items}
        expected_ids = {record['id'] for record in records} - set(receipt['deleted_ids'])
        if after_ids != expected_ids:
            raise CheckpointPublishError('checkpoint recovery readback did not confirm exact retained objects')
        verify_remote(github, repository, branch, head, tree)
        receipt['unique_sequences'] = all(len(items) == 1 for items in after_groups.values())
        if after_groups and receipt['unique_sequences']:
            latest, _ = select_latest_checkpoint_file(after, repository=repository, branch=branch)
            receipt['next_sequence'] = latest + 1
        elif not after_groups:
            receipt['next_sequence'] = 1
        if operation == 'settle' and not receipt['unique_sequences']:
            raise CheckpointPublishError('checkpoint recovery readback still contains duplicate sequences')
        receipt['readback_verified'] = True
        print(json.dumps(receipt, sort_keys=True), flush=True)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('repository', 'branch', 'head', 'tree'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--operation', choices=['inspect', 'settle'], required=True)
    args = parser.parse_args()
    # Fixed checkout owned by this workflow, not a caller-selectable script or source path.
    path = Path('canonical-rules/utilities/tools/checkpoint_recovery.py').resolve()
    spec = importlib.util.spec_from_file_location('canonical_checkpoint_recovery', path)
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    try:
        receipt = recover(DriveClient(os.environ['GOOGLE_DRIVE_ACCESS_TOKEN']),
                          GitHubClient(os.environ['TARGET_TOKEN'], args.repository),
                          root_folder_id=os.environ['GOOGLE_DRIVE_ROOT_FOLDER_ID'],
                          repository=args.repository, branch=args.branch, head=args.head,
                          tree=args.tree, operation=args.operation, rules=rules)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a') as output:
                output.write('Verified publisher-principal checkpoint recovery\n\n```json\n' + json.dumps(receipt, indent=2) + '\n```\n')
    except (CheckpointPublishError, SystemExit) as error:
        raise SystemExit(str(error)) from None


if __name__ == '__main__':
    main()
