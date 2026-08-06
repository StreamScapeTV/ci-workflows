-- Issue #52: reviewed transactional command dispatcher and bounded reads.

begin;

create or replace function agent_api.command(p_request jsonb)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, agent_private, extensions
set row_security = off
as $$
declare
  v_contract_version integer;
  v_request_id text;
  v_request_hash text;
  v_action text;
  v_repository text;
  v_project_key text;
  v_session_name text;
  v_profile_key text;
  v_slot smallint;
  v_project_id uuid;
  v_project_base text;
  v_agent_id text;
  v_issue_number bigint;
  v_branch text;
  v_branch_nonce text;
  v_task text;
  v_base_sha text;
  v_new_base_sha text;
  v_head_sha text;
  v_merge_sha text;
  v_pr_number bigint;
  v_summary text;
  v_reason text;
  v_claims jsonb;
  v_claim jsonb;
  v_claim_kind text;
  v_claim_mode text;
  v_claim_value text;
  v_claim_identity text;
  v_seen_claims text[] := array[]::text[];
  v_collision jsonb;
  v_session_id uuid;
  v_status text;
  v_existing_hash text;
  v_existing_response jsonb;
  v_existing_pr bigint;
  v_existing_base text;
  v_existing_head text;
begin
  perform agent_private.assert_object_keys(
    p_request,
    array[
      'contract_version','request_id','action','repository','project','session_name',
      'agent_id','issue_number','branch','branch_nonce','task','base_sha','new_base_sha',
      'head_sha','merge_sha','pr_number','claims','summary','reason'
    ]
  );

  if not (p_request ? 'contract_version')
     or pg_catalog.jsonb_typeof(p_request -> 'contract_version') <> 'number'
     or (p_request ->> 'contract_version') !~ '^[0-9]+$' then
    perform agent_private.fail('invalid_contract_version');
  end if;
  v_contract_version := (p_request ->> 'contract_version')::integer;
  if v_contract_version <> 1 then
    perform agent_private.fail('unsupported_contract_version');
  end if;

  v_request_id := agent_private.require_text(p_request, 'request_id', 8, 128);
  if v_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$' then
    perform agent_private.fail('invalid_request_id');
  end if;
  v_action := agent_private.require_text(p_request, 'action', 3, 32);
  if v_action not in ('resume','start','claim','release','reconcile_base','block','review','done','cancel') then
    perform agent_private.fail('unsupported_action');
  end if;
  v_repository := agent_private.require_text(p_request, 'repository', 3, 200);
  v_project_key := agent_private.require_text(p_request, 'project', 1, 63);
  v_session_name := agent_private.require_text(p_request, 'session_name', 7, 32);

  select profile_key, slot into v_profile_key, v_slot
  from agent_private.parse_session_name(v_session_name);

  v_request_hash := pg_catalog.encode(extensions.digest(p_request::text, 'sha256'), 'hex');

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_request_id, 7612952)
  );

  select r.request_hash, c.response
    into v_existing_hash, v_existing_response
  from agent_private.requests r
  join agent_private.command_receipts c on c.request_id = r.request_id
  where r.request_id = v_request_id;

  if found then
    if v_existing_hash <> v_request_hash then
      perform agent_private.fail('request_id_conflict');
    end if;
    return v_existing_response;
  end if;

  select p.id, p.current_base_sha
    into v_project_id, v_project_base
  from agent_private.projects p
  where p.project_key = v_project_key
    and p.repository_full_name = v_repository
    and p.enabled
  for update;

  if v_project_id is null then
    perform agent_private.fail('project_repository_mismatch');
  end if;

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_project_id::text, 7612953)
  );

  if not exists (
    select 1 from agent_private.project_slots ps
    where ps.project_id = v_project_id
      and ps.profile_key = v_profile_key
      and ps.slot = v_slot
      and ps.enabled
  ) then
    perform agent_private.fail('slot_not_allowed');
  end if;

  if v_action = 'resume' then
    perform agent_private.assert_object_keys(
      p_request,
      array['contract_version','request_id','action','repository','project','session_name']
    );
  elsif v_action = 'start' then
    perform agent_private.assert_object_keys(
      p_request,
      array[
        'contract_version','request_id','action','repository','project','session_name',
        'agent_id','issue_number','branch','branch_nonce','task','base_sha',
        'pr_number','head_sha','claims'
      ]
    );
  elsif v_action in ('claim','release') then
    perform agent_private.assert_object_keys(
      p_request,
      array[
        'contract_version','request_id','action','repository','project','session_name',
        'agent_id','issue_number','branch','claims'
      ]
    );
  elsif v_action = 'reconcile_base' then
    perform agent_private.assert_object_keys(
      p_request,
      array[
        'contract_version','request_id','action','repository','project','session_name',
        'agent_id','issue_number','branch','base_sha','new_base_sha','head_sha'
      ]
    );
  elsif v_action = 'block' then
    perform agent_private.assert_object_keys(
      p_request,
      array[
        'contract_version','request_id','action','repository','project','session_name',
        'agent_id','issue_number','branch','reason'
      ]
    );
  elsif v_action = 'review' then
    perform agent_private.assert_object_keys(
      p_request,
      array[
        'contract_version','request_id','action','repository','project','session_name',
        'agent_id','issue_number','branch','pr_number','base_sha','head_sha','summary'
      ]
    );
  elsif v_action = 'done' then
    perform agent_private.assert_object_keys(
      p_request,
      array[
        'contract_version','request_id','action','repository','project','session_name',
        'agent_id','issue_number','branch','pr_number','base_sha','head_sha','merge_sha','summary'
      ]
    );
  elsif v_action = 'cancel' then
    perform agent_private.assert_object_keys(
      p_request,
      array[
        'contract_version','request_id','action','repository','project','session_name',
        'agent_id','issue_number','branch','reason'
      ]
    );
  end if;

  if v_action = 'start' then
    v_agent_id := agent_private.optional_text(p_request, 'agent_id', 128);
    if v_agent_id is null then
      v_agent_id := agent_private.generate_agent_id(v_profile_key, v_slot);
    else
      perform agent_private.assert_agent_id(v_agent_id, v_profile_key, v_slot);
    end if;
  elsif v_action <> 'resume' then
    v_agent_id := agent_private.require_text(p_request, 'agent_id', 16, 128);
    perform agent_private.assert_agent_id(v_agent_id, v_profile_key, v_slot);
  end if;

  insert into agent_private.requests(
    request_id, request_hash, action, project_id, session_name, agent_id
  ) values (
    v_request_id, v_request_hash, v_action, v_project_id, v_session_name, v_agent_id
  );

  if v_action = 'resume' then
    select s.id into v_session_id
    from agent_private.work_sessions s
    where s.project_id = v_project_id
      and s.profile_key = v_profile_key
      and s.slot = v_slot
      and s.status in ('active','blocked','review')
    order by s.updated_at desc, s.created_at desc
    limit 1;

    if v_session_id is null then
      return agent_private.complete_request(
        v_request_id, v_project_id, null, v_action, 'no_active_work',
        pg_catalog.jsonb_build_object(
          'accepted', false,
          'decision', 'no_active_work',
          'instruction', 'No unfinished work is assigned to this project/profile/slot.'
        )
      );
    end if;

    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'resume',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'resume',
        'instruction', 'Resume the returned unfinished work before starting unrelated work.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  end if;

  if v_action = 'start' then
    v_issue_number := agent_private.require_positive_bigint(p_request, 'issue_number');
    v_branch := agent_private.require_text(p_request, 'branch', 10, 255);
    v_branch_nonce := agent_private.optional_text(p_request, 'branch_nonce', 4);
    v_branch_nonce := agent_private.assert_branch(v_branch, v_issue_number, v_branch_nonce);
    v_task := agent_private.require_text(p_request, 'task', 1, 2000);
    v_base_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'base_sha', 40, 40), 'base_sha'
    );
    v_pr_number := agent_private.optional_positive_bigint(p_request, 'pr_number');
    v_head_sha := agent_private.assert_sha(
      agent_private.optional_text(p_request, 'head_sha', 40), 'head_sha', true
    );
    if (v_pr_number is null) <> (v_head_sha is null) then
      perform agent_private.fail('pr_head_pair_required');
    end if;

    if v_project_base is not null and v_project_base <> v_base_sha then
      perform agent_private.fail('stale_base');
    end if;

    if exists (
      select 1 from agent_private.work_sessions
      where project_id = v_project_id
        and profile_key = v_profile_key
        and slot = v_slot
        and status in ('active','blocked','review')
    ) then
      perform agent_private.fail('slot_has_unfinished_work');
    end if;

    if not (p_request ? 'claims') then
      v_claims := '[]'::jsonb;
    elsif pg_catalog.jsonb_typeof(p_request -> 'claims') <> 'array' then
      perform agent_private.fail('claims_must_be_array');
    else
      v_claims := p_request -> 'claims';
    end if;
    if pg_catalog.jsonb_array_length(v_claims) > 256 then
      perform agent_private.fail('too_many_claims');
    end if;

    for v_claim in select value from pg_catalog.jsonb_array_elements(v_claims)
    loop
      perform agent_private.assert_object_keys(v_claim, array['kind','mode','value']);
      v_claim_kind := agent_private.require_text(v_claim, 'kind', 4, 16);
      v_claim_mode := agent_private.require_text(v_claim, 'mode', 5, 6);
      v_claim_value := agent_private.normalize_claim_value(
        v_claim_kind,
        v_claim_mode,
        agent_private.require_text(v_claim, 'value', 1, 512)
      );
      v_claim_identity := v_claim_kind || chr(31) || v_claim_mode || chr(31) || v_claim_value;
      if v_claim_identity = any(v_seen_claims) then
        perform agent_private.fail('duplicate_claim', v_claim_value);
      end if;
      v_seen_claims := pg_catalog.array_append(v_seen_claims, v_claim_identity);
      v_collision := agent_private.find_claim_collision(
        v_project_id, null, v_claim_kind, v_claim_mode, v_claim_value
      );
      if v_collision is not null then
        return agent_private.complete_request(
          v_request_id, v_project_id, null, v_action, 'claim_conflict',
          pg_catalog.jsonb_build_object(
            'accepted', false,
            'decision', 'claim_conflict',
            'instruction', 'Narrow the claim or coordinate with the recorded owner.',
            'collision', v_collision
          )
        );
      end if;
    end loop;

    if v_project_base is null then
      update agent_private.projects
      set current_base_sha = v_base_sha,
          updated_at = pg_catalog.clock_timestamp()
      where id = v_project_id;
    end if;

    insert into agent_private.work_sessions(
      project_id, profile_key, slot, session_name, agent_id, task, status
    ) values (
      v_project_id, v_profile_key, v_slot, v_session_name, v_agent_id, v_task, 'active'
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

    for v_claim in select value from pg_catalog.jsonb_array_elements(v_claims)
    loop
      insert into agent_private.claims(
        project_id, session_id, kind, mode, value, created_request_id
      ) values (
        v_project_id,
        v_session_id,
        v_claim ->> 'kind',
        v_claim ->> 'mode',
        v_claim ->> 'value',
        v_request_id
      );
    end loop;

    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'started',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'started',
        'instruction', 'Work and claims are active.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  end if;

  v_issue_number := agent_private.require_positive_bigint(p_request, 'issue_number');
  v_branch := agent_private.require_text(p_request, 'branch', 10, 255);
  v_session_id := agent_private.lock_session(
    v_project_id, v_session_name, v_agent_id, v_issue_number, v_branch
  );

  select status into strict v_status
  from agent_private.work_sessions where id = v_session_id;

  if v_action = 'claim' then
    if v_status <> 'active' then
      perform agent_private.fail('invalid_transition', v_status || '->claim');
    end if;
    if not (p_request ? 'claims')
       or pg_catalog.jsonb_typeof(p_request -> 'claims') <> 'array'
       or pg_catalog.jsonb_array_length(p_request -> 'claims') < 1 then
      perform agent_private.fail('claims_required');
    end if;
    v_claims := p_request -> 'claims';
    if pg_catalog.jsonb_array_length(v_claims) > 256 then
      perform agent_private.fail('too_many_claims');
    end if;
    v_seen_claims := array[]::text[];

    for v_claim in select value from pg_catalog.jsonb_array_elements(v_claims)
    loop
      perform agent_private.assert_object_keys(v_claim, array['kind','mode','value']);
      v_claim_kind := agent_private.require_text(v_claim, 'kind', 4, 16);
      v_claim_mode := agent_private.require_text(v_claim, 'mode', 5, 6);
      v_claim_value := agent_private.normalize_claim_value(
        v_claim_kind,
        v_claim_mode,
        agent_private.require_text(v_claim, 'value', 1, 512)
      );
      v_claim_identity := v_claim_kind || chr(31) || v_claim_mode || chr(31) || v_claim_value;
      if v_claim_identity = any(v_seen_claims) then
        perform agent_private.fail('duplicate_claim', v_claim_value);
      end if;
      v_seen_claims := pg_catalog.array_append(v_seen_claims, v_claim_identity);
      if exists (
        select 1 from agent_private.claims
        where session_id = v_session_id and active
          and kind = v_claim_kind and mode = v_claim_mode and value = v_claim_value
      ) then
        perform agent_private.fail('claim_already_owned', v_claim_value);
      end if;
      v_collision := agent_private.find_claim_collision(
        v_project_id, v_session_id, v_claim_kind, v_claim_mode, v_claim_value
      );
      if v_collision is not null then
        return agent_private.complete_request(
          v_request_id, v_project_id, v_session_id, v_action, 'claim_conflict',
          pg_catalog.jsonb_build_object(
            'accepted', false,
            'decision', 'claim_conflict',
            'instruction', 'Narrow the claim or coordinate with the recorded owner.',
            'collision', v_collision,
            'session', agent_private.session_snapshot(v_session_id)
          )
        );
      end if;
    end loop;

    for v_claim in select value from pg_catalog.jsonb_array_elements(v_claims)
    loop
      insert into agent_private.claims(
        project_id, session_id, kind, mode, value, created_request_id
      ) values (
        v_project_id,
        v_session_id,
        v_claim ->> 'kind',
        v_claim ->> 'mode',
        v_claim ->> 'value',
        v_request_id
      );
    end loop;
    update agent_private.work_sessions
    set updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;
    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'claims_acquired',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'claims_acquired',
        'instruction', 'Claims are active.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  elsif v_action = 'release' then
    if not (p_request ? 'claims')
       or pg_catalog.jsonb_typeof(p_request -> 'claims') <> 'array'
       or pg_catalog.jsonb_array_length(p_request -> 'claims') < 1 then
      perform agent_private.fail('claims_required');
    end if;
    v_claims := p_request -> 'claims';
    if pg_catalog.jsonb_array_length(v_claims) > 256 then
      perform agent_private.fail('too_many_claims');
    end if;
    v_seen_claims := array[]::text[];

    for v_claim in select value from pg_catalog.jsonb_array_elements(v_claims)
    loop
      perform agent_private.assert_object_keys(v_claim, array['kind','mode','value']);
      v_claim_kind := agent_private.require_text(v_claim, 'kind', 4, 16);
      v_claim_mode := agent_private.require_text(v_claim, 'mode', 5, 6);
      v_claim_value := agent_private.normalize_claim_value(
        v_claim_kind,
        v_claim_mode,
        agent_private.require_text(v_claim, 'value', 1, 512)
      );
      v_claim_identity := v_claim_kind || chr(31) || v_claim_mode || chr(31) || v_claim_value;
      if v_claim_identity = any(v_seen_claims) then
        perform agent_private.fail('duplicate_claim', v_claim_value);
      end if;
      v_seen_claims := pg_catalog.array_append(v_seen_claims, v_claim_identity);
      if not exists (
        select 1 from agent_private.claims
        where session_id = v_session_id and active
          and kind = v_claim_kind and mode = v_claim_mode and value = v_claim_value
      ) then
        perform agent_private.fail('claim_not_owned', v_claim_value);
      end if;
    end loop;

    for v_claim in select value from pg_catalog.jsonb_array_elements(v_claims)
    loop
      update agent_private.claims
      set active = false,
          released_request_id = v_request_id,
          released_at = pg_catalog.clock_timestamp()
      where session_id = v_session_id and active
        and kind = v_claim ->> 'kind'
        and mode = v_claim ->> 'mode'
        and value = v_claim ->> 'value';
    end loop;
    update agent_private.work_sessions
    set updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;
    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'claims_released',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'claims_released',
        'instruction', 'Claims were released.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  elsif v_action = 'reconcile_base' then
    v_base_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'base_sha', 40, 40), 'base_sha'
    );
    v_new_base_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'new_base_sha', 40, 40), 'new_base_sha'
    );
    v_head_sha := agent_private.assert_sha(
      agent_private.optional_text(p_request, 'head_sha', 40), 'head_sha', true
    );
    select base_sha into strict v_existing_base
    from agent_private.work_evidence where session_id = v_session_id for update;
    if v_project_base <> v_base_sha or v_existing_base <> v_base_sha then
      perform agent_private.fail('stale_base');
    end if;
    update agent_private.projects
    set current_base_sha = v_new_base_sha,
        updated_at = pg_catalog.clock_timestamp()
    where id = v_project_id;
    update agent_private.work_evidence
    set base_sha = v_new_base_sha,
        head_sha = coalesce(v_head_sha, head_sha),
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;
    update agent_private.work_sessions
    set updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;
    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'base_reconciled',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'base_reconciled',
        'instruction', 'Base evidence was reconciled.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  elsif v_action = 'block' then
    if v_status <> 'active' then
      perform agent_private.fail('invalid_transition', v_status || '->blocked');
    end if;
    v_reason := agent_private.require_text(p_request, 'reason', 1, 2000);
    update agent_private.work_sessions
    set status = 'blocked',
        blocked_reason = v_reason,
        updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;
    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'blocked',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'blocked',
        'instruction', 'Resolve the recorded blocker before unrelated work.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  elsif v_action = 'review' then
    if v_status not in ('active','blocked') then
      perform agent_private.fail('invalid_transition', v_status || '->review');
    end if;
    v_pr_number := agent_private.require_positive_bigint(p_request, 'pr_number');
    v_base_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'base_sha', 40, 40), 'base_sha'
    );
    v_head_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'head_sha', 40, 40), 'head_sha'
    );
    v_summary := agent_private.require_text(p_request, 'summary', 1, 2000);
    select base_sha into strict v_existing_base
    from agent_private.work_evidence where session_id = v_session_id for update;
    if v_project_base <> v_base_sha or v_existing_base <> v_base_sha then
      perform agent_private.fail('stale_base');
    end if;
    select pr_number into v_existing_pr
    from agent_private.work_bindings where session_id = v_session_id for update;
    if v_existing_pr is not null and v_existing_pr <> v_pr_number then
      perform agent_private.fail('pr_assertion_failed');
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
        terminal_summary = v_summary,
        updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;
    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'review',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'review',
        'instruction', 'The exact PR head is recorded for review.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  elsif v_action = 'done' then
    v_base_sha := agent_private.assert_sha(
      agent_private.require_text(p_request, 'base_sha', 40, 40), 'base_sha'
    );
    v_summary := agent_private.require_text(p_request, 'summary', 1, 2000);
    v_pr_number := agent_private.optional_positive_bigint(p_request, 'pr_number');
    v_head_sha := agent_private.assert_sha(
      agent_private.optional_text(p_request, 'head_sha', 40), 'head_sha', true
    );
    v_merge_sha := agent_private.assert_sha(
      agent_private.optional_text(p_request, 'merge_sha', 40), 'merge_sha', true
    );
    select b.pr_number, e.base_sha, e.head_sha
      into v_existing_pr, v_existing_base, v_existing_head
    from agent_private.work_bindings b
    join agent_private.work_evidence e on e.session_id = b.session_id
    where b.session_id = v_session_id
    for update of b, e;
    if v_project_base <> v_base_sha or v_existing_base <> v_base_sha then
      perform agent_private.fail('stale_base');
    end if;
    if v_existing_pr is not null then
      if v_pr_number is null or v_pr_number <> v_existing_pr
         or v_head_sha is null or v_head_sha <> v_existing_head
         or v_merge_sha is null then
        perform agent_private.fail('merge_evidence_required');
      end if;
    elsif v_pr_number is not null or v_head_sha is not null or v_merge_sha is not null then
      if v_pr_number is null or v_head_sha is null or v_merge_sha is null then
        perform agent_private.fail('pr_head_merge_group_required');
      end if;
      update agent_private.work_bindings
      set pr_number = v_pr_number,
          updated_at = pg_catalog.clock_timestamp()
      where session_id = v_session_id;
    end if;
    if v_head_sha is not null then
      update agent_private.work_evidence
      set head_sha = v_head_sha,
          merge_sha = v_merge_sha,
          updated_at = pg_catalog.clock_timestamp()
      where session_id = v_session_id;
    end if;
    update agent_private.claims
    set active = false,
        released_request_id = v_request_id,
        released_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id and active;
    update agent_private.work_bindings
    set active = false,
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;
    update agent_private.work_sessions
    set status = 'done',
        blocked_reason = null,
        terminal_summary = v_summary,
        terminal_at = pg_catalog.clock_timestamp(),
        updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;
    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'done',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'done',
        'instruction', 'Work is terminal and all claims are released.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  elsif v_action = 'cancel' then
    v_reason := agent_private.require_text(p_request, 'reason', 1, 2000);
    update agent_private.claims
    set active = false,
        released_request_id = v_request_id,
        released_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id and active;
    update agent_private.work_bindings
    set active = false,
        updated_at = pg_catalog.clock_timestamp()
    where session_id = v_session_id;
    update agent_private.work_sessions
    set status = 'cancelled',
        blocked_reason = null,
        terminal_summary = v_reason,
        terminal_at = pg_catalog.clock_timestamp(),
        updated_at = pg_catalog.clock_timestamp()
    where id = v_session_id;
    return agent_private.complete_request(
      v_request_id, v_project_id, v_session_id, v_action, 'cancelled',
      pg_catalog.jsonb_build_object(
        'accepted', true,
        'decision', 'cancelled',
        'instruction', 'Work is terminal and all claims are released.',
        'session', agent_private.session_snapshot(v_session_id)
      )
    );
  end if;

  perform agent_private.fail('unsupported_action');
  return null;
end;
$$;

create or replace function agent_api.resume(
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
  v_profile_key text;
  v_slot smallint;
  v_session_id uuid;
begin
  select id into v_project_id
  from agent_private.projects
  where project_key = p_project
    and repository_full_name = p_repository
    and enabled;
  if v_project_id is null then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'found', false, 'decision', 'project_repository_mismatch'
    );
  end if;
  select profile_key, slot into v_profile_key, v_slot
  from agent_private.parse_session_name(p_session_name);
  select id into v_session_id
  from agent_private.work_sessions
  where project_id = v_project_id
    and profile_key = v_profile_key
    and slot = v_slot
    and status in ('active','blocked','review')
  order by updated_at desc, created_at desc
  limit 1;
  if v_session_id is null then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'found', false, 'decision', 'no_active_work'
    );
  end if;
  return pg_catalog.jsonb_build_object(
    'contract_version', 1,
    'found', true,
    'decision', 'resume',
    'session', agent_private.session_snapshot(v_session_id)
  );
end;
$$;

create or replace function agent_api.context(
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
  v_current_base text;
begin
  select id, current_base_sha into v_project_id, v_current_base
  from agent_private.projects
  where project_key = p_project
    and repository_full_name = p_repository
    and enabled;
  if v_project_id is null then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'found', false, 'decision', 'project_repository_mismatch'
    );
  end if;
  select id into v_session_id
  from agent_private.work_sessions
  where project_id = v_project_id and agent_id = p_agent_id
  order by created_at desc
  limit 1;
  if v_session_id is null then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'found', false, 'decision', 'session_not_found'
    );
  end if;
  return pg_catalog.jsonb_build_object(
    'contract_version', 1,
    'found', true,
    'current_base_sha', v_current_base,
    'session', agent_private.session_snapshot(v_session_id)
  );
end;
$$;

create or replace function agent_api.ownership_check(
  p_project text,
  p_repository text,
  p_issue_number bigint,
  p_branch text,
  p_pr_number bigint default null,
  p_base_sha text default null,
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
  v_current_base text;
  v_session_id uuid;
  v_pr_number bigint;
  v_base_sha text;
  v_head_sha text;
begin
  if p_issue_number is null or p_issue_number < 1 then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'owned', false, 'decision', 'invalid_issue'
    );
  end if;
  select id, current_base_sha into v_project_id, v_current_base
  from agent_private.projects
  where project_key = p_project
    and repository_full_name = p_repository
    and enabled;
  if v_project_id is null then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'owned', false, 'decision', 'project_repository_mismatch'
    );
  end if;
  select b.session_id, b.pr_number, e.base_sha, e.head_sha
    into v_session_id, v_pr_number, v_base_sha, v_head_sha
  from agent_private.work_bindings b
  join agent_private.work_evidence e on e.session_id = b.session_id
  join agent_private.work_sessions s on s.id = b.session_id
  where b.project_id = v_project_id
    and b.issue_number = p_issue_number
    and b.branch = p_branch
    and b.active
    and s.status in ('active','blocked','review')
  limit 1;
  if v_session_id is null then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'owned', false, 'decision', 'binding_not_found'
    );
  end if;
  if p_pr_number is not null and p_pr_number <> v_pr_number then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'owned', false, 'decision', 'pr_mismatch'
    );
  end if;
  if p_base_sha is not null
     and (p_base_sha <> v_base_sha or p_base_sha <> v_current_base) then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'owned', false, 'decision', 'base_mismatch'
    );
  end if;
  if p_head_sha is not null and p_head_sha <> v_head_sha then
    return pg_catalog.jsonb_build_object(
      'contract_version', 1, 'owned', false, 'decision', 'head_mismatch'
    );
  end if;
  return pg_catalog.jsonb_build_object(
    'contract_version', 1,
    'owned', true,
    'decision', 'owned',
    'session', agent_private.session_snapshot(v_session_id)
  );
end;
$$;

revoke all on function agent_api.command(jsonb) from public, anon, authenticated;
revoke all on function agent_api.resume(text, text, text) from public, anon, authenticated;
revoke all on function agent_api.context(text, text, text) from public, anon, authenticated;
revoke all on function agent_api.ownership_check(text, text, bigint, text, bigint, text, text)
  from public, anon, authenticated;

grant execute on function agent_api.command(jsonb) to service_role;
grant execute on function agent_api.resume(text, text, text) to service_role;
grant execute on function agent_api.context(text, text, text) to service_role;
grant execute on function agent_api.ownership_check(text, text, bigint, text, bigint, text, text)
  to service_role;

commit;
