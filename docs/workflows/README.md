# Workflow documentation

Public reusable workflow APIs are generated and validated through their checked-in contracts.

Agent State is intentionally absent. Ordinary coordination uses the approved direct `agent_api.*` Supabase RPC contract owned by `StreamScapeTV/agent-state-supabase`; the retired compatibility workflow is not a supported fallback.

Consumer repositories must reference only `StreamScapeTV/organization-rules@main/AGENTS.md` for shared operating policy; they do not link this implementation documentation as their policy entry point.
