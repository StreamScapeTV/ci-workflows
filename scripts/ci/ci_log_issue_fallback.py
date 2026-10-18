"""Keep a scrubbed run log on its exact private owning issue after Drive failure."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid

API = 'https://api.github.com'
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_COMMENT_BYTES = 60000


class FallbackError(ValueError):
    pass


def request(url, token, payload=None, *, state=False):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'central-ci-log-fallback'}
    headers.update({'apikey': token, 'Content-Profile': 'agent_api', 'Accept-Profile': 'agent_api'}
                   if state else {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    data = json.dumps(payload).encode() if payload is not None else None
    # Only provenance/read calls are replayable. A comment POST keeps the
    # existing exact-part readback path after any uncertain delivery.
    read_only = state or payload is None
    family = 'Agent State lookup' if state else 'GitHub read' if read_only else 'GitHub comment POST'
    for attempt in range(11 if read_only else 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            category = 'http_' + str(error.code)
            retryable = error.code == 429 or 500 <= error.code <= 599
        except OSError as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            category = ('dns_resolution_failed' if isinstance(reason, socket.gaierror)
                        else 'timeout' if isinstance(reason, TimeoutError)
                        else 'tls_certificate_failed' if isinstance(reason, ssl.SSLCertVerificationError)
                        else 'transport')
            retryable = category != 'tls_certificate_failed'
        except ValueError:
            category, retryable = 'invalid_json_response', False
        if read_only and retryable and attempt < 10:
            time.sleep(2)
            continue
        raise FallbackError(f'{family}: request_failed_{category} after {attempt + 1} attempt(s)') from None


def resolve(env, call=request):
    repository = env['LOG_REPOSITORY']
    if not re.fullmatch(r'StreamScapeTV/[A-Za-z0-9_.-]+', repository):
        raise FallbackError('invalid_owning_repository')
    run_id = str(uuid.UUID(env['LOG_CI_RUN_ID']))
    root = env['AGENT_STATE_SUPABASE_URL'].rstrip('/') + '/rest/v1/rpc/'
    token = env['AGENT_STATE_SUPABASE_SECRET_KEY']
    result = call(root + 'list_current_ci_runs', token, {'p_filter': {
        'repository': repository, 'ref': env['LOG_REF'], 'limit': 100}}, state=True)
    if result.get('ok') is not True or not isinstance(result.get('runs'), list):
        raise FallbackError('ci_lookup_unavailable')
    matches = [r for r in result['runs'] if r.get('ci_run_id') == run_id]
    if len(matches) != 1:
        raise FallbackError('exact_ci_run_unavailable')
    result = call(root + 'get_ci_run', token, {'p_project_key': matches[0]['project_key'],
                  'p_ci_run_id': run_id}, state=True)
    run = result.get('run')
    if result.get('ok') is not True or not isinstance(run, dict):
        raise FallbackError('exact_ci_run_unavailable')
    issue = run.get('issue_number')
    source = run.get('observed_source_sha')
    if (run.get('ci_run_id') != run_id or run.get('repository') != repository
            or run.get('ref') != env['LOG_REF'] or type(issue) is not int or issue < 1
            or run.get('external_repository') != env['GITHUB_REPOSITORY']
            or str(run.get('external_run_id')) != env['GITHUB_RUN_ID']
            or str(run.get('external_run_attempt')) != env['GITHUB_RUN_ATTEMPT']
            or not isinstance(source, str) or not re.fullmatch(r'[a-f0-9]{40}', source)):
        raise FallbackError('ci_provenance_mismatch')
    lane = env['LOG_LANE']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', lane):
        raise FallbackError('invalid_log_lane')
    return dict(repository=repository, issue=issue, ci_run_id=run_id, source_sha=source,
                run_id=env['GITHUB_RUN_ID'], attempt=env['GITHUB_RUN_ATTEMPT'],
                execution_repository=env['GITHUB_REPOSITORY'], lane=lane)


def bodies(context, log):
    """Preserve the full log, escaping HTML so log content cannot escape code."""
    pieces, offset = [], 0
    while offset < len(log):
        end = min(offset + 50000, len(log))
        while len(html.escape(log[offset:end]).encode()) > 50000:
            end = offset + (end - offset) // 2
        pieces.append(html.escape(log[offset:end]))
        offset = end
    digest = hashlib.sha256(log.encode()).hexdigest()
    run_url = f"https://github.com/{context['execution_repository']}/actions/runs/{context['run_id']}/attempts/{context['attempt']}"
    for index, piece in enumerate(pieces, 1):
        marker = f"<!-- central-ci-log:{context['ci_run_id']}:{context['attempt']}:{context['lane']}:{digest}:{index} -->"
        body = (f"{marker}\nGoogle Drive log upload failed. Scrubbed CI log retained here.\n\n"
                f"[Run {context['run_id']}, attempt {context['attempt']}]({run_url}); "
                f"lane `{context['lane']}`; source `{context['source_sha']}`. "
                "The original CI verdict is unchanged.\n\n"
                f"<details><summary>Log part {index}/{len(pieces)}</summary>\n\n"
                f"<pre>{piece}</pre>\n</details>")
        if len(body.encode()) > MAX_COMMENT_BYTES:
            raise FallbackError('comment_too_large')
        yield body


def comments(endpoint, token, call):
    found = []
    for page in range(1, 101):
        values = call(f'{endpoint}?per_page=100&page={page}', token)
        if not isinstance(values, list):
            raise FallbackError('comments_lookup_invalid')
        found.extend(values)
        if len(values) < 100:
            return found
    raise FallbackError('comments_lookup_incomplete')


def post(context, path, token, call=request, pause=time.sleep):
    repository = context['repository']
    repo = call(f'{API}/repos/{repository}', token)
    if repo.get('private') is not True or repo.get('full_name') != repository:
        raise FallbackError('private_owning_repository_required')
    issue_url = f"{API}/repos/{repository}/issues/{context['issue']}"
    issue = call(issue_url, token)
    if (issue.get('number') != context['issue'] or 'pull_request' in issue
            or issue.get('repository_url') != f'{API}/repos/{repository}'):
        raise FallbackError('owning_issue_mismatch')
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_LOG_BYTES:
        raise FallbackError('scrubbed_log_unavailable_or_oversized')
    try:
        log = path.read_text(encoding='utf-8')
    except UnicodeError:
        raise FallbackError('scrubbed_log_not_utf8') from None
    endpoint = issue_url + '/comments'
    existing = {c.get('body') for c in comments(endpoint, token, call)}
    posted = 0
    for body in bodies(context, log):
        if body in existing:
            continue
        try:
            result = call(endpoint, token, {'body': body})
            if not isinstance(result, dict) or result.get('body') != body or not result.get('id'):
                raise FallbackError('comment_post_unconfirmed')
        except FallbackError:
            # A timed-out POST may already exist. Read back, never blindly replay.
            if body not in {c.get('body') for c in comments(endpoint, token, call)}:
                raise FallbackError('comment_delivery_unconfirmed_no_replay') from None
        existing.add(body)
        posted += 1
        pause(1)  # Respect GitHub's secondary content-creation rate limit.
    return posted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('resolve', 'post', 'cleanup'))
    args = parser.parse_args()
    env = os.environ
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', env['LOG_LANE']):
        raise SystemExit('Invalid fallback lane.')
    context_path = Path(env['RUNNER_TEMP']) / ('ci-log-fallback-' + env['LOG_LANE'] + '.json')
    try:
        if args.phase == 'cleanup':
            context_path.unlink(missing_ok=True)
        elif args.phase == 'resolve':
            context = resolve(env)
            with context_path.open('w', encoding='utf-8') as stream:
                os.chmod(context_path, 0o600)
                json.dump(context, stream)
            with open(env['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
                stream.write('repository_name=' + context['repository'].split('/')[1] + '\n')
        else:
            context = json.loads(context_path.read_text())
            count = post(context, Path(env['LOG_PATH']), env['GH_TOKEN'])
            print(f'Private owning-issue log fallback complete; new parts={count}. Original verdict unchanged.')
    except (FallbackError, OSError, KeyError, ValueError) as error:
        # Never print API bodies, private log text, environment or credentials.
        message = str(error) if isinstance(error, FallbackError) else type(error).__name__
        raise SystemExit('Private issue log fallback failed: ' + message) from None
    finally:
        if args.phase == 'post':
            context_path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
