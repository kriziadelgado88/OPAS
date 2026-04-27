-- =============================================================================
-- Step 6 — RLS enforcement test (v4 — results via SELECT from function)
-- Paste into Supabase SQL Editor → Run.
-- The final SELECT returns a 3-row table in the Results pane.
-- =============================================================================

create or replace function pg_temp.rls_test_skills()
returns table(
    test        text,
    skills_seen int,
    expected    int,
    result      text
)
language plpgsql
as $$
declare
    lucas_uuid uuid;
    t int;
begin
    select id into lucas_uuid from auth.users
     where email = 'lucas.kuziv@gmail.com';

    -- ---- Test 1: anon --------------------------------------------------
    perform set_config('role', 'anon', true);
    perform set_config('request.jwt.claims', '{"role":"anon"}', true);
    execute 'select count(*) from skills' into t;
    reset role;
    test := 'TEST 1: anon';
    skills_seen := t; expected := 0;
    result := case when t = 0 then 'PASS' else 'FAIL' end;
    return next;

    -- ---- Test 2: random authenticated learner --------------------------
    perform set_config('role', 'authenticated', true);
    perform set_config(
        'request.jwt.claims',
        '{"role":"authenticated","sub":"11111111-1111-1111-1111-111111111111"}',
        true
    );
    execute 'select count(*) from skills' into t;
    reset role;
    test := 'TEST 2: random learner';
    skills_seen := t; expected := 0;
    result := case when t = 0 then 'PASS' else 'FAIL' end;
    return next;

    -- ---- Test 3: Lucas as educator+author ------------------------------
    perform set_config('role', 'authenticated', true);
    perform set_config(
        'request.jwt.claims',
        json_build_object('role','authenticated','sub', lucas_uuid)::text,
        true
    );
    execute 'select count(*) from skills' into t;
    reset role;
    test := 'TEST 3: Lucas educator';
    skills_seen := t; expected := 1;
    result := case when t = 1 then 'PASS' else 'FAIL' end;
    return next;
end $$;

select * from pg_temp.rls_test_skills();
