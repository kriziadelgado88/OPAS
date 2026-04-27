# Architecture

How the pieces fit together. This doc is for developers who want to understand or modify the OPAS runtime.

```
┌─────────────────────┐         ┌──────────────────────────┐         ┌─────────────────┐
│  Frontend (static)  │  HTTP   │  Agent runtime (FastAPI) │  HTTPS  │  LLM provider   │
│  app/*.html         │────────▶│  agent/app/              │────────▶│  Claude / GPT   │
│   - student         │  /sess  │   - prompt_assembler     │         │   / Gemini      │
│   - wizard          │         │   - model_adapter        │         └─────────────────┘
│   - skills          │         │   - constitutions/       │
└─────────────────────┘         │     enforcer (the Soul)  │
           │                    │   - routers/             │         ┌─────────────────┐
           │                    └──────────┬───────────────┘  ↕      │  Supabase       │
           │                               │  service-role key  ────▶│   - skills      │
           │                               │  + RLS-on JWTs           │   - sessions    │
           └─ learner JWT ─────────────────┘                          │   - xAPI events │
                                                                      │   - profiles    │
                                                                      └─────────────────┘
```

---

## The three layers

### Frontend

Static HTML + vanilla JS, served by any web server. No build step.

- `app/opas-student.html` — learner client. Token gate, chat pane, optional YAML demo pane.
- `app/opas-wizard.html` — skill authoring tool for educators. Tailwind via CDN.
- `app/opas-skills.html` — catalogue of published skills.
- `app/opas-dashboard.html` — read-only teacher dashboard (signals across a class).
- `app/opas-onboarding.html` — first-run learner profile setup.

The frontend talks to the agent runtime over HTTP. No direct database access from the browser; everything is gated through the runtime so that row-level-security and the constitution enforcer get a chance to run.

### Agent runtime

`agent/app/` — a FastAPI service. The two files that do the real work:

**`prompt_assembler.py`** assembles the system prompt for each model call. It composes 12 sections, in this order:

1. Learner profile (language, interests, bandwidth)
2. Mode directive (teach / review / auto)
3. Instructional model (from skill YAML)
4. Persona — voice + register
5. Forbidden moves (hard negatives)
6. Disallowed phrases
7. Current phase objectives
8. Personalization rules (hard-locked vs allowed-to-adapt)
9. Citation requirement
10. Session-opening instruction (for `is_session_start=True`)
11. Prior session memories
12. Grounded corpus chunks
13. Probe elicitation
14. Time budget
15. Constitution injection (from `minerva.soul.v1.yaml`)

Each section is data-driven from the skill YAML. The assembler returns the prompt text plus a list of YAML reference paths the prompt drew from — used by the wizard's demo mode to show which YAML lines shaped each turn.

**`constitutions/enforcer.py`** (the Soul) is the constitutional safety layer. Every learner message goes through `scan_learner()` before reaching the model; every model reply goes through `scan_agent()` before reaching the learner. Pattern matches inject system-level wellbeing instructions or block bad replies. See [`/soul/README.md`](../soul/README.md) for the full grammar.

**`model_adapter.py`** is the LLM-swap point. One method, `chat()`, returns a string. Implementations for Claude (default), OpenAI, and Gemini. Adding a new provider is ~50 lines.

**Routers** in `agent/app/routers/`:

| route | purpose | auth |
| --- | --- | --- |
| `/session/start`, `/session/turn`, `/session/end` | the agent loop | learner token |
| `/session/compare` | run a turn against multiple models side-by-side | dev bearer (teacher) |
| `/admin/dashboard/*` | read-only teacher analytics | dev bearer |
| `/auth/*` | magic-link signup + callback | none (this is the auth) |
| `/groups/*` | study group management | learner token |
| `/pedagogies` | public catalogue | none |
| `/me/*` | learner profile + prefs | learner token |
| `/skills/*` | self-serve skill generation + lifecycle | learner token |

The session loop is the heart of the runtime: receive learner message → enforcer scan → assemble prompt → call model → enforcer scan → return reply. Errors in any step are surfaced cleanly to the frontend with retry guidance.

### Storage

Supabase (PostgreSQL + pgvector + Auth). Schema is in `deployment/supabase/schema.sql`. The important tables:

| table | what it holds |
| --- | --- |
| `skills` | one row per skill YAML; `status` is `draft` / `published` / `archived` |
| `sessions` | one row per learner session; tracks current phase, time, mode |
| `xapi_events` | every learner turn + agent reply, for analytics |
| `corpus_chunks` | embedded source material; queried per turn for grounding |
| `profiles` | learner preferences (language, interests, bandwidth) |
| `study_groups` | optional cohort grouping for class-level dashboards |

Row-level security is on by default. The runtime uses the service-role key server-side; the browser only ever sees a learner JWT. RLS policies enforce that learners can only read their own profile + sessions, and only published skills.

---

## Data flow for one turn

```
Learner types  →  POST /session/turn
                  ├─ enforcer.scan_learner(msg)
                  │   └─ if distress/harm pattern: return wellbeing protocol response,
                  │      do NOT call the model
                  ├─ retrieve corpus chunks (top-k via pgvector cosine)
                  ├─ load skill YAML, current phase, prior memories
                  ├─ build_system_prompt(skill, phase, chunks, ...)
                  ├─ model_adapter.chat(prompt, conversation_history)
                  ├─ enforcer.scan_agent(reply, learner_msg)
                  │   └─ if rule violation: rewrite or block
                  ├─ check probes — did the learner answer one correctly?
                  │   └─ if yes: advance phase
                  ├─ write xAPI event to Supabase
                  └─ return reply to frontend
```

The whole loop is roughly 200 lines in `agent/app/routers/session.py`. Most of it is plumbing; the substantive work happens in `prompt_assembler.py` and `enforcer.py`.

---

## Why the boundaries are where they are

**Why the skill is YAML (data) rather than code.** Code requires a deploy. YAML can be authored by an educator in the wizard, saved to Supabase, and run immediately. It also means the skill survives prompt-engineering churn — the prompt assembler can change its formatting without breaking any existing skill.

**Why the model adapter is a single method.** Different providers have different SDK shapes. One method (`chat(prompt, history) -> str`) is the smallest portable interface that works for all of them. Streaming, tool use, etc. are not in v1.0 — they'd live in a separate `chat_stream()` method when needed.

**Why the enforcer is regex, not an LLM judge.** Determinism. See [`/soul/README.md`](../soul/README.md) for the full case.

**Why frontend → runtime → Supabase, not frontend → Supabase directly.** Two reasons. First, the constitution enforcer needs to see every learner message; if the browser writes directly to Supabase, the enforcer is bypassed. Second, the runtime emits xAPI events on every turn — that's the analytics substrate. We need a single chokepoint.

---

## What's swappable

- **Model provider** — Claude is default; swap via `model_adapter.py`.
- **Database** — Supabase is convenient (auth + RLS + pgvector in one). Replacing with vanilla Postgres + a separate auth provider is straightforward; the schema is plain SQL.
- **Frontend** — vanilla static HTML. Swap for a React/Vue/Svelte app if you prefer; the runtime API is just HTTP/JSON.
- **Constitution** — `minerva.soul.v1.yaml` is one constitution. You can ship your own (different rules, different patterns, different interventions) by changing the YAML and pointing skills at the new id.

---

## What's NOT swappable (deliberate)

- The skill YAML schema — v1.0 frozen. Future versions are additive.
- The 12-section prompt assembler structure — sections may be added (additive) but not reordered.
- The enforcer's two scan points (learner-side + agent-side) — both are required.

---

## Where to look first when changing something

| if you want to... | start here |
| --- | --- |
| Add a new pedagogy template | `agent/app/routers/pedagogies.py` |
| Add a new LLM provider | `agent/app/model_adapter.py` |
| Change how prompts get assembled | `agent/app/prompt_assembler.py` |
| Add a new Soul pattern | `agent/constitutions/minerva.soul.v1.yaml` |
| Change how the wizard authors skills | `app/opas-wizard.html` (single file) |
| Modify the database schema | `deployment/supabase/schema.sql` |
| Add a new analytics event | `agent/app/event_emitter.py` |
