-- =============================================================================
-- OPAS PoC  |  Supabase schema migration  v0.1  |  2026-04-20
-- =============================================================================
-- Run once in the Supabase SQL editor (Project → SQL Editor → New query).
-- Idempotent: safe to re-run; uses `if not exists` / `create or replace` throughout.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. EXTENSIONS
-- -----------------------------------------------------------------------------

create extension if not exists "pgcrypto";   -- gen_random_uuid()
create extension if not exists "vector";     -- pgvector (RAG embeddings)
create extension if not exists "pg_trgm";    -- fuzzy text search for corpus admin

-- -----------------------------------------------------------------------------
-- 2. ENUMS
-- -----------------------------------------------------------------------------

do $$ begin
    create type user_role as enum ('learner', 'educator', 'admin');
exception when duplicate_object then null; end $$;

do $$ begin
    create type session_status as enum ('active', 'paused', 'completed', 'abandoned');
exception when duplicate_object then null; end $$;

do $$ begin
    create type skill_status as enum ('draft', 'pilot', 'published', 'deprecated');
exception when duplicate_object then null; end $$;

do $$ begin
    create type mastery_signal as enum (
        'rubric_explanation',
        'bayesian_update_demo',
        'journal_entry',
        'probe_score',
        'self_assessment'
    );
exception when duplicate_object then null; end $$;

do $$ begin
    create type consent_scope as enum (
        'mastery_summaries',
        'probe_aggregates',
        'raw_journal_text',
        'raw_chat_turns',
        'current_phase',
        'skill_id'
    );
exception when duplicate_object then null; end $$;

-- -----------------------------------------------------------------------------
-- 3. PROFILES  (extends Supabase auth.users)
-- -----------------------------------------------------------------------------

create table if not exists profiles (
    id          uuid primary key references auth.users(id) on delete cascade,
    email       text not null unique,
    full_name   text,
    role        user_role not null default 'learner',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- Auto-populate profile on auth.users insert.
create or replace function handle_new_user() returns trigger
    language plpgsql security definer set search_path = public
as $$
begin
    insert into public.profiles (id, email, full_name)
    values (new.id, new.email, coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email, '@', 1)))
    on conflict (id) do nothing;
    return new;
end; $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function handle_new_user();

-- -----------------------------------------------------------------------------
-- 4. SKILLS  (OPAS YAML stored as JSONB; portability is preserved)
-- -----------------------------------------------------------------------------

create table if not exists skills (
    id              text primary key,                    -- e.g. hks.api318.unit1.thinking-probabilistically
    name            text not null,
    version         text not null,                       -- semver
    status          skill_status not null default 'draft',
    author_id       uuid references profiles(id),
    yaml            jsonb not null,                      -- full OPAS skill spec
    schema_version  text not null default '0.2',         -- opas_version from YAML
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    published_at    timestamptz,
    unique (id, version)
);

create index if not exists skills_status_idx on skills(status);
create index if not exists skills_yaml_gin on skills using gin (yaml);

-- -----------------------------------------------------------------------------
-- 5. CORPUS CHUNKS  (RAG — one row per embedded chunk)
-- -----------------------------------------------------------------------------

create table if not exists corpus_chunks (
    id            uuid primary key default gen_random_uuid(),
    skill_id      text not null references skills(id) on delete cascade,
    source_id     text not null,                         -- matches corpus.primary_sources[].id in YAML
    chunk_index   int  not null,
    chunk_text    text not null,
    embedding     vector(1536),                          -- default OpenAI / Cohere dim; adjust if model differs
    metadata      jsonb not null default '{}'::jsonb,    -- { handout, section, page, phase_id, ... }
    created_at    timestamptz not null default now(),
    unique (skill_id, source_id, chunk_index)
);

-- HNSW index for fast cosine similarity. Tune ef_construction / m after load.
create index if not exists corpus_chunks_embedding_idx
    on corpus_chunks using hnsw (embedding vector_cosine_ops);

create index if not exists corpus_chunks_skill_idx on corpus_chunks(skill_id);

-- -----------------------------------------------------------------------------
-- 6. SESSIONS  (one learner's pass through one skill)
-- -----------------------------------------------------------------------------

create table if not exists sessions (
    id            uuid primary key default gen_random_uuid(),
    learner_id    uuid not null references profiles(id) on delete cascade,
    skill_id      text not null references skills(id),
    skill_version text not null,
    status        session_status not null default 'active',
    started_at    timestamptz not null default now(),
    last_activity timestamptz not null default now(),
    completed_at  timestamptz,
    meta          jsonb not null default '{}'::jsonb
);

create index if not exists sessions_learner_idx on sessions(learner_id);
create index if not exists sessions_skill_idx   on sessions(skill_id);

-- -----------------------------------------------------------------------------
-- 7. PHASE STATES  (current phase + advance history per session)
-- -----------------------------------------------------------------------------

create table if not exists phase_states (
    id             uuid primary key default gen_random_uuid(),
    session_id     uuid not null references sessions(id) on delete cascade,
    phase_id       text not null,                        -- matches phases[].id in skill YAML
    entered_at     timestamptz not null default now(),
    advanced_at    timestamptz,
    mastery_score  numeric(4,3),                         -- 0.000–1.000
    signals_hit    mastery_signal[] not null default '{}',
    notes          jsonb not null default '{}'::jsonb,
    unique (session_id, phase_id)
);

create index if not exists phase_states_session_idx on phase_states(session_id);

-- -----------------------------------------------------------------------------
-- 8. EVENTS  (xAPI-shaped event bus; append-only source of truth)
-- -----------------------------------------------------------------------------

create table if not exists events (
    id          bigserial primary key,
    occurred_at timestamptz not null default now(),
    actor_id    uuid references profiles(id),            -- learner or educator
    verb        text not null,                           -- started | attempted | answered | updated | reflected | mastered | graduated | ...
    object_type text not null,                           -- phase | probe | belief | journal | skill
    object_id   text,
    session_id  uuid references sessions(id) on delete cascade,
    skill_id    text references skills(id),
    context     jsonb not null default '{}'::jsonb,
    result      jsonb not null default '{}'::jsonb       -- score, duration, etc.
);

create index if not exists events_session_idx on events(session_id);
create index if not exists events_actor_idx   on events(actor_id);
create index if not exists events_verb_idx    on events(verb);
create index if not exists events_occurred_idx on events(occurred_at desc);

-- -----------------------------------------------------------------------------
-- 9. PROBE ATTEMPTS  (scored answers to skill probes; dashboard read-model source)
-- -----------------------------------------------------------------------------

create table if not exists probe_attempts (
    id           uuid primary key default gen_random_uuid(),
    session_id   uuid not null references sessions(id) on delete cascade,
    phase_id     text not null,
    probe_id     text not null,                          -- matches probe_set[].id in YAML
    response     jsonb not null,                         -- numeric | choice | free text
    score        numeric(4,3),                           -- 0.000–1.000
    scorer       text not null default 'deterministic',  -- deterministic | rubric | llm_judge
    occurred_at  timestamptz not null default now()
);

create index if not exists probe_attempts_session_idx on probe_attempts(session_id);
create index if not exists probe_attempts_probe_idx   on probe_attempts(probe_id);

-- -----------------------------------------------------------------------------
-- 10. JOURNAL ENTRIES  (metacognitive reflections; gated by consent)
-- -----------------------------------------------------------------------------

create table if not exists journal_entries (
    id          uuid primary key default gen_random_uuid(),
    session_id  uuid not null references sessions(id) on delete cascade,
    phase_id    text,                                    -- nullable (unit-wide reflections)
    content     text not null,
    word_count  int generated always as (array_length(regexp_split_to_array(trim(content), '\s+'), 1)) stored,
    created_at  timestamptz not null default now()
);

create index if not exists journal_entries_session_idx on journal_entries(session_id);

-- -----------------------------------------------------------------------------
-- 11. MEMORIES  (Mem0-compatible learner memory store; per-learner, per-skill)
-- -----------------------------------------------------------------------------

create table if not exists memories (
    id           uuid primary key default gen_random_uuid(),
    learner_id   uuid not null references profiles(id) on delete cascade,
    skill_id     text references skills(id) on delete set null,
    kind         text not null,                          -- fact | preference | misconception | goal | ...
    content      text not null,
    embedding    vector(1536),
    strength     numeric(3,2) not null default 1.0,      -- decays over time
    created_at   timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
);

create index if not exists memories_learner_idx   on memories(learner_id);
create index if not exists memories_embedding_idx on memories using hnsw (embedding vector_cosine_ops);

-- -----------------------------------------------------------------------------
-- 12. CONSENT GRANTS  (the consent-graph skeleton)
-- -----------------------------------------------------------------------------

create table if not exists consent_grants (
    id          uuid primary key default gen_random_uuid(),
    grantor_id  uuid not null references profiles(id) on delete cascade,   -- the learner
    grantee_id  uuid not null references profiles(id) on delete cascade,   -- teacher / peer / parent (parent = v2)
    skill_id    text references skills(id),
    scopes      consent_scope[] not null,
    granted_at  timestamptz not null default now(),
    expires_at  timestamptz,
    revoked_at  timestamptz,
    unique (grantor_id, grantee_id, skill_id)
);

create index if not exists consent_grants_grantee_idx on consent_grants(grantee_id);

-- -----------------------------------------------------------------------------
-- 13. DASHBOARD READ-MODEL VIEWS  (materialised aggregates for teachers)
-- -----------------------------------------------------------------------------
-- These are the ONLY surfaces a teacher sees unless the learner elevates consent
-- to raw_journal_text or raw_chat_turns. They compute from events/probe_attempts.

create or replace view v_learner_phase_progress as
    select
        s.learner_id,
        s.skill_id,
        ps.phase_id,
        ps.mastery_score,
        ps.advanced_at is not null as advanced,
        ps.entered_at,
        ps.advanced_at
    from phase_states ps
    join sessions s on s.id = ps.session_id;

create or replace view v_skill_probe_aggregates as
    select
        s.skill_id,
        pa.phase_id,
        pa.probe_id,
        count(*)                     as attempts,
        count(distinct s.learner_id) as unique_learners,
        avg(pa.score)                as avg_score,
        percentile_cont(0.5) within group (order by pa.score) as median_score
    from probe_attempts pa
    join sessions s on s.id = pa.session_id
    group by s.skill_id, pa.phase_id, pa.probe_id;

-- =============================================================================
-- ROW-LEVEL SECURITY  (the consent-graph skeleton, enforced in Postgres)
-- =============================================================================

-- Turn on RLS for every non-public table.
alter table profiles         enable row level security;
alter table skills           enable row level security;
alter table corpus_chunks    enable row level security;
alter table sessions         enable row level security;
alter table phase_states     enable row level security;
alter table events           enable row level security;
alter table probe_attempts   enable row level security;
alter table journal_entries  enable row level security;
alter table memories         enable row level security;
alter table consent_grants   enable row level security;

-- Helper: is the current user an admin?
create or replace function is_admin() returns boolean
    language sql stable security definer as $$
    select exists(select 1 from profiles where id = auth.uid() and role = 'admin');
$$;

-- Helper: is the current user an educator?
create or replace function is_educator() returns boolean
    language sql stable security definer as $$
    select exists(select 1 from profiles where id = auth.uid() and role in ('educator', 'admin'));
$$;

-- Helper: does this educator have consent to see learner's data on this skill?
create or replace function has_consent(target_learner uuid, target_skill text, needed_scope consent_scope)
    returns boolean language sql stable security definer as $$
    select exists(
        select 1 from consent_grants
        where grantor_id = target_learner
          and grantee_id = auth.uid()
          and (skill_id = target_skill or skill_id is null)
          and needed_scope = any(scopes)
          and revoked_at is null
          and (expires_at is null or expires_at > now())
    );
$$;

-- --- profiles ---------------------------------------------------------------
drop policy if exists profiles_self_read on profiles;
create policy profiles_self_read on profiles for select
    using (id = auth.uid() or is_educator());

drop policy if exists profiles_self_update on profiles;
create policy profiles_self_update on profiles for update
    using (id = auth.uid()) with check (id = auth.uid());

-- --- skills -----------------------------------------------------------------
drop policy if exists skills_published_read on skills;
create policy skills_published_read on skills for select
    using (status = 'published' or author_id = auth.uid() or is_educator());

drop policy if exists skills_author_write on skills;
create policy skills_author_write on skills for all
    using (author_id = auth.uid() or is_admin())
    with check (author_id = auth.uid() or is_admin());

-- --- corpus_chunks (readable by anyone who can read the skill) --------------
drop policy if exists corpus_chunks_read on corpus_chunks;
create policy corpus_chunks_read on corpus_chunks for select
    using (exists(select 1 from skills s where s.id = corpus_chunks.skill_id
           and (s.status = 'published' or s.author_id = auth.uid() or is_educator())));

-- --- sessions: learner sees own; educator sees only with consent ------------
drop policy if exists sessions_learner_read on sessions;
create policy sessions_learner_read on sessions for select
    using (learner_id = auth.uid()
           or has_consent(learner_id, skill_id, 'mastery_summaries')
           or is_admin());

drop policy if exists sessions_learner_write on sessions;
create policy sessions_learner_write on sessions for all
    using (learner_id = auth.uid())
    with check (learner_id = auth.uid());

-- --- phase_states: same pattern --------------------------------------------
drop policy if exists phase_states_read on phase_states;
create policy phase_states_read on phase_states for select
    using (exists(select 1 from sessions s where s.id = phase_states.session_id
           and (s.learner_id = auth.uid()
                or has_consent(s.learner_id, s.skill_id, 'mastery_summaries')
                or is_admin())));

drop policy if exists phase_states_write on phase_states;
create policy phase_states_write on phase_states for all
    using (exists(select 1 from sessions s where s.id = phase_states.session_id and s.learner_id = auth.uid()))
    with check (exists(select 1 from sessions s where s.id = phase_states.session_id and s.learner_id = auth.uid()));

-- --- events: learner sees own; educator sees aggregates only ----------------
drop policy if exists events_read on events;
create policy events_read on events for select
    using (actor_id = auth.uid()
           or (is_educator() and session_id is not null and exists(
                select 1 from sessions s where s.id = events.session_id
                and has_consent(s.learner_id, s.skill_id, 'probe_aggregates')))
           or is_admin());

drop policy if exists events_insert on events;
create policy events_insert on events for insert
    with check (actor_id = auth.uid() or is_admin());

-- --- probe_attempts: learner own + educator with probe_aggregates consent ---
drop policy if exists probe_attempts_read on probe_attempts;
create policy probe_attempts_read on probe_attempts for select
    using (exists(select 1 from sessions s where s.id = probe_attempts.session_id
           and (s.learner_id = auth.uid()
                or (is_educator() and has_consent(s.learner_id, s.skill_id, 'probe_aggregates'))
                or is_admin())));

drop policy if exists probe_attempts_write on probe_attempts;
create policy probe_attempts_write on probe_attempts for all
    using (exists(select 1 from sessions s where s.id = probe_attempts.session_id and s.learner_id = auth.uid()))
    with check (exists(select 1 from sessions s where s.id = probe_attempts.session_id and s.learner_id = auth.uid()));

-- --- journal_entries: STRICT — learner-only unless raw_journal_text consent -
drop policy if exists journal_read on journal_entries;
create policy journal_read on journal_entries for select
    using (exists(select 1 from sessions s where s.id = journal_entries.session_id
           and (s.learner_id = auth.uid()
                or (is_educator() and has_consent(s.learner_id, s.skill_id, 'raw_journal_text'))
                or is_admin())));

drop policy if exists journal_write on journal_entries;
create policy journal_write on journal_entries for all
    using (exists(select 1 from sessions s where s.id = journal_entries.session_id and s.learner_id = auth.uid()))
    with check (exists(select 1 from sessions s where s.id = journal_entries.session_id and s.learner_id = auth.uid()));

-- --- memories: strict learner-only (no educator path at all in v0.1) --------
drop policy if exists memories_owner on memories;
create policy memories_owner on memories for all
    using (learner_id = auth.uid() or is_admin())
    with check (learner_id = auth.uid() or is_admin());

-- --- consent_grants: grantor and grantee can both read; only grantor writes -
drop policy if exists consent_grants_read on consent_grants;
create policy consent_grants_read on consent_grants for select
    using (grantor_id = auth.uid() or grantee_id = auth.uid() or is_admin());

drop policy if exists consent_grants_write on consent_grants;
create policy consent_grants_write on consent_grants for all
    using (grantor_id = auth.uid() or is_admin())
    with check (grantor_id = auth.uid() or is_admin());

-- =============================================================================
-- AGENT RUNTIME RPCs  (added Phase C, 2026-04-21)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- match_corpus_chunks: pgvector cosine similarity search
-- ---------------------------------------------------------------------------
create or replace function match_corpus_chunks(
  query_embedding vector(1536),
  p_skill_id      text,
  match_threshold float,
  match_count     int
)
returns table (
  id           uuid,
  source_id    text,
  chunk_index  int,
  chunk_text   text,
  similarity   float,
  metadata     jsonb
)
language sql stable
security invoker
set search_path = public
as $$
  select id, source_id, chunk_index, chunk_text,
         1 - (embedding <=> query_embedding) as similarity,
         metadata
  from corpus_chunks
  where skill_id = p_skill_id
    and 1 - (embedding <=> query_embedding) > match_threshold
  order by embedding <=> query_embedding
  limit match_count;
$$;

-- ---------------------------------------------------------------------------
-- init_session: atomically create sessions + first phase_states row
-- ---------------------------------------------------------------------------
create or replace function init_session(
  p_learner_id    uuid,
  p_skill_id      text,
  p_skill_version text,
  p_phase_id      text
)
returns uuid
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_session_id uuid;
begin
  insert into sessions (learner_id, skill_id, skill_version, status, started_at)
  values (p_learner_id, p_skill_id, p_skill_version, 'active', now())
  returning id into v_session_id;

  insert into phase_states (session_id, phase_id, entered_at)
  values (v_session_id, p_phase_id, now());

  return v_session_id;
end;
$$;

-- =============================================================================
-- SUPABASE STORAGE: corpus bucket RLS  (added Phase C, 2026-04-22)
-- =============================================================================
-- Run after creating the "corpus" bucket in Supabase Storage dashboard.
--
-- ATOMICITY NOTE (for wizard implementation):
--   Upload to Storage first, then write storage_path + content_hash into the
--   skill YAML. Reverse order is worse: the YAML would reference a path that
--   doesn't exist yet. Accept the orphan-file risk for Demo Day; a weekly cron
--   can sweep storage objects with no matching YAML entry post-launch.
--
-- FILE SIZE NOTE:
--   Supabase Storage default cap is 50 MB per object. Enforce a pre-upload
--   size check in the wizard (warn at 40 MB, hard-block at 50 MB), or raise
--   the bucket limit in Supabase Storage dashboard settings.
--
-- NICE-TO-HAVE (not required for Demo Day):
--   Before uploading, the wizard can query:
--     select 1 from corpus_chunks
--     where metadata->>'content_hash' = '<client_computed_hash>' limit 1
--   If a row exists, skip both the upload and the ingest trigger entirely.
-- =============================================================================

-- Teachers may insert objects into the corpus bucket only under skill_ids
-- they own (skills.author_id = auth.uid()).
drop policy if exists "teachers upload own skill corpus" on storage.objects;
create policy "teachers upload own skill corpus"
  on storage.objects for insert
  to authenticated
  with check (
    bucket_id = 'corpus'
    and (storage.foldername(name))[1] in (
      select id from public.skills where author_id = auth.uid()
    )
  );

-- Allow teachers to overwrite (re-upload corrected files) under the same path.
drop policy if exists "teachers update own skill corpus" on storage.objects;
create policy "teachers update own skill corpus"
  on storage.objects for update
  to authenticated
  using (
    bucket_id = 'corpus'
    and (storage.foldername(name))[1] in (
      select id from public.skills where author_id = auth.uid()
    )
  );

-- Learners and the agent (service_role bypasses RLS) can read corpus objects.
drop policy if exists "authenticated read corpus" on storage.objects;
create policy "authenticated read corpus"
  on storage.objects for select
  to authenticated
  using (bucket_id = 'corpus');

-- =============================================================================
-- END OF MIGRATION
-- =============================================================================
-- Verification queries (uncomment to spot-check after running):
--   select table_name from information_schema.tables where table_schema='public' order by 1;
--   select * from pg_policies where schemaname='public' order by tablename, policyname;
--   select extname from pg_extension where extname in ('vector','pgcrypto','pg_trgm');
--   select policyname from pg_policies where tablename = 'objects' and schemaname = 'storage';
-- =============================================================================
