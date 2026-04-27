# OPAS — Portable Tutoring Skills

A protocol for authoring AI tutoring skills as YAML and running them on any LLM. The skill is data, not code. The LLM is swappable.

---

## The thesis

Tutoring quality lives in the skill, not the prompt. A good skill encodes:

- A **pedagogy template** — Socratic, mastery-based, project-based, etc. (we ship four)
- A **persona** — voice, register, disallowed phrases
- **Phase objectives** — what the learner should be able to do after each phase
- **Probes** — the questions the agent uses to assess understanding
- **Corpus grounding** — the materials every factual claim must cite back to
- **Personalization rules** — what may adapt per learner (interests, language) and what's hard-locked (objectives, citation rules)

Every skill is portable. Switch from Claude to Gemini by changing one env var; the skill YAML doesn't change.

---

## Hello, world

A minimal skill (`skills/api-318-unit-1/skill.opas.yaml` is the reference example):

```yaml
id: hks.api318.unit1.thinking-probabilistically
version: "0.1.0"
name: "Thinking Probabilistically"

constitution: minerva.soul.v1   # see ../soul/README.md

pedagogy:
  template: socratic_bayesian
  instructional_model:
    description: "Agent never lectures first. Probes the learner's prior, then introduces evidence one piece at a time."
    forbidden_moves:
      - "Stating Bayes' formula before the learner has offered a prior."
      - "Accepting 'it depends' without pressing for a number."

persona:
  voice: "warm Socratic tutor, curious not patronizing"
  register: informal
  disallowed_phrases: ["good question", "let me explain"]

phases:
  - id: intuition
    objectives:
      - "Articulate probability as a degree of belief, not a frequency."
    probe_set:
      - id: P1-prior
        prompt: "Give me a probability that the next coin flip is heads. Defend it."

corpus:
  sources:
    - drive_id: 1XYZ...
      title: "Thinking Probabilistically — Unit 1"
  grounding_policy:
    require_citation: true
    citation_style: "[Source, §section]"
```

Drop this into Supabase (or load from disk for local dev), point a learner at it, and you have a tutoring agent. The runtime takes care of:

- Building the system prompt for each turn (12 sections — persona, forbidden moves, phase objectives, retrieved corpus chunks, probes, etc.)
- Tracking phase progression as the learner answers probes correctly
- Emitting xAPI events so you can analyze sessions later
- Enforcing the constitution (the Soul) on every learner turn and every agent reply
- Running the same skill against Claude, GPT-4, or Gemini (model adapter is one file)

---

## Why this isn't just "a system prompt"

Three things a system prompt can't give you that OPAS does:

1. **Phase state.** The agent knows what phase the learner is in and progresses based on probe outcomes — not just "here's the whole curriculum, figure it out."
2. **Citation enforcement.** Every factual claim must cite a corpus chunk. Enforced in the prompt assembler + the response validator, not just hoped for.
3. **Personalization rules.** What can adapt per learner is declared. The agent literally cannot adapt the assessment criteria — that's a hard-locked surface.

These belong in the skill (data) so they survive prompt-engineering churn, not in the prompt (text) where they decay each time you tweak a phrase.

---

## The skill YAML schema (high level)

A skill has these top-level sections:

| section | required | purpose |
| --- | --- | --- |
| `id` | yes | unique, dot-separated. The runtime keys off this. |
| `version` | yes | semver string. Bump when the skill changes. |
| `name` | yes | human-readable title. |
| `constitution` | recommended | references a constitution YAML (e.g. `minerva.soul.v1`). |
| `pedagogy` | yes | which template + the instructional model. |
| `persona` | yes | voice, register, disallowed phrases. |
| `phases` | yes | ordered list of teaching units, each with objectives + probes. |
| `corpus` | yes | source references + grounding policy. |
| `personalization` | optional | hard-locked vs allowed-to-adapt surfaces. |

For the canonical schema with every field, validation rules, and examples, see [`docs/opas-protocol-spec.md`](../docs/opas-protocol-spec.md) (TBD — coming in v0.2).

---

## The reference runtime

Included: a FastAPI service that loads skills from Supabase, runs sessions, emits events, and serves a generic web client (`opas-student.html`) and authoring wizard (`opas-wizard.html`).

- **Default model adapter:** Claude (`claude-sonnet-4-6`).
- **Pluggable adapters:** GPT-4 and Gemini ship as functional but less-tested.
- **Auth:** magic-link signup + bearer tokens. Production deployments should swap to OAuth.
- **Storage:** Supabase. Migrations in `deployment/supabase/`.
- **Sample skill:** `skills/api-318-unit-1/skill.opas.yaml` — the protocol example. The corpus content (HKS course materials) is not redistributed; you'll need to point at your own.

### Running it

```bash
cd agent
pip install -r requirements.txt
cp .env.example .env       # fill in keys
uvicorn app.main:app --reload --port 8001

# in another terminal
cd ../app
python -m http.server 8080
```

Wizard: http://localhost:8080/opas-wizard.html
Student client: http://localhost:8080/opas-student.html

---

## The pedagogy catalogue

Four templates ship in `agent/app/routers/pedagogies.py`:

- **Discovery Learning** — Bruner-style. Heavy probes, direct instruction is a last resort.
- **Socratic Method** — every turn ends with a question. Used in the reference skill.
- **Spiral Curriculum** — same concept revisited at increasing depth across phases.
- **Direct Instruction with Checks** — explanation first, comprehension probe immediately after.

Adding a fifth is small — see [Contributing](#contributing).

---

## When to use OPAS vs. just a system prompt

Use a system prompt when:

- The session is single-turn or stateless
- There's no curriculum, just a persona
- You don't need citation enforcement or progress tracking

Use OPAS when:

- You're building multi-turn tutoring with phases and assessment
- The same skill needs to run on multiple LLMs (procurement portability, model drift hedging)
- Multiple authors are creating skills and you need a shared format
- You need analytics (xAPI events) for what's working in the curriculum

---

## What's production-ready vs. demo-quality

**Production-ready**

- The skill YAML schema (v1.0 frozen)
- The prompt assembler (deterministic, ~250 lines, well-tested)
- The model-adapter contract

**Demo-quality**

- Auth (magic-link + bearer; should be OAuth in production)
- The wizard (functional; lacks versioning, drafts, review workflow)
- The Gemini and OpenAI adapters (work but less battle-tested than Claude)

**Missing (roadmap)**

- A 27-block pedagogy taxonomy that decomposes pedagogy into composable layers (foundations, approach, techniques, modalities, context). v0.2.
- A teacher dashboard for anonymized cross-class signals. v0.2.
- The full protocol spec doc. v0.2.

---

## Contributing

Most-needed contributions, ranked:

1. **New pedagogy templates** — add a YAML template to `agent/app/routers/pedagogies.py`.
2. **New model adapters** — the interface in `agent/app/model_adapter.py` is one method.
3. **Sample skills** — a real skill in your domain (intro CS, freshman writing, statistics, civics) is more valuable than ten stars. PR the YAML to `skills/`.
4. **Documentation** — if anything in the schema or runtime is wrong or missing, that's a real bug for an open-source protocol.

---

## License

MIT. See [`../LICENSE`](../LICENSE).
