# Local Testing Setup

Walkthrough for running OPAS end-to-end on your own machine. For the high-level Quickstart, see [`README.md`](README.md). This file is the longer-form version that catches the edge cases.

**Prerequisites:** Python 3.11+, git, a Supabase project, an Anthropic API key.

---

## 1. Clone & install

```bash
git clone https://github.com/<your-org>/opas.git
cd opas/agent

python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

## 2. Provision Supabase

Follow [`deployment/supabase/runbook.md`](deployment/supabase/runbook.md). It walks through creating the project, copying credentials, applying `schema.sql`, and seeding the reference skill (`seed_skill_unit1.sql`).

When you're done you should have four values to put in your `.env`:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_DB_PASSWORD`

## 3. Configure environment

```bash
cp .env.example .env
# Edit .env with the four Supabase values + your Anthropic key
```

The minimum viable .env for a local run:

```
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<from Supabase dashboard>
ANTHROPIC_API_KEY=<from console.anthropic.com>
OPAS_ENV=dev
OPAS_DEV_BEARER_TOKEN=dev-token-change-me
CLAUDE_MODEL=claude-sonnet-4-6
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
```

`.env` is gitignored. Never commit it.

## 4. Mint a learner token

```bash
# from agent/
python scripts/mint_learner_tokens.py --email you@example.com
```

The script prints a token. Copy it.

## 5. Start the backend

```bash
# from agent/
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Confirm it's up: http://localhost:8001/health should return `{"env":"dev",...}`.

## 6. Start the frontend

In a second terminal:

```bash
# from app/
python -m http.server 8080
```

## 7. Open the demo

The student client expects a `token=` query param the first time:

```
http://localhost:8080/opas-student.html?skill_id=hks.api318.unit1.thinking-probabilistically&token=<your-token>
```

The token is cached to localStorage so subsequent loads don't need it.

**Demo mode** (live YAML highlights — useful when showing how the protocol works):

```
http://localhost:8080/opas-student.html?skill_id=hks.api318.unit1.thinking-probabilistically&demo=1
```

**Wizard** (skill authoring): http://localhost:8080/opas-wizard.html

---

## What to test

- [ ] Session starts with a Socratic question (not a lecture)
- [ ] In demo mode: YAML pane populates on load; lines highlight after each turn
- [ ] Citations appear in agent replies (`[source, §section]` format)
- [ ] Off-topic questions get a polite refusal (the `refuse_if_ungrounded` rule fires)
- [ ] "Just give me the answer" — agent stays Socratic
- [ ] End session button works and writes session-end events to `xapi_events` in Supabase
- [ ] Wizard can author a new skill and publish it (you should see it appear in `skills` table with `status='published'`)

---

## Troubleshooting

**"Missing required env var: SUPABASE_URL"** — your `.env` isn't being read. Confirm it's at `agent/.env` (not `agent/app/.env`).

**Health check returns 500** — usually means an env var is set but invalid. Check the uvicorn console for the specific error.

**Wizard can save drafts but publishing fails** — the row-level-security policies require an authenticated educator role. Make sure you ran the educator promotion step in the Supabase runbook.

**Student page returns 401** — your bearer token is missing or expired. Mint a new one with `mint_learner_tokens.py`.

**Agent replies are slow / time out** — Anthropic API calls can take 5-30 seconds for long replies. If they're consistently timing out, check your API key is valid and your account has credit.
