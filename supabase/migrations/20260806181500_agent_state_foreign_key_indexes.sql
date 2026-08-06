-- Issue #52: cover authoritative foreign-key lookup and delete-check paths.
begin;
create index claims_created_request_idx on agent_private.claims(created_request_id);
create index claims_released_request_idx on agent_private.claims(released_request_id) where released_request_id is not null;
create index command_receipts_project_idx on agent_private.command_receipts(project_id);
create index command_receipts_session_idx on agent_private.command_receipts(session_id) where session_id is not null;
create index events_request_idx on agent_private.events(request_id);
create index events_receipt_idx on agent_private.events(receipt_id);
create index project_slots_profile_idx on agent_private.project_slots(profile_key,project_id,slot);
create index requests_project_idx on agent_private.requests(project_id);
create index work_bindings_session_project_idx on agent_private.work_bindings(session_id,project_id);
create index work_evidence_session_project_idx on agent_private.work_evidence(session_id,project_id);
commit;
