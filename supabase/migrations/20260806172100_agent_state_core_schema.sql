-- Issue #52: private normalized Supabase Agent State authority.
-- PostgreSQL 17. Extension versions are intentionally not pinned.

begin;

create schema if not exists agent_private authorization postgres;
create schema if not exists agent_api authorization postgres;

revoke all on schema agent_private from public, anon, authenticated, service_role;
revoke all on schema agent_api from public, anon, authenticated;
grant usage on schema agent_api to service_role;

alter default privileges for role postgres in schema agent_private
  revoke all on tables from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema agent_private
  revoke all on sequences from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema agent_private
  revoke execute on functions from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema agent_api
  revoke execute on functions from public, anon, authenticated;

create table agent_private.projects (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  project_key text not null unique,
  repository_full_name text not null unique,
  integration_branch text not null,
  current_base_sha text,
  enabled boolean not null default true,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  updated_at timestamptz not null default pg_catalog.clock_timestamp(),
  constraint projects_project_key_check
    check (project_key ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,62}$'),
  constraint projects_repository_check
    check (repository_full_name ~ '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'),
  constraint projects_branch_check check (
    integration_branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$'
    and integration_branch !~ '(^|/)\.\.?(/|$)'
    and integration_branch !~ '\.\.'
    and integration_branch !~ '//'
    and integration_branch !~ '@\{'
    and integration_branch !~ '(^|/)[^/]*\.lock(/|$)'
    and right(integration_branch, 1) not in ('/', '.')
  ),
  constraint projects_base_sha_check
    check (current_base_sha is null or current_base_sha ~ '^[0-9a-f]{40}$')
);

create table agent_private.profiles (
  profile_key text primary key,
  display_name text not null unique,
  id_prefix text not null unique,
  constraint profiles_key_check check (profile_key in ('agent', 'codex')),
  constraint profiles_display_check check (display_name in ('Agent', 'Codex')),
  constraint profiles_prefix_check check (id_prefix in ('gpt-agent', 'cod-agent'))
);

create table agent_private.project_slots (
  project_id uuid not null references agent_private.projects(id) on delete restrict,
  profile_key text not null references agent_private.profiles(profile_key) on delete restrict,
  slot smallint not null,
  enabled boolean not null default true,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  primary key (project_id, profile_key, slot),
  constraint project_slots_slot_check check (slot between 1 and 99)
);

create table agent_private.work_sessions (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  project_id uuid not null references agent_private.projects(id) on delete restrict,
  profile_key text not null,
  slot smallint not null,
  session_name text not null,
  agent_id text not null unique,
  task text not null,
  status text not null,
  blocked_reason text,
  terminal_summary text,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  updated_at timestamptz not null default pg_catalog.clock_timestamp(),
  terminal_at timestamptz,
  unique (id, project_id),
  foreign key (project_id, profile_key, slot)
    references agent_private.project_slots(project_id, profile_key, slot) on delete restrict,
  constraint work_sessions_session_name_check
    check (session_name ~ '^(Agent|Codex) ([1-9][0-9]?)$'),
  constraint work_sessions_agent_id_check
    check (agent_id ~ '^(gpt-agent|cod-agent)-[1-9][0-9]?-[0-9]{8}-[0-9]{4}-[a-z0-9]{4}$'),
  constraint work_sessions_task_check check (length(task) between 1 and 2000),
  constraint work_sessions_status_check
    check (status in ('active', 'blocked', 'review', 'done', 'cancelled')),
  constraint work_sessions_blocked_reason_check
    check (blocked_reason is null or length(blocked_reason) between 1 and 2000),
  constraint work_sessions_terminal_summary_check
    check (terminal_summary is null or length(terminal_summary) between 1 and 2000),
  constraint work_sessions_terminal_shape_check check (
    (status in ('done', 'cancelled') and terminal_at is not null)
    or (status not in ('done', 'cancelled') and terminal_at is null)
  )
);

create unique index work_sessions_one_current_per_slot
  on agent_private.work_sessions(project_id, profile_key, slot)
  where status in ('active', 'blocked', 'review');
create index work_sessions_project_status_idx
  on agent_private.work_sessions(project_id, status, updated_at desc);
create index work_sessions_slot_fk_idx
  on agent_private.work_sessions(project_id, profile_key, slot);

create table agent_private.work_bindings (
  session_id uuid primary key,
  project_id uuid not null,
  issue_number bigint not null,
  branch text not null,
  branch_nonce text not null,
  pr_number bigint,
  active boolean not null default true,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  updated_at timestamptz not null default pg_catalog.clock_timestamp(),
  foreign key (session_id, project_id)
    references agent_private.work_sessions(id, project_id) on delete restrict,
  constraint work_bindings_issue_check check (issue_number > 0),
  constraint work_bindings_pr_check check (pr_number is null or pr_number > 0),
  constraint work_bindings_branch_nonce_check check (branch_nonce ~ '^[a-z0-9]{4}$'),
  constraint work_bindings_branch_check check (
    branch ~ '^issue/[1-9][0-9]*-[a-z0-9][a-z0-9-]{0,180}-[a-z0-9]{4}$'
    and branch !~ '\.\.'
    and branch !~ '//'
    and branch !~ '@\{'
    and branch !~ '(^|/)[^/]*\.lock(/|$)'
    and right(branch, 1) not in ('/', '.')
  )
);

create unique index work_bindings_active_issue
  on agent_private.work_bindings(project_id, issue_number) where active;
create unique index work_bindings_active_branch
  on agent_private.work_bindings(project_id, branch) where active;
create unique index work_bindings_active_pr
  on agent_private.work_bindings(project_id, pr_number)
  where active and pr_number is not null;
create index work_bindings_project_fk_idx on agent_private.work_bindings(project_id);

create table agent_private.work_evidence (
  session_id uuid primary key,
  project_id uuid not null,
  base_sha text not null,
  head_sha text,
  merge_sha text,
  updated_at timestamptz not null default pg_catalog.clock_timestamp(),
  foreign key (session_id, project_id)
    references agent_private.work_sessions(id, project_id) on delete restrict,
  constraint work_evidence_base_check check (base_sha ~ '^[0-9a-f]{40}$'),
  constraint work_evidence_head_check check (head_sha is null or head_sha ~ '^[0-9a-f]{40}$'),
  constraint work_evidence_merge_check check (merge_sha is null or merge_sha ~ '^[0-9a-f]{40}$')
);
create index work_evidence_project_fk_idx on agent_private.work_evidence(project_id);

create table agent_private.requests (
  request_id text primary key,
  request_hash text not null,
  action text not null,
  project_id uuid not null references agent_private.projects(id) on delete restrict,
  session_name text not null,
  agent_id text,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  constraint requests_request_id_check
    check (request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'),
  constraint requests_hash_check check (request_hash ~ '^[0-9a-f]{64}$'),
  constraint requests_action_check check (
    action in ('resume', 'start', 'claim', 'release', 'reconcile_base', 'block', 'review', 'done', 'cancel')
  )
);
create index requests_project_fk_idx on agent_private.requests(project_id);

create table agent_private.command_receipts (
  receipt_id uuid primary key default pg_catalog.gen_random_uuid(),
  request_id text not null unique references agent_private.requests(request_id) on delete restrict,
  project_id uuid not null references agent_private.projects(id) on delete restrict,
  session_id uuid references agent_private.work_sessions(id) on delete restrict,
  action text not null,
  response jsonb not null,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  constraint command_receipts_response_check check (jsonb_typeof(response) = 'object')
);
create index command_receipts_project_fk_idx on agent_private.command_receipts(project_id);
create index command_receipts_session_fk_idx on agent_private.command_receipts(session_id)
  where session_id is not null;

create table agent_private.claims (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  project_id uuid not null references agent_private.projects(id) on delete restrict,
  session_id uuid not null references agent_private.work_sessions(id) on delete restrict,
  kind text not null,
  mode text not null,
  value text not null,
  active boolean not null default true,
  created_request_id text not null references agent_private.requests(request_id) on delete restrict,
  released_request_id text references agent_private.requests(request_id) on delete restrict,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  released_at timestamptz,
  constraint claims_kind_check
    check (kind in ('file', 'package', 'resource', 'manifest', 'device')),
  constraint claims_mode_check check (mode in ('exact', 'prefix')),
  constraint claims_prefix_check check (mode = 'exact' or kind = 'file'),
  constraint claims_value_length_check check (length(value) between 1 and 512),
  constraint claims_release_shape_check check (
    (active and released_request_id is null and released_at is null)
    or (not active and released_request_id is not null and released_at is not null)
  )
);

create unique index claims_session_active_identity
  on agent_private.claims(session_id, kind, mode, value) where active;
create index claims_project_active_lookup
  on agent_private.claims(project_id, kind, value, mode, session_id) where active;
create index claims_session_active_lookup
  on agent_private.claims(session_id, kind, value) where active;
create index claims_project_fk_idx on agent_private.claims(project_id);
create index claims_session_fk_idx on agent_private.claims(session_id);
create index claims_created_request_fk_idx on agent_private.claims(created_request_id);
create index claims_released_request_fk_idx on agent_private.claims(released_request_id)
  where released_request_id is not null;

create table agent_private.events (
  event_id bigint generated always as identity primary key,
  project_id uuid not null references agent_private.projects(id) on delete restrict,
  session_id uuid references agent_private.work_sessions(id) on delete restrict,
  request_id text not null references agent_private.requests(request_id) on delete restrict,
  receipt_id uuid not null references agent_private.command_receipts(receipt_id) on delete restrict,
  event_type text not null,
  payload jsonb not null,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  constraint events_type_check check (event_type in (
    'resume', 'started', 'claims_acquired', 'claims_released',
    'base_reconciled', 'blocked', 'review', 'done', 'cancelled',
    'claim_conflict', 'no_active_work'
  )),
  constraint events_payload_check check (jsonb_typeof(payload) = 'object')
);
create index events_project_created_idx
  on agent_private.events(project_id, created_at desc, event_id desc);
create index events_session_created_idx
  on agent_private.events(session_id, created_at desc, event_id desc)
  where session_id is not null;
create index events_request_fk_idx on agent_private.events(request_id);
create index events_receipt_fk_idx on agent_private.events(receipt_id);

alter table agent_private.projects enable row level security;
alter table agent_private.projects force row level security;
alter table agent_private.profiles enable row level security;
alter table agent_private.profiles force row level security;
alter table agent_private.project_slots enable row level security;
alter table agent_private.project_slots force row level security;
alter table agent_private.work_sessions enable row level security;
alter table agent_private.work_sessions force row level security;
alter table agent_private.work_bindings enable row level security;
alter table agent_private.work_bindings force row level security;
alter table agent_private.work_evidence enable row level security;
alter table agent_private.work_evidence force row level security;
alter table agent_private.requests enable row level security;
alter table agent_private.requests force row level security;
alter table agent_private.command_receipts enable row level security;
alter table agent_private.command_receipts force row level security;
alter table agent_private.claims enable row level security;
alter table agent_private.claims force row level security;
alter table agent_private.events enable row level security;
alter table agent_private.events force row level security;

revoke all on all tables in schema agent_private from public, anon, authenticated, service_role;
revoke all on all sequences in schema agent_private from public, anon, authenticated, service_role;

create or replace function agent_private.reject_immutable_mutation()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  raise exception using errcode = 'P0001', message = 'agent_state:immutable_record';
end;
$$;

create trigger requests_immutable
before update or delete on agent_private.requests
for each row execute function agent_private.reject_immutable_mutation();

create trigger receipts_immutable
before update or delete on agent_private.command_receipts
for each row execute function agent_private.reject_immutable_mutation();

create trigger events_append_only
before update or delete on agent_private.events
for each row execute function agent_private.reject_immutable_mutation();

create or replace function agent_private.fail(p_code text, p_detail text default null)
returns void
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  raise exception using
    errcode = 'P0001',
    message = 'agent_state:' || p_code,
    detail = p_detail;
end;
$$;

create or replace function agent_private.assert_object_keys(p_value jsonb, p_allowed text[])
returns void
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_key text;
begin
  if p_value is null or pg_catalog.jsonb_typeof(p_value) <> 'object' then
    perform agent_private.fail('request_must_be_object');
  end if;
  for v_key in select pg_catalog.jsonb_object_keys(p_value)
  loop
    if not (v_key = any(p_allowed)) then
      perform agent_private.fail('unknown_field', v_key);
    end if;
  end loop;
end;
$$;

create or replace function agent_private.require_text(
  p_object jsonb, p_key text, p_min integer default 1, p_max integer default 2000
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_value text;
begin
  if not (p_object ? p_key) or pg_catalog.jsonb_typeof(p_object -> p_key) <> 'string' then
    perform agent_private.fail('missing_or_invalid_field', p_key);
  end if;
  v_value := p_object ->> p_key;
  if pg_catalog.length(v_value) < p_min or pg_catalog.length(v_value) > p_max then
    perform agent_private.fail('field_length', p_key);
  end if;
  return v_value;
end;
$$;

create or replace function agent_private.optional_text(
  p_object jsonb, p_key text, p_max integer default 2000
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_value text;
begin
  if not (p_object ? p_key) or p_object -> p_key = 'null'::jsonb then
    return null;
  end if;
  if pg_catalog.jsonb_typeof(p_object -> p_key) <> 'string' then
    perform agent_private.fail('invalid_field_type', p_key);
  end if;
  v_value := p_object ->> p_key;
  if pg_catalog.length(v_value) < 1 or pg_catalog.length(v_value) > p_max then
    perform agent_private.fail('field_length', p_key);
  end if;
  return v_value;
end;
$$;

create or replace function agent_private.require_positive_bigint(p_object jsonb, p_key text)
returns bigint
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_text text;
  v_value bigint;
begin
  if not (p_object ? p_key) or pg_catalog.jsonb_typeof(p_object -> p_key) <> 'number' then
    perform agent_private.fail('missing_or_invalid_field', p_key);
  end if;
  v_text := p_object ->> p_key;
  if v_text !~ '^[0-9]+$' then
    perform agent_private.fail('invalid_positive_integer', p_key);
  end if;
  v_value := v_text::bigint;
  if v_value < 1 then
    perform agent_private.fail('invalid_positive_integer', p_key);
  end if;
  return v_value;
exception
  when numeric_value_out_of_range then
    perform agent_private.fail('invalid_positive_integer', p_key);
    return null;
end;
$$;

create or replace function agent_private.optional_positive_bigint(p_object jsonb, p_key text)
returns bigint
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if not (p_object ? p_key) or p_object -> p_key = 'null'::jsonb then
    return null;
  end if;
  return agent_private.require_positive_bigint(p_object, p_key);
end;
$$;

create or replace function agent_private.assert_sha(
  p_value text, p_field text, p_nullable boolean default false
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if p_value is null and p_nullable then
    return null;
  end if;
  if p_value is null or p_value !~ '^[0-9a-f]{40}$' then
    perform agent_private.fail('invalid_sha', p_field);
  end if;
  return p_value;
end;
$$;

create or replace function agent_private.parse_session_name(p_session_name text)
returns table(profile_key text, slot smallint)
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_match text[];
begin
  v_match := pg_catalog.regexp_match(p_session_name, '^(Agent|Codex) ([1-9][0-9]?)$');
  if v_match is null then
    perform agent_private.fail('invalid_session_name');
  end if;
  profile_key := case v_match[1] when 'Agent' then 'agent' else 'codex' end;
  slot := v_match[2]::smallint;
  return next;
end;
$$;

create or replace function agent_private.assert_agent_id(
  p_agent_id text, p_profile_key text, p_slot smallint
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_prefix text;
  v_pattern text;
begin
  select id_prefix into strict v_prefix
  from agent_private.profiles where profile_key = p_profile_key;
  v_pattern := '^' || v_prefix || '-' || p_slot::text ||
    '-[0-9]{8}-[0-9]{4}-[a-z0-9]{4}$';
  if p_agent_id is null or p_agent_id !~ v_pattern then
    perform agent_private.fail('invalid_agent_id');
  end if;
  return p_agent_id;
end;
$$;

create or replace function agent_private.generate_agent_id(
  p_profile_key text, p_slot smallint
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog, extensions
as $$
declare
  v_prefix text;
  v_agent_id text;
  v_attempt integer;
begin
  select id_prefix into strict v_prefix
  from agent_private.profiles where profile_key = p_profile_key;
  for v_attempt in 1..16 loop
    v_agent_id := v_prefix || '-' || p_slot::text || '-' ||
      pg_catalog.to_char(pg_catalog.clock_timestamp() at time zone 'UTC', 'YYYYMMDD-HH24MI') ||
      '-' || pg_catalog.substring(pg_catalog.encode(extensions.gen_random_bytes(3), 'hex') from 1 for 4);
    if not exists (select 1 from agent_private.work_sessions where agent_id = v_agent_id) then
      return v_agent_id;
    end if;
  end loop;
  perform agent_private.fail('agent_id_generation_failed');
  return null;
end;
$$;

create or replace function agent_private.assert_branch(
  p_branch text, p_issue_number bigint, p_branch_nonce text
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_nonce text;
begin
  if p_branch is null
     or p_branch !~ ('^issue/' || p_issue_number::text || '-[a-z0-9][a-z0-9-]{0,180}-[a-z0-9]{4}$')
     or p_branch ~ '\.\.'
     or p_branch ~ '//'
     or p_branch ~ '@\{'
     or p_branch ~ '(^|/)[^/]*\.lock(/|$)'
     or right(p_branch, 1) in ('/', '.') then
    perform agent_private.fail('invalid_branch');
  end if;
  v_nonce := right(p_branch, 4);
  if p_branch_nonce is not null and p_branch_nonce <> v_nonce then
    perform agent_private.fail('branch_nonce_mismatch');
  end if;
  return v_nonce;
end;
$$;

create or replace function agent_private.normalize_claim_value(
  p_kind text, p_mode text, p_value text
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if p_kind not in ('file', 'package', 'resource', 'manifest', 'device') then
    perform agent_private.fail('invalid_claim_kind', p_kind);
  end if;
  if p_mode not in ('exact', 'prefix') then
    perform agent_private.fail('invalid_claim_mode', p_mode);
  end if;
  if p_mode = 'prefix' and p_kind <> 'file' then
    perform agent_private.fail('prefix_requires_file');
  end if;
  if p_value is null or length(p_value) < 1 or length(p_value) > 512 then
    perform agent_private.fail('invalid_claim_value');
  end if;
  if p_kind = 'file' then
    if p_value !~ '^[A-Za-z0-9._@+:-]+(/[A-Za-z0-9._@+:-]+)*$'
       or p_value ~ '(^|/)\.\.?(/|$)'
       or p_value ~ '//'
       or p_value ~ '\\'
       or left(p_value, 1) = '/' then
      perform agent_private.fail('unsafe_path', p_value);
    end if;
  else
    if p_value !~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$'
       or p_value ~ '\.\.'
       or p_value ~ '//' then
      perform agent_private.fail('unsafe_claim_identifier', p_value);
    end if;
  end if;
  return p_value;
end;
$$;

create or replace function agent_private.find_claim_collision(
  p_project_id uuid, p_session_id uuid, p_kind text, p_mode text, p_value text
)
returns jsonb
language sql
security invoker
set search_path = pg_catalog
as $$
  select pg_catalog.jsonb_build_object(
    'kind', c.kind,
    'mode', c.mode,
    'value', c.value,
    'owner_agent_id', s.agent_id,
    'owner_issue_number', b.issue_number
  )
  from agent_private.claims c
  join agent_private.work_sessions s on s.id = c.session_id
  join agent_private.work_bindings b on b.session_id = s.id
  where c.project_id = p_project_id
    and c.active
    and (p_session_id is null or c.session_id <> p_session_id)
    and (
      (
        p_kind = 'file' and c.kind = 'file'
        and (
          (p_mode = 'exact' and c.mode = 'exact' and p_value = c.value)
          or (c.mode = 'prefix' and (p_value = c.value or p_value like c.value || '/%'))
          or (p_mode = 'prefix' and (c.value = p_value or c.value like p_value || '/%'))
        )
      )
      or (p_kind <> 'file' and c.kind = p_kind and c.value = p_value)
    )
  order by c.kind, c.value, s.agent_id
  limit 1
$$;

create or replace function agent_private.lock_session(
  p_project_id uuid,
  p_session_name text,
  p_agent_id text,
  p_issue_number bigint,
  p_branch text
)
returns uuid
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_session_id uuid;
  v_profile_key text;
  v_slot smallint;
begin
  select profile_key, slot into v_profile_key, v_slot
  from agent_private.parse_session_name(p_session_name);
  perform agent_private.assert_agent_id(p_agent_id, v_profile_key, v_slot);

  select s.id into v_session_id
  from agent_private.work_sessions s
  join agent_private.work_bindings b on b.session_id = s.id
  where s.project_id = p_project_id
    and s.session_name = p_session_name
    and s.agent_id = p_agent_id
    and s.status in ('active', 'blocked', 'review')
    and b.active
    and b.issue_number = p_issue_number
    and b.branch = p_branch
  for update of s, b;

  if v_session_id is null then
    perform agent_private.fail('session_assertion_failed');
  end if;
  return v_session_id;
end;
$$;

create or replace function agent_private.session_snapshot(p_session_id uuid)
returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog
as $$
  select pg_catalog.jsonb_build_object(
    'agent_id', s.agent_id,
    'session_name', s.session_name,
    'profile', s.profile_key,
    'slot', s.slot,
    'status', s.status,
    'task', s.task,
    'issue_number', b.issue_number,
    'branch', b.branch,
    'branch_nonce', b.branch_nonce,
    'pr_number', b.pr_number,
    'base_sha', e.base_sha,
    'head_sha', e.head_sha,
    'merge_sha', e.merge_sha,
    'blocked_reason', s.blocked_reason,
    'terminal_summary', s.terminal_summary,
    'claims', coalesce(
      (
        select pg_catalog.jsonb_agg(
          pg_catalog.jsonb_build_object('kind', c.kind, 'mode', c.mode, 'value', c.value)
          order by c.kind, c.value, c.mode
        )
        from agent_private.claims c
        where c.session_id = s.id and c.active
      ),
      '[]'::jsonb
    ),
    'updated_at', s.updated_at
  )
  from agent_private.work_sessions s
  join agent_private.work_bindings b on b.session_id = s.id
  join agent_private.work_evidence e on e.session_id = s.id
  where s.id = p_session_id
$$;

create or replace function agent_private.complete_request(
  p_request_id text,
  p_project_id uuid,
  p_session_id uuid,
  p_action text,
  p_event_type text,
  p_response jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_receipt_id uuid := pg_catalog.gen_random_uuid();
  v_project_key text;
  v_response jsonb;
begin
  select project_key into strict v_project_key
  from agent_private.projects where id = p_project_id;

  v_response := p_response || pg_catalog.jsonb_build_object(
    'contract_version', 1,
    'request_id', p_request_id,
    'receipt_id', v_receipt_id,
    'action', p_action,
    'project', v_project_key
  );

  insert into agent_private.command_receipts(
    receipt_id, request_id, project_id, session_id, action, response
  ) values (
    v_receipt_id, p_request_id, p_project_id, p_session_id, p_action, v_response
  );

  insert into agent_private.events(
    project_id, session_id, request_id, receipt_id, event_type, payload
  ) values (
    p_project_id, p_session_id, p_request_id, v_receipt_id, p_event_type,
    v_response - 'receipt_id'
  );

  return v_response;
end;
$$;

revoke all on all functions in schema agent_private from public, anon, authenticated, service_role;

insert into agent_private.profiles(profile_key, display_name, id_prefix)
values
  ('agent', 'Agent', 'gpt-agent'),
  ('codex', 'Codex', 'cod-agent');

insert into agent_private.projects(project_key, repository_full_name, integration_branch)
values
  ('ci-workflows', 'StreamScapeTV/ci-workflows', 'main'),
  ('iptv-backend', 'StreamScapeTV/iptv-backend', 'main'),
  ('StreamScapeWeb', 'StreamScapeTV/StreamScapeWeb', 'main'),
  ('iptv-android', 'StreamScapeTV/iptv-android', 'develop'),
  ('iptv-apple', 'StreamScapeTV/iptv-apple', 'develop'),
  ('streamscape-media', 'StreamScapeTV/streamscape-media', 'develop'),
  ('directus-front', 'StreamScapeTV/directus-front', 'main'),
  ('finance-hub', 'StreamScapeTV/finance-hub', 'main'),
  ('agent-state', 'StreamScapeTV/agent-state', 'main'),
  ('flux', 'StreamScapeTV/flux', 'main'),
  ('organization-rules', 'StreamScapeTV/organization-rules', 'main');

insert into agent_private.project_slots(project_id, profile_key, slot)
select p.id, f.profile_key, s.slot
from agent_private.projects p
cross join agent_private.profiles f
cross join lateral (
  select pg_catalog.generate_series(1, 9)::smallint as slot
) s;

commit;
