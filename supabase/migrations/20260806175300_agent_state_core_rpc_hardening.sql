-- Issue #52: correct first-review assertions and validate selective release requests.
begin;
do $$
declare
  v_definition text;
begin
  v_definition := pg_get_functiondef('agent_api.command(jsonb)'::regprocedure);

  v_definition := replace(
    v_definition,
    'if v_pr is not null and v_binding_pr is distinct from v_pr then perform agent_private.fail(''stale_pr_assertion''); end if;',
    'if v_action <> ''review'' and v_pr is not null and v_binding_pr is distinct from v_pr then perform agent_private.fail(''stale_pr_assertion''); end if;'
  );
  v_definition := replace(
    v_definition,
    'if v_head is not null and v_evidence_head is distinct from v_head then perform agent_private.fail(''stale_head_assertion''); end if;',
    'if v_action <> ''review'' and v_head is not null and v_evidence_head is distinct from v_head then perform agent_private.fail(''stale_head_assertion''); end if;'
  );
  v_definition := replace(
    v_definition,
    'v_all:=coalesce((p_request->>''all'')::boolean,false); v_claims:=coalesce(p_request->''claims'',''[]''::jsonb);',
    'if p_request ? ''all'' and jsonb_typeof(p_request->''all'') <> ''boolean'' then perform agent_private.fail(''invalid_field_type'',''all''); end if; v_all:=coalesce((p_request->>''all'')::boolean,false); v_claims:=coalesce(p_request->''claims'',''[]''::jsonb); if jsonb_typeof(v_claims) <> ''array'' then perform agent_private.fail(''claims_must_be_array''); end if; if not v_all then for v_claim in select value from jsonb_array_elements(v_claims) loop perform agent_private.assert_object_keys(v_claim,array[''kind'',''mode'',''value'']); perform agent_private.normalize_claim_value(agent_private.require_text(v_claim,''kind'',1,16),agent_private.require_text(v_claim,''mode'',1,8),agent_private.require_text(v_claim,''value'',1,512)); end loop; end if;'
  );

  if strpos(v_definition, 'v_action <> ''review'' and v_pr is not null') = 0
     or strpos(v_definition, 'v_action <> ''review'' and v_head is not null') = 0
     or strpos(v_definition, 'claims_must_be_array') = 0 then
    raise exception 'agent_state:rpc_hardening_source_mismatch';
  end if;

  execute v_definition;
end;
$$;
revoke all on function agent_api.command(jsonb) from public, anon, authenticated;
grant execute on function agent_api.command(jsonb) to service_role;
commit;
