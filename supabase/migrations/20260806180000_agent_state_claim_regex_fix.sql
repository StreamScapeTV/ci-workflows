-- Issue #52: PostgreSQL regex repetition bounds are smaller than the claim length limit.
begin;
create or replace function agent_private.normalize_claim_value(k text,m text,v text)
returns text
language plpgsql
security invoker
set search_path=pg_catalog
as $$
begin
  if k not in('file','package','resource','manifest','device') then
    perform agent_private.fail('invalid_claim_kind',k);
  end if;
  if m not in('exact','prefix') then
    perform agent_private.fail('invalid_claim_mode',m);
  end if;
  if m='prefix' and k<>'file' then
    perform agent_private.fail('prefix_requires_file');
  end if;
  if v is null or length(v)<1 or length(v)>512 then
    perform agent_private.fail('invalid_claim_value');
  end if;
  if k='file' then
    if v!~'^[A-Za-z0-9._@+:-]+(/[A-Za-z0-9._@+:-]+)*$'
       or v~'(^|/)\.\.?(/|$)' or v~'//' or v~'\\' or left(v,1)='/' then
      perform agent_private.fail('unsafe_path',v);
    end if;
  else
    if v!~'^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$' or v~'\.\.|//' then
      perform agent_private.fail('unsafe_claim_identifier',v);
    end if;
  end if;
  return v;
end;
$$;
revoke all on function agent_private.normalize_claim_value(text,text,text)
  from public,anon,authenticated,service_role;
commit;
