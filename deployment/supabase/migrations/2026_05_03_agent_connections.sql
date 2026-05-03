-- Agent connections — V1 of the A2A (agent-to-agent) feature.
-- Lets two learners' agents form a mutual handshake within a shared group.
-- Drafted 2026-05-03. Apply via Supabase SQL Editor.
--
-- Mutual handshake model: requester creates a row with status='pending';
-- recipient flips it to 'accepted' (or 'declined'). Either party can revoke
-- a connection by deleting the row.

create table if not exists agent_connections (
  id uuid primary key default gen_random_uuid(),

  -- The two skills/agents being connected
  requester_skill_id text not null references skills(id) on delete cascade,
  recipient_skill_id text not null references skills(id) on delete cascade,

  -- The two learners who own those agents
  requester_learner_id uuid not null references learner_accounts(id) on delete cascade,
  recipient_learner_id uuid not null references learner_accounts(id) on delete cascade,

  -- Lifecycle: pending → accepted | declined.
  -- Declined rows are retained briefly (privacy: don't reveal repeated declines)
  -- but UI hides them. Hard-delete on connection revocation.
  status text not null default 'pending'
    check (status in ('pending', 'accepted', 'declined')),

  -- Where the connection was made — usually the shared group_id; nullable
  -- so future cross-group connections are possible without schema change.
  group_id uuid,

  created_at  timestamptz not null default now(),
  responded_at timestamptz,

  -- Prevent duplicate connection requests (regardless of who requested first).
  -- Two rows for skill_a/skill_b with reversed roles would be a duplicate;
  -- application code de-duplicates on (least, greatest) of the two skill ids.
  unique (requester_skill_id, recipient_skill_id),

  -- Sanity: a skill can't connect to itself
  check (requester_skill_id <> recipient_skill_id)
);

-- Indexes for the hot queries:
--   "find all connections for a given skill" (used by the agent card badges)
create index if not exists agent_connections_requester_idx
  on agent_connections (requester_skill_id);
create index if not exists agent_connections_recipient_idx
  on agent_connections (recipient_skill_id);

-- "find pending requests sent TO this learner"
create index if not exists agent_connections_recipient_learner_pending_idx
  on agent_connections (recipient_learner_id, status)
  where status = 'pending';

-- RLS: keep policies permissive for now (gated by API auth at the router level).
-- Tighten later if learners ever get direct supabase access.
alter table agent_connections enable row level security;

-- Allow service-role full access (the agent backend uses this).
drop policy if exists agent_connections_service_role_all on agent_connections;
create policy agent_connections_service_role_all on agent_connections
  for all to service_role using (true) with check (true);
