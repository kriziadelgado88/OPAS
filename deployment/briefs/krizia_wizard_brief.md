# Krizia — Teacher Wizard brief (Days 1–3)

**Goal:** Replace the hardcoded-prompt chatbot look with a visible *OPAS authoring* moment. Teacher answers ~6 questions in a wizard; a full OPAS YAML is emitted live; YAML is saved to Supabase. This is Demo Day beat #1 (of 3) — the audience must *see* OPAS being authored, not just used.

**Why this matters:** Mentor review (Apr 21): *"App looks like just another chatbot — no sign of OPAS at all."* This brief closes that gap.

---

## What you're building

A single-page HTML app. No backend of your own — the browser talks directly to Supabase. Three panes:

1. **Wizard (left).** 6 steps, one question at a time. Progress bar.
2. **Live YAML preview (right).** Updates on every keystroke; highlights the section just changed; gives the audience the "oh, *that's* OPAS" beat.
3. **Emit button (bottom).** Writes the YAML to the Supabase `skills` table. Success state = "Skill `<id>` saved — ready for the tutor."

---

## Wizard fields (map directly to OPAS YAML sections)

| Step | Wizard question | OPAS YAML section filled |
|---|---|---|
| 1 | Skill name + short ID | `skill.name`, `skill.id` |
| 2 | Grade level, subject, estimated duration | `skill.level`, `skill.subject`, `skill.duration` |
| 3 | Learning objectives (free text → bullets) | `learning_objectives[]` |
| 4 | Pedagogical model (dropdown: Socratic-Bayesian / Case-Method / Worked-Example / Constructivist-Free) | `pedagogy.instructional_model.primary` |
| 5 | Phases (name + duration + objective per phase, 3–6 phases) | `phases[]` (seed each phase with a default mastery signal) |
| 6 | Corpus (drag-drop Drive links or upload PDFs) | `corpus.primary_sources[]` |

For Step 4, preloading **Socratic-Bayesian** pulls in the `forbidden_moves` list and the `techniques.always_use` from our Unit 1 YAML — don't make the teacher type these. Same for the other pedagogies (future work; stub OK for Demo Day).

---

## Reference files

- **YAML shape to emit:** `/skills/api-318-unit-1/skill.opas.yaml` — the authoritative example. Generate YAML in this shape.
- **Database target:** `deployment/supabase/schema.sql` → `skills` table. Columns: `id, name, version, status, author_id, yaml, schema_version, published_at`. You write to `yaml` as JSON-from-YAML (JSONB).
- **How to insert:** Look at `deployment/supabase/seed_step4_5_combined.sql` for the insert pattern. Your version uses the Supabase JS client, not raw SQL.

---

## Credentials

You only need the browser-safe ones:

```
SUPABASE_URL=https://bhpfpespvjiolsglymbk.supabase.co
SUPABASE_ANON_KEY=<from the shared 1Password vault>
```

**Do NOT** put the Service Role Key, DB password, or any LLM keys in HTML. Those are server-side only.

RLS is enforced. Your insert will fail unless the authed user's `auth.uid()` is the `author_id`. Which means: **the teacher must sign in before emitting.** Use Supabase Auth's magic-link flow — ~20 lines of JS.

---

## Success criteria (Day 3 demo)

1. Teacher opens the page, signs in with magic link.
2. Fills the 6 wizard steps; YAML pane updates live on every answer.
3. Clicks **Emit**. Page shows "✓ Skill `<id>` saved".
4. Lucas checks Supabase Table Editor → new row in `skills` with full YAML JSONB.
5. The pre-seeded Unit 1 skill can also be loaded back into the wizard (round-trip test): open → edit one field → re-emit → Supabase updates.

---

## Out of scope (do NOT build this cycle)

- Student-side rendering (Dima's workstream).
- Full pedagogy library — stub the 3 non-Socratic models with "Coming soon" for now.
- Multi-author permissions, version history UI, skill sharing.
- Rich corpus ingestion (chunking + embedding) — for Demo Day, it's enough to store Drive file IDs and let Dima's RAG pipeline pick them up.

---

## Cleanup prerequisite

Your key-rotation brief (`deployment/day0/krizia_key_rotation_brief.md`) is still open. The hardcoded Anthropic + Google keys in `poppy-builder (9).html` and `poppy-student (9).html` must be rotated and moved server-side before anything built in this wizard ships. Can run in parallel with wizard development — but *must* be done before the first external share.

---

## Stack recommendation

HTML + Tailwind (you already use both) + two libraries:
- [`@supabase/supabase-js`](https://supabase.com/docs/reference/javascript) — DB + Auth.
- [`js-yaml`](https://github.com/nodeca/js-yaml) — the YAML ↔ JSON bridge for the live preview.

Both via CDN. No build step.

---

**Timebox:** Days 1–3 (Apr 22–24). If you hit any Supabase or YAML shape question, pull Lucas in same-day — the critical path to Demo Day runs through this.
