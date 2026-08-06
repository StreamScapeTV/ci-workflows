-- Issue #52: PostgreSQL RAISE options may not be assigned null.
begin;
create or replace function agent_private.fail(p_code text,p_detail text default null)
returns void
language plpgsql
security invoker
set search_path=pg_catalog
as $$
begin
  if p_detail is null then
    raise exception using errcode='P0001',message='agent_state:'||p_code;
  end if;
  raise exception using errcode='P0001',message='agent_state:'||p_code,detail=p_detail;
end;
$$;
revoke all on function agent_private.fail(text,text)
  from public,anon,authenticated,service_role;
commit;
