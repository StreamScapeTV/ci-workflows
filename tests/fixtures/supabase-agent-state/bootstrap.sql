-- Minimal Supabase-compatible role bootstrap for an isolated PostgreSQL cluster.
-- This file is test-only and is never deployed to Supabase.

create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
