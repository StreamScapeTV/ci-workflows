-- Issue #52: authoritative transactional command RPC and bounded reads.
-- This migration is pre-deployment source. It is safe to revise while PR #57 is draft.

begin;

create function agent_api.command(p_request jsonb)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, agent_private, extensions
set row_security = off
as $$
declare
  v_version integer;
  v_request_id text;
  v_request_hash text;
  v_existing_hash text;
  v_action text;
  v_project text;
  v_repository text;
  v_session_name text;
  v_project_id uuid;
  v_current_base text;
  v_profile text;
  v_slot smallint;
  v_agent_id text;
  v_session_id uuid;
  v_status text;
  v_issue_number bigint;
  v_branch text;
  v_branch_nonce text;
  v_pr_number bigint;
  v_binding_pr bigint;
  v_base_sha text;
  v_expected_base_sha text;
  v_head_sha text;
  v_existing_head_sha text;
  v_merge_sha text;
  v_task text;
  v_reason text;
  v_summary text;
  v_claims jsonb;
  v_claim jsonb;
  v_collision jsonb;
  v_response jsonb;
  v_released_count integer := 0;
  v_claim_count integer := 0;
  v_release_all boolean := false;
  v_allowed text[];
begin
  perform agent_private.assert_object_keys(
    p_request,
    array[
      'contract_version', 'request_id', 'action', 'project', 'repository',
      'session_name', 'agent_id', 'task', 'issue_number', 'branch', 'branch_nonce',
      'pr_number', 'base_sha', 'expected_base_sha', 'head_sha', 'merge_sha',
      'reason', 'summary', 'claims', 'all'
    ]
  );

  if pg_catalog.jsonb_typeof(p_request -> 'contract_version') <> 'number'
     or (p_request ->> 'contract_version') !~ '^[0-9]+$' then
    perform agent_private.fail('invalid_contract_version');
  end if;
  v_version := (p_request ->> 'contract_version')::integer;
  if v_version <> 1 then
    perform agent_private.fail('unsupported_contract_version');
  end if;

  v_request_id := agent_private.require_text(p_request, 'request_id', 8, 128);
  if v_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$' then
    perform agent_private.fail('invalid_request_id');
  end if;

  v_action := agent_private.require_text(p_request, 'action', 1, 32);
  if v_action not in ('resume', 'start', 'claim', 'release', 'reconcile_base', 'block', 'review', 'done', 'cancel') then
    perform agent_private.fail('unsupported_action', v_action);
  end if;

  v_project := agent_private.require_text(p_request, 'project', 1, 63);
  v_repository := agent_private.require_text(p_request, 'repository', 3, 200);
  v_session_name := agent_private.require_text(p_request, 'session_name', 7, 16);
  select profile_key, slot into v_profile, v_slot
  from agent_private.parse_session_name(v_session_name);

  v_request_hash := pg_catalog.encode(extensions.digest(p_request::text, 'sha256'), 'hex');
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('agent-state-request:' || v_request_id, 0)
  );

  select request_hash into v_existing_hash
  from agent_private.requests
  where request_id = v_request_id;

  if found then
    if v_existing_hash <> v_request_hash then
      perform agent_private.fail('request_id_reuse_conflict');
    end if;
    select response into v_response
    from agent_private.command_receipts
    where request_id = v_request_id;
    if v_response is null then
      perform agent_private.fail('request_incomplete');
    end if;
    return v_response;
  end if;

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('agent-state-project:' || v_project, 0)
  );

  select id, current_base_sha into v_project_id, v_current_base
  from agent_private.projects
  where project_key = v_project
    and repository_full_name = v_repository
    and enabled
  for update;

  if v_project_id is null then
    perform agent_private.fail('project_mismatch');
  end if;

  perform 1
  from agent_private.project_slots
  where project_id = v_project_id
    and profile_key = v_profile
    and slot = v_slot
    and enabled
  for update;

  if not found then
    perform agent_private.fail('slot_not_allowed');
  end if;

  if v_action = 'resume' then
    v_allowed := array['contract_version', 'request_id', 'action', 'project', 'repository', 'session_name'];
  elsif v_action = 'start' then
    v_allowed := array[
      'contract_version', 'request_id', 'action', 'project', 'repository', 'session_name',
      'agent_id', 'task', 'issue_number', 'branch', 'branch_nonce', 'pr_number',
      'base_sha', 'head_sha', 'claims'
    ];
  elsif v_action = 'claim' then
    v_allowed := array[
      'contract_version', 'request_id', 'action', 'project', 'repository', 'session_name',
      'agent_id', 'issue_number', 'branch', 'pr_number', 'head_sha', 'claims'
    ];
  elsif v_action = 'release' then
    v_allowed := array[
      'contract_version', 'request_id', 'action', 'project', 'repository', 'session_name',
      'agent_id', 'issue_number', 'branch', 'claims', 'all'
    ];
  elsif v_action = 'reconcile_base' then
    v_allowed := array[
      'contract_version', 'request_id', 'action', 'project', 'repository', 'session_name',
      'agent_id', 'issue_number', 'branch', 'expected_base_sha', 'base_sha'
    ];
  elsif v_action = 'block' then
    v_allowed := array[
      'contract_version', 'request_id', 'action', 'project', 'repository', 'session_name',
      'agent_id', 'issue_number', 'branch', 'reason'
    ];
  elsif v_action = 'review' then
    v_allowed := array[
      'contract_version', 'request_id', 'action', 'project', 'repository', 'session_name',
      'agent_id', 'issue_number', 'branch', 'pr_number', 'head_sha', 'summary'
    ];
  elsif v_action = 'done' then
    v_allowed := array[
      'contract_version', 'request_id', 'action', 'project', 'repository', 'session_name',
      'agent_id', 'issue_number', 'branch', 'pr_number', 'head_sha', 'merge_sha', 'summary'
    ];
  else
    v_allowed := array[
      'contract_version', 'request_id', 'action', 'project', 'repository', 'session_name',
      'agent_id', 'issue_number', 'branch', 'summary'
    ];
  end if;
  perform agent_private.assert_object_keys(p_request, v_allowed);

  if v_action = 'resume' then
    insert into agent_private.requests(
      request_id, request_hash, action, project_id, session_name
    ) values (
      v_request_id, v_request_hash, v_action, v_project_id, v_session_name
    );

    select id into v_session_id
    from agent_private.work_sessions
    where project_id = v_project_id
      and session_name = v_session_name
      and status in ('active', 'blocked', 'review')
    order by updated_at desc
    limit 1
    for update;

    if v_session_id is null then
      return agent_private.complete_request(
        v_request_id,
        v_project_id,
        null,
        v_action,
        'no_active_work',
        pg_catalog.jsonb_build_object(
          'accepted', true,
          'decision', 'no-active-work',
          'instruction', 'start a bounded issue'
        )
      );
    end if;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'resume',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'resume',
        'instruction', 'continue the active work',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  end if;

  v_agent_id := agent_private.optional_text(p_request, 'agent_id', 128);

  if v_action = 'start' then
    if v_agent_id is null then
      v_agent_id := agent_private.generate_agent_id(v_profile, v_slot);
    else
      perform agent_private.assert_agent_id(v_agent_id, v_profile, v_slot);
    end if;

    v_task := agent_private.assert_public_text(
      agent_private.require_text(p_request, 'task', 1, 2000),
      'task'
    );
    v_issue_number := agent_private.require_positive_bigint(p_request, 'issue_number');
    v_branch := agent_private.require_text(p_request, 'branch', 12, 255);
    v_branch_nonce := agent_private.assert_branch(
      v_branch,
      v_issue_number,
      agent_private.optional_text(p_request, 'branch_nonce', 4)
    );
    v_pr_number := agent_private.optional_positive_bigint(p_request, 'pr_number');
    v_base_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'base_sha', 40, 40),
      'base_sha'
    );
    v_head_sha := agent_private.assert_sha(
      agent_private.optional_text(p_request, 'head_sha', 40),
      'head_sha',
      true
    );

    if v_pr_number is null and v_head_sha is not null then
      perform agent_private.fail('head_requires_pr');
    end if;

    if v_current_base is null then
      update agent_private.projects
      set current_base_sha = v_base_sha,
          updated_at = pg_catalog.clock_timestamp()
      where id = v_project_id;
    elsif v_current_base <> v_base_sha then
      perform agent_private.fail('stale_base_assertion');
    end if;

    if exists (
      select 1
      from agent_private.work_sessions
      where project_id = v_project_id
        and profile_key = v_profile
        and slot = v_slot
        and status in ('active', 'blocked', 'review')
    ) then
      perform agent_private.fail('active_session_exists');
    end if;

    v_claims := agent_private.normalize_claims(
      coalesce(p_request -> 'claims', '[]'::jsonb),
      false
    );

    for v_claim in select value from pg_catalog.jsonb_array_elements(v_claims)
    loop
      v_collision := agent_private.find_claim_collision(
        v_project_id,
        null,
        v_claim ->> 'kind',
        v_claim ->> 'mode',
        v_claim ->> 'value'
      );
      if v_collision is not null then
        insert into agent_private.requests(
          request_id, request_hash, action, project_id, session_name, agent_id
        ) values (
          v_request_id, v_request_hash, v_action, v_project_id, v_session_name, v_agent_id
        );
        return agent_private.complete_request(
          v_request_id,
          v_project_id,
          null,
          v_action,
          'claim_conflict',
          pg_catalog.jsonb_build_object(
            'accepted', false,
            'decision', 'claim-conflict',
            'instruction', 'choose a non-overlapping claim',
            'collision', v_collision
          )
        );
      end if;
    end loop;

    insert into agent_private.requests(
      request_id, request_hash, action, project_id, session_name, agent_id
    ) values (
      v_request_id, v_request_hash, v_action, v_project_id, v_session_name, v_agent_id
    );

    insert into agent_private.work_sessions(
      project_id, profile_key, slot, session_name, agent_id, task, status
    ) values (
      v_project_id, v_profile, v_slot, v_session_name, v_agent_id, v_task, 'active'
    ) returning id into v_session_id;

    insert into agent_private.work_bindings(
      session_id, project_id, issue_number, branch, branch_nonce, pr_number
    ) values (
      v_session_id, v_project_id, v_issue_number, v_branch, v_branch_nonce, v_pr_number
    );

    insert into agent_private.work_evidence(
      session_id, project_id, base_sha, head_sha
    ) values (
      v_session_id, v_project_id, v_base_sha, v_head_sha
    );

    insert into agent_private.claims(
      project_id, session_id, kind, mode, value, created_request_id
    )
    select
      v_project_id,
      v_session_id,
      claim ->> 'kind',
      claim ->> 'mode',
      claim ->> 'value',
      v_request_id
    from pg_catalog.jsonb_array_elements(v_claims) claim;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'started',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'started',
        'instruction', 'continue the bounded issue',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  end if;

  if v_agent_id is null then
    perform agent_private.fail('missing_agent_id');
  end if;

  v_issue_number := agent_private.require_positive_bigint(p_request, 'issue_number');
  v_branch := agent_private.require_text(p_request, 'branch', 12, 255);
  v_session_id := agent_private.lock_session(
    v_project_id,
    v_session_name,
    v_agent_id,
    v_issue_number,
    v_branch
  );

  select s.status, b.pr_number, e.head_sha
  into v_status, v_binding_pr, v_existing_head_sha
  from agent_private.work_sessions s
  join agent_private.work_bindings b on b.session_id = s.id
  join agent_private.work_evidence e on e.session_id = s.id
  where s.id = v_session_id;

  if v_action in ('claim', 'done') then
    v_pr_number := agent_private.optional_positive_bigint(p_request, 'pr_number');
    v_head_sha := agent_private.assert_sha(
      agent_private.optional_text(p_request, 'head_sha', 40),
      'head_sha',
      true
    );
    if v_pr_number is not null and v_binding_pr is distinct from v_pr_number then
      perform agent_private.fail('stale_pr_assertion');
    end if;
    if v_head_sha is not null and v_existing_head_sha is distinct from v_head_sha then
      perform agent_private.fail('stale_head_assertion');
    end if;
  end if;

  insert into agent_private.requests(
    request_id, request_hash, action, project_id, session_name, agent_id
  ) values (
    v_request_id, v_request_hash, v_action, v_project_id, v_session_name, v_agent_id
  );

  if v_action = 'claim' then
    v_claims := agent_private.normalize_claims(p_request -> 'claims', true);

    for v_claim in select value from pg_catalog.jsonb_array_elements(v_claims)
    loop
      v_collision := agent_private.find_claim_collision(
        v_project_id,
        v_session_id,
        v_claim ->> 'kind',
        v_claim ->> 'mode',
        v_claim ->> 'value'
      );
      if v_collision is not null then
        return agent_private.complete_request(
          v_request_id,
          v_project_id,
          v_session_id,
          v_action,
          'claim_conflict',
          pg_catalog.jsonb_build_object(
            'accepted', false,
            'decision', 'claim-conflict',
            'instruction', 'choose a non-overlapping claim',
            'collision', v_collision
          )
        );
      end if;
    end loop;

    insert into agent_private.claims(
      project_id, session_id, kind, mode, value, created_request_id
    )
    select
      v_project_id,
      v_session_id,
      claim ->> 'kind',
      claim ->> 'mode',
      claim ->> 'value',
      v_request_id
    from pg_catalog.jsonb_array_elements(v_claims) claim
    on conflict do nothing;

    get diagnostics v_claim_count = row_count;
    update agent_private.work_sessions
    set updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'claims_acquired',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'claimed',
        'claim_count', v_claim_count,
        'session', agent_private.session_snapshot(v_session_id)
      )
    );

  elsif v_action = 'release' then
    if p_request ? 'all' and pg_catalog.jsonb_typeof(p_request -> 'all') <> 'boolean' then
      perform agent_private.fail('invalid_field_type', 'all');
    end if;
    v_release_all := coalesce((p_request ->> 'all')::boolean, false);

    if v_release_all then
      if p_request ? 'claims'
         and pg_catalog.jsonb_typeof(p_request -> 'claims') = 'array'
         and pg_catalog.jsonb_array_length(p_request -> 'claims') > 0 then
        perform agent_private.fail('ambiguous_release_request');
      end if;
      update agent_private.claims
      set active = false,
          released_request_id = v_request_id,
          released_at = pg_catalog.clock_timestamp()
      where session_id = v_session_id and active;
    else
      v_claims := agent_private.normalize_claims(p_request -> 'claims', true);
      update agent_private.claims c
      set active = false,
          released_request_id = v_request_id,
          released_at = pg_catalog.clock_timestamp()
      where c.session_id = v_session_id
        and c.active
        and exists (
          select 1
          from pg_catalog.jsonb_array_elements(v_claims) claim
          where c.kind = claim ->> 'kind'
            and c.mode = claim ->> 'mode'
            and c.value = claim ->> 'value'
        );
    end if;

    get diagnostics v_released_count = row_count;
    update agent_private.work_sessions
    set updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'claims_released',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'released',
        'released_count', v_released_count,
        'session', agent_private.session_snapshot(v_session_id)
      )
    );

  elsif v_action = 'reconcile_base' then
    v_expected_base_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'expected_base_sha', 40, 40),
      'expected_base_sha'
    );
    v_base_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'base_sha', 40, 40),
      'base_sha'
    );

    if v_current_base is distinct from v_expected_base_sha then
      perform agent_private.fail('stale_base_assertion');
    end if;

    update agent_private.projects
    set current_base_sha = v_base_sha,
        updated_at = pg_catalog.clock_timestamp()
    where id = v_project_id;

    update agent_private.work_evidence
    set base_sha = v_base_sha,
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;

    update agent_private.work_sessions
    set updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'base_reconciled',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'base-reconciled',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );

  elsif v_action = 'block' then
    v_reason := agent_private.assert_public_text(
      agent_private.require_text(p_request, 'reason', 1, 2000),
      'reason'
    );

    update agent_private.work_sessions
    set status = 'blocked',
        blocked_reason = v_reason,
        updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'blocked',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'blocked',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );

  elsif v_action = 'review' then
    v_pr_number := agent_private.require_positive_bigint(p_request, 'pr_number');
    v_head_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'head_sha', 40, 40),
      'head_sha'
    );
    v_summary := agent_private.assert_public_text(
      agent_private.optional_text(p_request, 'summary', 2000),
      'summary'
    );

    if v_binding_pr is not null and v_binding_pr <> v_pr_number then
      perform agent_private.fail('stale_pr_assertion');
    end if;
    if v_existing_head_sha is not null and v_existing_head_sha <> v_head_sha then
      perform agent_private.fail('stale_head_assertion');
    end if;

    update agent_private.work_bindings
    set pr_number = v_pr_number,
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;

    update agent_private.work_evidence
    set head_sha = v_head_sha,
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;

    update agent_private.work_sessions
    set status = 'review',
        blocked_reason = null,
        updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'review',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'review',
        'summary', v_summary,
        'session', agent_private.session_snapshot(v_session_id)
      )
    );

  elsif v_action = 'done' then
    if v_status <> 'review' then
      perform agent_private.fail('review_status_required');
    end if;
    if v_binding_pr is null or v_existing_head_sha is null then
      perform agent_private.fail('review_evidence_required');
    end if;

    v_summary := agent_private.assert_public_text(
      agent_private.require_text(p_request, 'summary', 1, 2000),
      'summary'
    );
    v_merge_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'merge_sha', 40, 40),
      'merge_sha'
    );

    update agent_private.claims
    set active = false,
        released_request_id = v_request_id,
        released_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id and active;
    get diagnostics v_released_count = row_count;

    update agent_private.work_evidence
    set merge_sha = v_merge_sha,
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;

    update agent_private.work_bindings
    set active = false,
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;

    update agent_private.work_sessions
    set status = 'done',
        terminal_summary = v_summary,
        terminal_at = pg_catalog.clock_timestamp(),
        updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'done',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'done',
        'released_count', v_released_count,
        'session', agent_private.session_snapshot(v_session_id)
      )
    );

  elsif v_action = 'cancel' then
    v_summary := agent_private.assert_public_text(
      agent_private.require_text(p_request, 'summary', 1, 2000),
      'summary'
    );

    update agent_private.claims
    set active = false,
        released_request_id = v_request_id,
        released_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id and active;
    get diagnostics v_released_count = row_count;

    update agent_private.work_bindings
    set active = false,
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;

    update agent_private.work_sessions
    set status = 'cancelled',
        terminal_summary = v_summary,
        terminal_at = pg_catalog.clock_timestamp(),
        updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;

    return agent_private.complete_request(
      v_request_id,
      v_project_id,
      v_session_id,
      v_action,
      'cancelled',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'cancelled',
        'released_count', v_released_count,
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  end if;

  perform agent_private.fail('unreachable');
  return null;
end;
$$;

create function agent_api.resume(
  p_project text,
  p_repository text,
  p_session_name text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, agent_private
set row_security = off
as $$
declare
  v_project_id uuid;
  v_session_id uuid;
  v_profile text;
  v_slot smallint;
begin
  select id into v_project_id
  from agent_private.projects
  where project_key = p_project
    and repository_full_name = p_repository
    and enabled;
  if v_project_id is null then
    perform agent_private.fail('project_mismatch');
  end if;

  select profile_key, slot into v_profile, v_slot
  from agent_private.parse_session_name(p_session_name);
  perform 1 from agent_private.project_slots
  where project_id = v_project_id
    and profile_key = v_profile
    and slot = v_slot
    and enabled;
  if not found then
    perform agent_private.fail('slot_not_allowed');
  end if;

  select id into v_session_id
  from agent_private.work_sessions
  where project_id = v_project_id
    and session_name = p_session_name
    and status in ('active', 'blocked', 'review')
  order by updated_at desc
  limit 1;

  if v_session_id is null then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1,
      'project', p_project,
      'repository', p_repository,
      'decision', 'no-active-work'
    );
  end if;

  return pg_catalog.jsonb_build_object(
    'contract_version', 1,
    'project', p_project,
    'repository', p_repository,
    'decision', 'resume',
    'session', agent_private.session_snapshot(v_session_id)
  );
end;
$$;

create function agent_api.context(
  p_project text,
  p_repository text,
  p_agent_id text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, agent_private
set row_security = off
as $$
declare
  v_project_id uuid;
  v_session_id uuid;
begin
  select id into v_project_id
  from agent_private.projects
  where project_key = p_project
    and repository_full_name = p_repository
    and enabled;
  if v_project_id is null then
    perform agent_private.fail('project_mismatch');
  end if;

  select id into v_session_id
  from agent_private.work_sessions
  where project_id = v_project_id
    and agent_id = p_agent_id
  order by updated_at desc
  limit 1;

  if v_session_id is null then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1,
      'project', p_project,
      'repository', p_repository,
      'session', null,
      'events', '[]'::jsonb
    );
  end if;

  return pg_catalog.jsonb_build_object(
    'contract_version', 1,
    'project', p_project,
    'repository', p_repository,
    'session', agent_private.session_snapshot(v_session_id),
    'events', (
      select coalesce(
        pg_catalog.jsonb_agg(
          pg_catalog.jsonb_build_object(
            'event_id', event_id,
            'event_type', event_type,
            'request_id', request_id,
            'created_at', created_at
          ) order by event_id desc
        ),
        '[]'::jsonb
      )
      from (
        select event_id, event_type, request_id, created_at
        from agent_private.events
        where session_id = v_session_id
        order by event_id desc
        limit 50
      ) bounded_events
    )
  );
end;
$$;

create function agent_api.ownership_check(
  p_project text,
  p_repository text,
  p_issue_number bigint,
  p_branch text,
  p_pr_number bigint default null,
  p_head_sha text default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, agent_private
set row_security = off
as $$
declare
  v_project_id uuid;
  v_result jsonb;
begin
  if p_issue_number is null or p_issue_number < 1 then
    perform agent_private.fail('invalid_positive_integer', 'issue_number');
  end if;
  perform agent_private.assert_branch(p_branch, p_issue_number, null);
  if p_pr_number is not null and p_pr_number < 1 then
    perform agent_private.fail('invalid_positive_integer', 'pr_number');
  end if;
  perform agent_private.assert_sha(p_head_sha, 'head_sha', true);

  select id into v_project_id
  from agent_private.projects
  where project_key = p_project
    and repository_full_name = p_repository
    and enabled;
  if v_project_id is null then
    perform agent_private.fail('project_mismatch');
  end if;

  select pg_catalog.jsonb_build_object(
    'contract_version', 1,
    'owned', true,
    'project', p_project,
    'repository', p_repository,
    'agent_id', s.agent_id,
    'session_name', s.session_name,
    'status', s.status,
    'issue_number', b.issue_number,
    'branch', b.branch,
    'pr_number', b.pr_number,
    'head_sha', e.head_sha
  ) into v_result
  from agent_private.work_sessions s
  join agent_private.work_bindings b on b.session_id = s.id
  join agent_private.work_evidence e on e.session_id = s.id
  where s.project_id = v_project_id
    and b.active
    and b.issue_number = p_issue_number
    and b.branch = p_branch
    and (p_pr_number is null or b.pr_number = p_pr_number)
    and (p_head_sha is null or e.head_sha = p_head_sha)
  limit 1;

  return coalesce(
    v_result,
    pg_catalog.jsonb_build_object(
      'contract_version', 1,
      'owned', false,
      'project', p_project,
      'repository', p_repository
    )
  );
end;
$$;

revoke all on function agent_api.command(jsonb) from public, anon, authenticated, service_role;
revoke all on function agent_api.resume(text, text, text) from public, anon, authenticated, service_role;
revoke all on function agent_api.context(text, text, text) from public, anon, authenticated, service_role;
revoke all on function agent_api.ownership_check(text, text, bigint, text, bigint, text)
  from public, anon, authenticated, service_role;

grant execute on function agent_api.command(jsonb) to service_role;
grant execute on function agent_api.resume(text, text, text) to service_role;
grant execute on function agent_api.context(text, text, text) to service_role;
grant execute on function agent_api.ownership_check(text, text, bigint, text, bigint, text)
  to service_role;

comment on function agent_api.command(jsonb) is
  'Issue #52 authoritative transactional command RPC. Ordinary agents call this function only.';
comment on function agent_api.resume(text, text, text) is
  'Issue #52 bounded current-assignment read.';
comment on function agent_api.context(text, text, text) is
  'Issue #52 bounded session and append-only event summary.';
comment on function agent_api.ownership_check(text, text, bigint, text, bigint, text) is
  'Issue #52 exact issue, branch, PR, and head ownership assertion.';

commit;
