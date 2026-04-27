# Limitations

Honest scope of what's in this repo. We'd rather you know upfront than discover at deploy time.

---

## What's production-ready

These pieces are stable, well-tested, and we ship them in our own product:

- **The skill YAML schema (v1.0)** — frozen. Future versions are additive only.
- **The prompt assembler** — deterministic, ~250 lines, has unit tests covering each of the 12 sections.
- **The Soul enforcer** — deterministic regex matcher, ~200 lines, has tests covering all 25 patterns.
- **The session loop** — the per-turn flow in `agent/app/routers/session.py` is stable.
- **xAPI event emission** — every turn writes a structured event; the schema is what you'd expect for downstream analytics.

If you're integrating any of these into your own project, you can rely on the interface staying stable across minor version bumps.

---

## What's demo-quality

These work, but you'd want to harden them before going to real users at scale:

- **Auth.** Magic-link signup + bearer tokens for demo. Production deployments should swap to OAuth (Google / Microsoft / institutional SSO) and proper session management with refresh tokens.
- **The wizard.** Functional — a teacher can author and publish skills end-to-end. But it lacks: draft/review workflow, version history per skill, multi-author collaboration, undo/redo. For a serious classroom deployment you'd add these.
- **The skill catalogue UI.** `app/opas-skills.html` is a flat list. Past ~30 skills you'd want filtering, search, and tags.
- **Model adapters other than Claude.** OpenAI and Gemini adapters are functional but less battle-tested. Edge cases around long-context handling, retry logic on rate limits, and streaming are Claude-tuned first.
- **Corpus ingestion.** `agent/scripts/ingest_corpus.py` chunks PDFs and embeds them. It works but has no incremental update — if you change a source PDF you re-ingest the whole corpus.

---

## What's missing

These are real gaps, not nitpicks. Each is a near-term roadmap item:

- **Multilingual Soul patterns.** The 16 distress + 9 harm patterns in `minerva.soul.v1.yaml` are English-only. Tutoring an ELL student in Spanish, French, Arabic, etc. would currently bypass the regex layer entirely (model-level safety still catches some cases, but not deterministically). High-priority for v0.2.
- **27-block pedagogy taxonomy.** We ship 4 pedagogy templates (Discovery, Socratic, Spiral, Direct Instruction). Internally we have a 27-block taxonomy that decomposes pedagogy into composable layers (Foundations / Approach / Techniques / Modalities / Context). Landing it as a v0.2 wizard upgrade.
- **Teacher dashboard with cross-class signals.** `agent/app/routers/dashboard.py` exists but is minimal — it currently shows aggregate session counts. The richer analytics (which probes are tripping students, which phases take longer than expected, which forbidden-moves keep nearly firing) live in xAPI events but aren't surfaced yet.
- **Parent persona.** Age-appropriate session summaries for parents (consent-gated, never raw transcripts) is on the roadmap. v0.3.
- **Soul context extensions.** Additive constitution layers for neurodivergence, refugee populations, language learners, economic hardship, first-generation students, physical disability/chronic illness. Each ships as a separate YAML that composes with `minerva.soul.v1`. v0.4.
- **The full protocol spec doc.** `docs/architecture.md` covers the runtime; a dedicated `docs/opas-protocol-spec.md` with the canonical YAML schema (every field, validation rules, examples) is on the roadmap. For now the schema is documented in [`/opas/README.md`](../opas/README.md) and the working code in `agent/app/prompt_assembler.py`.

---

## Things we don't claim to do

We mention these because some readers will assume they're implicit. They aren't.

- **We are not a model.** OPAS is a protocol + runtime; the model is whichever LLM you point it at. We make no claims about the underlying model's capabilities, safety, or pedagogy beyond what the prompt + the enforcer can do.
- **The Soul does not replace model-level safety.** It's a backstop you can ship and verify in tests. Model-level RLHF / constitutional AI / system prompts still run above it. We strongly recommend not turning those off.
- **OPAS is not a teacher-replacement.** We've been deliberate in framing throughout: an OPAS skill is a *tutoring agent* that supports human teachers, not a substitute for them. The wizard's Soul panel surfaces this — the constitutional rules include "respect when the student wants to stop" because the agent shouldn't push past human judgement.
- **We don't have child-safety certifications yet.** COPPA / FERPA / GDPR compliance work is in progress, not done. If you're deploying to under-13 learners in the US, talk to your legal team before going live.

---

## Known issues / sharp edges

- The student client caches the bearer token in `localStorage`. Anyone with access to the device can resume a session as that learner. Production: consider refresh-token-on-session-end.
- The wizard's YAML preview can drift from the saved record in rare cases (auto-save race condition under flaky network). Not data loss, but the preview can lie until you reload.
- Embedding model is hard-coded to OpenAI's `text-embedding-3-small`. Swapping to a local model (e.g., for cost or offline use) requires a small change in `agent/app/rag.py`.
- The session loop is synchronous. Long agent replies block the request. Streaming is a planned upgrade.

---

## Reporting issues

If you hit a limitation we haven't documented here, [open an issue](#) (TODO: replace with the actual repo URL once published) — it's the single most useful thing you can do for the project.
