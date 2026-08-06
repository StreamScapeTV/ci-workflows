-- Issue #52: private normalized Supabase Agent State authority.
begin;
create schema agent_private authorization postgres;
create schema agent_api authorization postgres;
revoke all on schema agent_private from public, anon, authenticated, service_role;
revoke all on schema agent_api from public, anon, authenticated;
grant usage on schema agent_api to service_role;
alter default privileges for role postgres in schema agent_private revoke all on tables from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema agent_private revoke all on sequences from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema agent_private revoke execute on functions from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema agent_api revoke execute on functions from public, anon, authenticated;

create table agent_private.projects(
 id uuid primary key default gen_random_uuid(), project_key text not null unique,
 repository_full_name text not null unique, integration_branch text not null,
 current_base_sha text, enabled boolean not null default true,
 created_at timestamptz not null default clock_timestamp(), updated_at timestamptz not null default clock_timestamp(),
 check(project_key ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,62}$'),
 check(repository_full_name ~ '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'),
 check(integration_branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' and integration_branch !~ '(^|/)\.\.?(/|$)' and integration_branch !~ '\.\.|//|@\{' and integration_branch !~ '(^|/)[^/]*\.lock(/|$)' and right(integration_branch,1) not in ('/','.')),
 check(current_base_sha is null or current_base_sha ~ '^[0-9a-f]{40}$'));
create table agent_private.profiles(profile_key text primary key, display_name text not null unique, id_prefix text not null unique,
 check(profile_key in('agent','codex')), check(display_name in('Agent','Codex')), check(id_prefix in('gpt-agent','cod-agent')));
create table agent_private.project_slots(
 project_id uuid not null references agent_private.projects, profile_key text not null references agent_private.profiles,
 slot smallint not null check(slot between 1 and 99), enabled boolean not null default true,
 created_at timestamptz not null default clock_timestamp(), primary key(project_id,profile_key,slot));
create table agent_private.work_sessions(
 id uuid primary key default gen_random_uuid(), project_id uuid not null references agent_private.projects,
 profile_key text not null, slot smallint not null, session_name text not null,
 agent_id text not null unique, task text not null, status text not null,
 blocked_reason text, terminal_summary text, created_at timestamptz not null default clock_timestamp(),
 updated_at timestamptz not null default clock_timestamp(), terminal_at timestamptz,
 unique(id,project_id), foreign key(project_id,profile_key,slot) references agent_private.project_slots,
 check(session_name ~ '^(Agent|Codex) ([1-9][0-9]?)$'),
 check(agent_id ~ '^(gpt-agent|cod-agent)-[1-9][0-9]?-[0-9]{8}-[0-9]{4}-[a-z0-9]{4}$'),
 check(length(task) between 1 and 2000), check(status in('active','blocked','review','done','cancelled')),
 check(blocked_reason is null or length(blocked_reason) between 1 and 2000),
 check(terminal_summary is null or length(terminal_summary) between 1 and 2000),
 check((status in('done','cancelled') and terminal_at is not null) or (status not in('done','cancelled') and terminal_at is null)));
create unique index work_sessions_one_current_per_slot on agent_private.work_sessions(project_id,profile_key,slot) where status in('active','blocked','review');
create index work_sessions_project_status_idx on agent_private.work_sessions(project_id,status,updated_at desc);
create table agent_private.work_bindings(
 session_id uuid primary key, project_id uuid not null, issue_number bigint not null check(issue_number>0),
 branch text not null, branch_nonce text not null check(branch_nonce ~ '^[a-z0-9]{4}$'), pr_number bigint check(pr_number is null or pr_number>0),
 active boolean not null default true, created_at timestamptz not null default clock_timestamp(), updated_at timestamptz not null default clock_timestamp(),
 foreign key(session_id,project_id) references agent_private.work_sessions(id,project_id),
 check(branch ~ '^issue/[1-9][0-9]*-[a-z0-9][a-z0-9-]{0,180}-[a-z0-9]{4}$' and branch !~ '\.\.|//|@\{' and branch !~ '(^|/)[^/]*\.lock(/|$)' and right(branch,1) not in('/','.')));
create unique index work_bindings_active_issue on agent_private.work_bindings(project_id,issue_number) where active;
create unique index work_bindings_active_branch on agent_private.work_bindings(project_id,branch) where active;
create unique index work_bindings_active_pr on agent_private.work_bindings(project_id,pr_number) where active and pr_number is not null;
create table agent_private.work_evidence(
 session_id uuid primary key, project_id uuid not null, base_sha text not null check(base_sha ~ '^[0-9a-f]{40}$'),
 head_sha text check(head_sha is null or head_sha ~ '^[0-9a-f]{40}$'), merge_sha text check(merge_sha is null or merge_sha ~ '^[0-9a-f]{40}$'),
 updated_at timestamptz not null default clock_timestamp(), foreign key(session_id,project_id) references agent_private.work_sessions(id,project_id));
create table agent_private.requests(
 request_id text primary key check(request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'),
 request_hash text not null check(request_hash ~ '^[0-9a-f]{64}$'), action text not null check(action in('resume','start','claim','release','reconcile_base','block','review','done','cancel')),
 project_id uuid not null references agent_private.projects, session_name text not null, agent_id text,
 created_at timestamptz not null default clock_timestamp());
create table agent_private.command_receipts(
 receipt_id uuid primary key default gen_random_uuid(), request_id text not null unique references agent_private.requests,
 project_id uuid not null references agent_private.projects, session_id uuid references agent_private.work_sessions,
 action text not null, response jsonb not null check(jsonb_typeof(response)='object'), created_at timestamptz not null default clock_timestamp());
create table agent_private.claims(
 id uuid primary key default gen_random_uuid(), project_id uuid not null references agent_private.projects,
 session_id uuid not null references agent_private.work_sessions, kind text not null check(kind in('file','package','resource','manifest','device')),
 mode text not null check(mode in('exact','prefix')), value text not null check(length(value) between 1 and 512), active boolean not null default true,
 created_request_id text not null references agent_private.requests, released_request_id text references agent_private.requests,
 created_at timestamptz not null default clock_timestamp(), released_at timestamptz,
 check(mode='exact' or kind='file'), check((active and released_request_id is null and released_at is null) or (not active and released_request_id is not null and released_at is not null)));
create unique index claims_session_active_identity on agent_private.claims(session_id,kind,mode,value) where active;
create index claims_project_active_lookup on agent_private.claims(project_id,kind,value,mode,session_id) where active;
create table agent_private.events(
 event_id bigint generated always as identity primary key, project_id uuid not null references agent_private.projects,
 session_id uuid references agent_private.work_sessions, request_id text not null references agent_private.requests,
 receipt_id uuid not null references agent_private.command_receipts, event_type text not null,
 payload jsonb not null check(jsonb_typeof(payload)='object'), created_at timestamptz not null default clock_timestamp(),
 check(event_type in('resume','started','claims_acquired','claims_released','base_reconciled','blocked','review','done','cancelled','claim_conflict','no_active_work')));
create index events_project_created_idx on agent_private.events(project_id,created_at desc,event_id desc);
create index events_session_created_idx on agent_private.events(session_id,created_at desc,event_id desc) where session_id is not null;

alter table agent_private.projects enable row level security; alter table agent_private.projects force row level security;
alter table agent_private.profiles enable row level security; alter table agent_private.profiles force row level security;
alter table agent_private.project_slots enable row level security; alter table agent_private.project_slots force row level security;
alter table agent_private.work_sessions enable row level security; alter table agent_private.work_sessions force row level security;
alter table agent_private.work_bindings enable row level security; alter table agent_private.work_bindings force row level security;
alter table agent_private.work_evidence enable row level security; alter table agent_private.work_evidence force row level security;
alter table agent_private.requests enable row level security; alter table agent_private.requests force row level security;
alter table agent_private.command_receipts enable row level security; alter table agent_private.command_receipts force row level security;
alter table agent_private.claims enable row level security; alter table agent_private.claims force row level security;
alter table agent_private.events enable row level security; alter table agent_private.events force row level security;
revoke all on all tables in schema agent_private from public,anon,authenticated,service_role;
revoke all on all sequences in schema agent_private from public,anon,authenticated,service_role;

create function agent_private.reject_immutable_mutation() returns trigger language plpgsql security invoker set search_path=pg_catalog as $$begin raise exception using errcode='P0001',message='agent_state:immutable_record'; end$$;
create trigger requests_immutable before update or delete on agent_private.requests for each row execute function agent_private.reject_immutable_mutation();
create trigger receipts_immutable before update or delete on agent_private.command_receipts for each row execute function agent_private.reject_immutable_mutation();
create trigger events_append_only before update or delete on agent_private.events for each row execute function agent_private.reject_immutable_mutation();
create function agent_private.fail(p_code text,p_detail text default null) returns void language plpgsql security invoker set search_path=pg_catalog as $$begin raise exception using errcode='P0001',message='agent_state:'||p_code,detail=p_detail; end$$;
create function agent_private.assert_object_keys(p_value jsonb,p_allowed text[]) returns void language plpgsql security invoker set search_path=pg_catalog as $$declare k text; begin if p_value is null or jsonb_typeof(p_value)<>'object' then perform agent_private.fail('request_must_be_object'); end if; for k in select jsonb_object_keys(p_value) loop if not(k=any(p_allowed)) then perform agent_private.fail('unknown_field',k); end if; end loop; end$$;
create function agent_private.require_text(o jsonb,k text,mn int default 1,mx int default 2000) returns text language plpgsql security invoker set search_path=pg_catalog as $$declare v text; begin if not(o?k) or jsonb_typeof(o->k)<>'string' then perform agent_private.fail('missing_or_invalid_field',k); end if; v:=o->>k; if length(v)<mn or length(v)>mx then perform agent_private.fail('field_length',k); end if; return v; end$$;
create function agent_private.optional_text(o jsonb,k text,mx int default 2000) returns text language plpgsql security invoker set search_path=pg_catalog as $$declare v text; begin if not(o?k) or o->k='null'::jsonb then return null; end if; if jsonb_typeof(o->k)<>'string' then perform agent_private.fail('invalid_field_type',k); end if; v:=o->>k; if length(v)<1 or length(v)>mx then perform agent_private.fail('field_length',k); end if; return v; end$$;
create function agent_private.require_positive_bigint(o jsonb,k text) returns bigint language plpgsql security invoker set search_path=pg_catalog as $$declare t text; v bigint; begin if not(o?k) or jsonb_typeof(o->k)<>'number' then perform agent_private.fail('missing_or_invalid_field',k); end if; t:=o->>k; if t!~'^[0-9]+$' then perform agent_private.fail('invalid_positive_integer',k); end if; v:=t::bigint; if v<1 then perform agent_private.fail('invalid_positive_integer',k); end if; return v; exception when numeric_value_out_of_range then perform agent_private.fail('invalid_positive_integer',k); return null; end$$;
create function agent_private.optional_positive_bigint(o jsonb,k text) returns bigint language plpgsql security invoker set search_path=pg_catalog as $$begin if not(o?k) or o->k='null'::jsonb then return null; end if; return agent_private.require_positive_bigint(o,k); end$$;
create function agent_private.assert_sha(v text,f text,n boolean default false) returns text language plpgsql security invoker set search_path=pg_catalog as $$begin if v is null and n then return null; end if; if v is null or v!~'^[0-9a-f]{40}$' then perform agent_private.fail('invalid_sha',f); end if; return v; end$$;
create function agent_private.parse_session_name(n text) returns table(profile_key text,slot smallint) language plpgsql security invoker set search_path=pg_catalog as $$declare m text[]; begin m:=regexp_match(n,'^(Agent|Codex) ([1-9][0-9]?)$'); if m is null then perform agent_private.fail('invalid_session_name'); end if; profile_key:=case m[1] when 'Agent' then 'agent' else 'codex' end; slot:=m[2]::smallint; return next; end$$;
create function agent_private.assert_agent_id(a text,p text,s smallint) returns text language plpgsql security invoker set search_path=pg_catalog as $$declare x text; begin select id_prefix into strict x from agent_private.profiles where profile_key=p; if a is null or a!~('^'||x||'-'||s::text||'-[0-9]{8}-[0-9]{4}-[a-z0-9]{4}$') then perform agent_private.fail('invalid_agent_id'); end if; return a; end$$;
create function agent_private.generate_agent_id(p text,s smallint) returns text language plpgsql security invoker set search_path=pg_catalog,extensions as $$declare x text;a text;i int; begin select id_prefix into strict x from agent_private.profiles where profile_key=p; for i in 1..16 loop a:=x||'-'||s::text||'-'||to_char(clock_timestamp() at time zone 'UTC','YYYYMMDD-HH24MI')||'-'||substr(encode(extensions.gen_random_bytes(3),'hex'),1,4); if not exists(select 1 from agent_private.work_sessions where agent_id=a) then return a; end if; end loop; perform agent_private.fail('agent_id_generation_failed'); return null; end$$;
create function agent_private.assert_branch(b text,i bigint,n text) returns text language plpgsql security invoker set search_path=pg_catalog as $$declare x text; begin if b is null or b!~('^issue/'||i::text||'-[a-z0-9][a-z0-9-]{0,180}-[a-z0-9]{4}$') or b~'\.\.|//|@\{' or b~'(^|/)[^/]*\.lock(/|$)' or right(b,1) in('/','.') then perform agent_private.fail('invalid_branch'); end if; x:=right(b,4); if n is not null and n<>x then perform agent_private.fail('branch_nonce_mismatch'); end if; return x; end$$;
create function agent_private.normalize_claim_value(k text,m text,v text) returns text language plpgsql security invoker set search_path=pg_catalog as $$begin if k not in('file','package','resource','manifest','device') then perform agent_private.fail('invalid_claim_kind',k); end if; if m not in('exact','prefix') then perform agent_private.fail('invalid_claim_mode',m); end if; if m='prefix' and k<>'file' then perform agent_private.fail('prefix_requires_file'); end if; if v is null or length(v)<1 or length(v)>512 then perform agent_private.fail('invalid_claim_value'); end if; if k='file' then if v!~'^[A-Za-z0-9._@+:-]+(/[A-Za-z0-9._@+:-]+)*$' or v~'(^|/)\.\.?(/|$)' or v~'//' or v~'\\' or left(v,1)='/' then perform agent_private.fail('unsafe_path',v); end if; else if v!~'^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$' or v~'\.\.|//' then perform agent_private.fail('unsafe_claim_identifier',v); end if; end if; return v; end$$;
create function agent_private.find_claim_collision(pid uuid,sid uuid,k text,m text,v text) returns jsonb language sql security invoker set search_path=pg_catalog as $$select jsonb_build_object('kind',c.kind,'mode',c.mode,'value',c.value,'owner_agent_id',s.agent_id,'owner_issue_number',b.issue_number) from agent_private.claims c join agent_private.work_sessions s on s.id=c.session_id join agent_private.work_bindings b on b.session_id=s.id where c.project_id=pid and c.active and(sid is null or c.session_id<>sid) and((k='file' and c.kind='file' and((m='exact' and c.mode='exact' and v=c.value)or(c.mode='prefix' and(v=c.value or v like c.value||'/%'))or(m='prefix' and(c.value=v or c.value like v||'/%'))))or(k<>'file' and c.kind=k and c.value=v)) order by c.kind,c.value,s.agent_id limit 1$$;
create function agent_private.lock_session(pid uuid,sn text,aid text,ino bigint,br text) returns uuid language plpgsql security invoker set search_path=pg_catalog as $$declare sid uuid;p text;s smallint; begin select profile_key,slot into p,s from agent_private.parse_session_name(sn); perform agent_private.assert_agent_id(aid,p,s); select ws.id into sid from agent_private.work_sessions ws join agent_private.work_bindings b on b.session_id=ws.id where ws.project_id=pid and ws.session_name=sn and ws.agent_id=aid and ws.status in('active','blocked','review') and b.active and b.issue_number=ino and b.branch=br for update of ws,b; if sid is null then perform agent_private.fail('session_assertion_failed'); end if; return sid; end$$;
create function agent_private.session_snapshot(sid uuid) returns jsonb language sql stable security invoker set search_path=pg_catalog as $$select jsonb_build_object('agent_id',s.agent_id,'session_name',s.session_name,'profile',s.profile_key,'slot',s.slot,'status',s.status,'task',s.task,'issue_number',b.issue_number,'branch',b.branch,'branch_nonce',b.branch_nonce,'pr_number',b.pr_number,'base_sha',e.base_sha,'head_sha',e.head_sha,'merge_sha',e.merge_sha,'blocked_reason',s.blocked_reason,'terminal_summary',s.terminal_summary,'claims',coalesce((select jsonb_agg(jsonb_build_object('kind',c.kind,'mode',c.mode,'value',c.value) order by c.kind,c.value,c.mode) from agent_private.claims c where c.session_id=s.id and c.active),'[]'::jsonb),'updated_at',s.updated_at) from agent_private.work_sessions s join agent_private.work_bindings b on b.session_id=s.id join agent_private.work_evidence e on e.session_id=s.id where s.id=sid$$;
create function agent_private.complete_request(rid text,pid uuid,sid uuid,a text,et text,r jsonb) returns jsonb language plpgsql security invoker set search_path=pg_catalog as $$declare cid uuid:=gen_random_uuid();pk text;o jsonb; begin select project_key into strict pk from agent_private.projects where id=pid; o:=r||jsonb_build_object('contract_version',1,'request_id',rid,'receipt_id',cid,'action',a,'project',pk); insert into agent_private.command_receipts(receipt_id,request_id,project_id,session_id,action,response) values(cid,rid,pid,sid,a,o); insert into agent_private.events(project_id,session_id,request_id,receipt_id,event_type,payload) values(pid,sid,rid,cid,et,o-'receipt_id'); return o; end$$;
revoke all on all functions in schema agent_private from public,anon,authenticated,service_role;

insert into agent_private.profiles values('agent','Agent','gpt-agent'),('codex','Codex','cod-agent');
insert into agent_private.projects(project_key,repository_full_name,integration_branch) values
('ci-workflows','StreamScapeTV/ci-workflows','main'),('iptv-backend','StreamScapeTV/iptv-backend','main'),('StreamScapeWeb','StreamScapeTV/StreamScapeWeb','main'),('iptv-android','StreamScapeTV/iptv-android','develop'),('iptv-apple','StreamScapeTV/iptv-apple','develop'),('streamscape-media','StreamScapeTV/streamscape-media','develop'),('directus-front','StreamScapeTV/directus-front','main'),('finance-hub','StreamScapeTV/finance-hub','main'),('agent-state','StreamScapeTV/agent-state','main'),('flux','StreamScapeTV/flux','main'),('organization-rules','StreamScapeTV/organization-rules','main');
insert into agent_private.project_slots(project_id,profile_key,slot) select p.id,f.profile_key,g::smallint from agent_private.projects p cross join agent_private.profiles f cross join generate_series(1,9) g;
commit;
