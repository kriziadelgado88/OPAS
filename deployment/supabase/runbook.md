# Supabase provisioning runbook — Day-0

Estimated time: 25 minutes. You (Lucas) do the browser clicks; the SQL is pre-written.

## Prereqs

- GitHub account to log into Supabase with (recommended — easy SSO).
- Decide the project name and region *before* creating: region should be closest to pilot users (likely `us-east-1` for HKS; pick `eu-central-1` if any EU pilot students are expected).
- Have a password manager open for the database password.

---

## Step 1 — Create the project (5 min)

1. Go to https://supabase.com → Sign in with GitHub.
2. New project:
   - **Organisation:** pick your personal org (we can migrate to a team org later).
   - **Name:** `opas-poc`.
   - **Database password:** generate a strong one in 1Password, save it. You cannot retrieve this later — only reset it.
   - **Region:** `us-east-1` (Virginia).
   - **Plan:** Free tier is fine for PoC; upgrade to Pro only if we hit the 500MB / 2GB egress ceiling during the pilot.
3. Wait ~2 min for provisioning.

## Step 2 — Copy credentials (2 min)

In project dashboard → **Project Settings → API**, copy these four values into your `.env` (see `.env.template` in this folder):

- `SUPABASE_URL` → the project URL (e.g. `https://xxx.supabase.co`)
- `SUPABASE_ANON_KEY` → the public anon key (safe to ship to the browser)
- `SUPABASE_SERVICE_ROLE_KEY` → the service-role key (**NEVER** ship to browser; server-only)
- `SUPABASE_DB_PASSWORD` → the password you generated in Step 1

## Step 3 — Run the schema migration (5 min)

1. In Supabase dashboard → **SQL Editor** → **New query**.
2. Open `schema.sql` from this folder, paste the entire contents in.
3. Click **Run**. Expect: "Success. No rows returned."
4. If it errors on the `vector` extension, go to **Database → Extensions**, search "vector", enable `pgvector` manually, then re-run only the parts of the migration after the `CREATE EXTENSION` block.

## Step 4 — Seed the Unit 1 skill (3 min)

1. Before running the seed, export the skill YAML to JSON:

   ```bash
   # From the _OPAS root:
   python3 -c "import yaml, json, sys; print(json.dumps(yaml.safe_load(open('skills/api-318-unit-1/skill.opas.yaml'))))" > /tmp/unit1.json
   ```

2. Open `seed_skill_unit1.sql`, paste the contents of `/tmp/unit1.json` into the single placeholder, and run the resulting SQL in the Supabase SQL Editor.

3. Verify:
   ```sql
   select id, name, version, jsonb_array_length(yaml->'phases') as phase_count from skills;
   -- Expect: hks.api318.unit1.thinking-probabilistically | Thinking Probabilistically | 0.1.0 | 6
   ```

## Step 5 — Create your first test user (3 min)

1. **Authentication → Users → Add user → Send invite**. Use your own email.
2. In **Authentication → Providers**, confirm "Email" is enabled. Disable magic-link in production; for Demo Day keep it enabled (simpler for pilot students).
3. In SQL Editor, promote yourself to the educator role:
   ```sql
   update profiles set role = 'educator' where email = 'you@example.com';
   ```

## Step 6 — Verify RLS is actually enforcing (5 min)

This is the *real* Day-0 test: are the consent-graph policies working?

1. In SQL Editor, run as the anon key:
   ```sql
   -- Should return zero rows even though we just seeded a skill,
   -- because anon users can't list published skills without being authed.
   -- (If this returns a row, RLS is off; do not proceed.)
   set request.jwt.claim.role = 'anon';
   select count(*) from skills;
   ```
2. Run as an authed learner:
   ```sql
   set request.jwt.claim.sub = '<your-user-uuid>';
   set request.jwt.claim.role = 'authenticated';
   select count(*) from skills where status = 'published';
   -- Expect: 1 once the Unit 1 skill is marked published.
   ```

## Step 7 — Share credentials with Dima (2 min)

In a password-manager shared vault (not Slack), share:
- The `.env` block from Step 2.
- The Supabase dashboard URL.
- Read-only access to this project (Supabase dashboard → **Team → Invite member → role: Developer**).

That's Day-0 for Supabase. You'll come back once the FastAPI backend (workstream 1) is wired to connect.
