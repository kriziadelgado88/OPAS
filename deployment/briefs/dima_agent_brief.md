# Student Agent - brief (Days 4–6)

**Goal:** Build the OPAS *runtime* — the thing that reads a skill YAML out of Supabase, runs RAG over its corpus, and tutors a student end-to-end on one phase. Clean path on Claude first; the 3-model adapter comes Days 7–9.

**Why this matters:** Mentor review (Apr 21): the audience must see (a) a teacher-authored YAML (Krizia's wizard, Days 1–3), (b) that same YAML running the agent, and (c) the same agent running on 3 models. You own (b). Without it, (a) and (c) have nothing to connect.

---

## The one thing to internalise before coding

The agent is **not a chatbot with a system prompt**. The agent is a runtime that executes an OPAS skill. Everything pedagogical lives in the YAML — `pedagogy.instructional_model`, `forbidden_moves`, `phases[].objectives`, `mastery` criteria, `grounding_policy`, `persona`, `personalization.hard_locked`.

Your code's job is to *faithfully execute* that spec, not invent behaviour. If something feels hardcoded in your agent logic that could be expressed in YAML, push it back into YAML. That's what makes OPAS visible — and portable.

---

## What you're building (Day 4–6 critical path)

A FastAPI backend with four endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /session/start` | `{skill_id, learner_id}` → loads YAML, initialises `sessions` + first `phase_states`, returns session id + opening turn |
| `POST /session/turn` | `{session_id, learner_msg}` → retrieves corpus, calls model, emits events, returns agent reply + state |
| `POST /session/end` | `{session_id}` → closes session, writes journal entry stub |
| `GET /session/{id}/state` | for debugging and (later) dashboards |

Plus the runtime pieces that sit behind these:

1. **Skill loader** — `supabase.table('skills').select('yaml').eq('id', skill_id)` → parse JSONB → Python dict.
2. **RAG pipeline** — embed corpus chunks on first load (OpenAI `text-embedding-3-small`, 1536-dim; matches our pgvector HNSW index), store in `corpus_chunks`. On each turn: embed learner msg → HNSW nearest-k → filter by `grounding_policy.min_similarity` → inject into prompt.
3. **Prompt assembler** — builds system prompt from `pedagogy.instructional_model.description` + `persona.voice` + current phase objectives + `forbidden_moves` (as hard negative instructions) + retrieved corpus chunks (with citation tags).
4. **Model adapter (stub for now)** — single function `call_model(messages, hints) -> str`. Day 4–6 scope: Claude only via `ANTHROPIC_API_KEY`. Days 7–9 expands to GPT + Gemini with the same interface.
5. **Event emitter** — every turn writes to `events` table (xAPI-like verbs: `initialized`, `asked`, `responded`, `probed`, `updated`, `completed`). These feed the dashboard later.
6. **Probe scorer (deterministic first)** — at end of each phase, run the YAML's `evaluation_probes` against learner responses. Save to `probe_attempts`. LLM-as-judge fallback for free-text probes (stub `null` for now if no exact/regex accept rule).

---

## Success criteria (Day 6 end)

Manually through the FastAPI `/docs` UI (Swagger):

1. `POST /session/start` with `skill_id = 'hks.api318.unit1.thinking-probabilistically'` and any learner_id → returns a turn that opens Phase 1 (Levy's "certainty is an illusion" intro).
2. The opening turn contains a Socratic *question* (not a lecture) — forbidden_moves honoured.
3. `POST /session/turn` with a short learner answer → response cites at least one corpus chunk (e.g., `[Handout 1, §...]`) and elicits a numeric prior.
4. After 3–4 turns, Phase 1's first probe fires; result lands in `probe_attempts`.
5. `events` table shows a chronological event stream.
6. The agent *never* answers its own question before the learner offers a prior (forbidden_move enforced — you can unit-test this).

---

## Credentials (from Lucas, via 1Password shared vault)

```
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY       # server-side only — never leaves the API layer
SUPABASE_DB_PASSWORD            # only for direct-psql admin tasks; not needed by FastAPI
ANTHROPIC_API_KEY
OPENAI_API_KEY                  # for embeddings only in Day 4–6
GOOGLE_API_KEY                  # unused until Day 7–9
```

You'll also get **Developer** access on the Supabase dashboard (read-only on Auth + Billing; full on DB). Lucas will invite you.

---

## Reference files

- **The skill you're running:** `/skills/api-318-unit-1/skill.opas.yaml`. Read this end-to-end before you write a line of code. Pay particular attention to:
  - `pedagogy.instructional_model` (the operating mode)
  - `pedagogy.instructional_model.forbidden_moves` (hard constraints)
  - `phases[].entry_criteria` / `mastery` (state machine)
  - `corpus.grounding_policy` (RAG policy — `min_similarity: 0.72`, `refuse_if_ungrounded: true`)
  - `personalization.hard_locked` (what your adapter must NEVER adapt)
  - `persona.disallowed_phrases` (literal output filter)
- **Schema:** `deployment/supabase/schema.sql`. Note the RLS policies — your FastAPI uses the service-role key so RLS is bypassed, but that means your app layer *is* the access-control layer. Don't be loose.
- **Seed example:** `deployment/supabase/seed_step4_5_combined.sql`.
- **Architecture context:** `_deliverables/02_Architecture.md` (5-layer; you're building L2 runtime + parts of L3).

---

## Out of scope (do NOT build this cycle)

- Multi-agent A2A / event-bus coordination — that's L3 architecture work for later.
- Model adapter for 3 LLMs — Day 7–9 task (same person, different week). Build the *interface* now; fill in the other 2 models next week.
- Teacher dashboard reads — Day 10–11. But do make sure events are being written correctly so the dashboard work is just SELECTs.
- Mem0 integration — stub a "memories" write for now; real Mem0 wiring is Day 8+.
- WebSocket / streaming responses — FastAPI sync JSON is fine for Demo Day.
- Authentication on the FastAPI layer — Supabase Auth JWT verification can be Day 7; Day 4–6 can use a dev-only bearer token.

---

## Handoff interface (what Krizia and you need to agree on)

When Krizia's wizard emits a skill, her insert row shape must match what your loader expects. Agree on one field: **does her wizard set `status = 'pilot'` or `'published'`?** Recommend `'pilot'` so learner access requires consent grants. Share this decision in a 10-min sync on Apr 22.

---

## Stack recommendation

- Python 3.11 + FastAPI + Uvicorn.
- `supabase-py` for DB; `anthropic` SDK for Claude; `openai` SDK for embeddings.
- `pydantic` for request/response schemas; `python-dotenv` for `.env`.
- No LangChain / LlamaIndex for Day 4–6. Too much abstraction, we want the RAG path readable.

---

**Timebox:** Days 4–6 (Apr 22–24). First full-stack test (Krizia's wizard → your agent) targeted for end of Day 6.
