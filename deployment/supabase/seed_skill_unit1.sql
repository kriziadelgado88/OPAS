-- =============================================================================
-- Seed the API-318 Unit 1 OPAS skill into Supabase
-- =============================================================================
-- Prereqs:
--   1. schema.sql has been run.
--   2. You (Lucas) have created a profile with role = 'educator' and grabbed
--      your user UUID from auth.users.
--
-- How to use:
--   From the _OPAS root, convert the YAML to JSON:
--       python3 -c "import yaml, json; print(json.dumps(yaml.safe_load(open('skills/api-318-unit-1/skill.opas.yaml'))))" > /tmp/unit1.json
--
--   Then paste the contents of /tmp/unit1.json into the <<<YAML_JSON>>>
--   placeholder below, and paste this whole file into the Supabase SQL Editor.
-- =============================================================================

-- Replace the UUID below with your own educator profile id.
-- Find it with:   select id from profiles where email = 'you@example.com';
\set author_uuid '00000000-0000-0000-0000-000000000000'

insert into skills (id, name, version, status, author_id, yaml, schema_version, published_at)
values (
    'hks.api318.unit1.thinking-probabilistically',
    'Thinking Probabilistically',
    '0.1.0',
    'pilot',                                       -- flip to 'published' after Levy signs off
    :'author_uuid'::uuid,
    '<<<YAML_JSON>>>'::jsonb,                      -- paste /tmp/unit1.json here (keep the cast)
    '0.2',
    now()
)
on conflict (id, version) do update
    set yaml         = excluded.yaml,
        status       = excluded.status,
        updated_at   = now();

-- Verify the insert looks right:
select
    id,
    name,
    version,
    status,
    jsonb_array_length(yaml->'phases') as phase_count,
    jsonb_array_length(yaml->'corpus'->'primary_sources') as corpus_sources,
    yaml->'skill'->>'license' as license
from skills
where id = 'hks.api318.unit1.thinking-probabilistically';
-- Expect: 6 phases, 7 corpus sources, CC BY-NC-SA 4.0
