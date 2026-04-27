# Supabase setup runbook

End-to-end walkthrough for getting a Supabase project ready to run OPAS locally. Estimated time: 15–25 minutes. The SQL files in this folder are pre-written; you'll paste them into Supabase's SQL Editor.

## Prerequisites

- A free [Supabase](https://supabase.com) account (GitHub SSO works)
- A password manager open to record the database password
- This repo cloned locally with the YAML reference skill at `skills/api-318-unit-1/skill.opas.yaml`

---

## Step 1 — Create the project (~5 min)

1. Go to [supabase.com](https://supabase.com) and sign in.
2. **New project**:
   - **Organisation:** your personal org is fine for a local dev setup.
   - **Name:** `opas-dev` (or anything you prefer).
   - **Database password:** generate a strong one and save it — you can't retrieve it later, only reset it.
   - **Region:** pick the one closest to your users. `us-east-1` (Virginia) is a safe default.
   - **Plan:** Free tier is fine for development. Upgrade to Pro if you hit the 500MB / 2GB egress ceiling.
3. Wait ~2 minutes for provisioning.

## Step 2 — Copy credentials (~2 min)

In your project dashboard → **Project Settings → API**, copy these four values into `agent/.env` (use `agent/.env.example` as the template):

- `SUPABASE_URL` — the project URL (e.g. `https://abcd1234.supabase.co`)
- `SUPABASE_ANON_KEY` — public anon key (safe to ship to a browser)
- `SUPABASE_SERVICE_ROLE_KEY` — server-only; **never** ship to the browser
- `SUPABASE_DB_PASSWORD` — the password you generated in Step 1

## Step 3 — Apply the schema (~5 min)

1. In Supabase dashboard → **SQL Editor → New query**.
2. Open `schema.sql` from this folder, paste the entire contents in.
3. Click **Run**. Expected output: "Success. No rows returned."
4. If it errors on the `vector` extension, go to **Database → Extensions**, search "vector", enable `pgvector` manually, then re-run only the parts of the migration after the `CREATE EXTENSION` block.

## Step 4 — Seed the reference skill (~3 min)

1. From the repo root, export the skill YAML to JSON:

   ```bash
   python3 -c "import yaml, json; print(json.dumps(yaml.safe_load(open('skills/api-318-unit-1/skill.opas.yaml'))))" > /tmp/unit1.json
   ```

2. Open `seed_skill_unit1.sql` in this folder. Paste the contents of `/tmp/unit1.json` into the single placeholder in the SQL.
3. Run the resulting SQL in the Supabase SQL Editor.
4. Verify:

   ```sql
   select id, name, version, jsonb_array_length(yaml->'phases') as phase_count from skills;
   -- Expect: hks.api318.unit1.thinking-probabilistically | Thinking Probabilistically | 0.1.0 | 6
   ```

## Step 5 — Create your first user (~3 min)

1. **Authentication → Users → Add user → Send invite**. Use your own email.
2. In **Authentication → Providers**, confirm "Email" is enabled.
3. In SQL Editor, promote yourself to the educator role so you can use the wizard:

   ```sql
   update profiles set role = 'educator' where email = 'you@example.com';
   ```

## Step 6 — Verify RLS is enforcing (~5 min)

This is the real Day-0 test: are the row-level-security policies on?

```sql
-- As an anonymous client, you should NOT see published skills
-- without being authenticated. If this returns rows, RLS is off.
set request.jwt.claim.role = 'anon';
select count(*) from skills;
-- Expect: 0
```

```sql
-- As an authenticated learner, you SHOULD see the seeded skill.
set request.jwt.claim.sub = '<your-user-uuid>';
set request.jwt.claim.role = 'authenticated';
select count(*) from skills where status = 'published';
-- Expect: 1
```

If either check is wrong, double-check that `schema.sql` ran cleanly — RLS policies are at the bottom of that file.

---

## What's in this folder

| file | purpose |
| --- | --- |
| `schema.sql` | Full database schema — tables, indexes, RLS policies, functions. Idempotent; safe to re-run. |
| `seed_skill_unit1.sql` | Inserts the reference skill (`hks.api318.unit1.thinking-probabilistically`) into the `skills` table. Run after `schema.sql`. |
| `seed_step4_5_combined.sql` | Optional: additional fixture data for testing the wizard's draft/publish flow. |
| `step6_rls_enforcement_test.sql` | Standalone RLS smoke tests. Run this if you suspect RLS isn't working. |
| `_unit1.json` | A pre-rendered JSON dump of the Unit 1 skill, in case you don't want to convert YAML → JSON yourself in Step 4. |
| `.env.template` | Template for the deployment env (separate from `agent/.env.example` — this one is for the Supabase admin context). |

---

## When to come back

You'll touch this runbook again if:

- You want to **deploy to a fresh Supabase project** (new region, new tier, new account)
- You want to **re-seed** with a different reference skill
- You're **debugging RLS** in production and need to re-run Step 6 against the live DB

For the application code's setup, see [`/TESTING.md`](../../TESTING.md).
