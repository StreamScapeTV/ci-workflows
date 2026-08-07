-- Issue #52: indexes for bounded reads, foreign-key checks, and collision lookups.

begin;

create index work_sessions_project_status_idx
  on agent_private.work_sessions(project_id, status, updated_at desc);
create index work_sessions_slot_fk_idx
  on agent_private.work_sessions(project_id, profile_key, slot);
create index work_bindings_project_issue_idx
  on agent_private.work_bindings(project_id, issue_number, active);
create index work_bindings_session_project_idx
  on agent_private.work_bindings(session_id, project_id);
create index work_evidence_session_project_idx
  on agent_private.work_evidence(session_id, project_id);
create index requests_project_idx
  on agent_private.requests(project_id);
create index command_receipts_project_idx
  on agent_private.command_receipts(project_id);
create index command_receipts_session_idx
  on agent_private.command_receipts(session_id)
  where session_id is not null;
create index claims_project_active_lookup
  on agent_private.claims(project_id, kind, value, mode, session_id)
  where active;
create index claims_session_active_lookup
  on agent_private.claims(session_id, kind, mode, value)
  where active;
create index claims_created_request_idx
  on agent_private.claims(created_request_id);
create index claims_released_request_idx
  on agent_private.claims(released_request_id)
  where released_request_id is not null;
create index events_project_created_idx
  on agent_private.events(project_id, created_at desc, event_id desc);
create index events_session_created_idx
  on agent_private.events(session_id, created_at desc, event_id desc)
  where session_id is not null;
create index events_request_idx
  on agent_private.events(request_id);
create index events_receipt_idx
  on agent_private.events(receipt_id);
create index project_slots_profile_idx
  on agent_private.project_slots(profile_key, project_id, slot);

commit;
