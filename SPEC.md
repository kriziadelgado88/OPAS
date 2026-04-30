# OPAS Specification

**Version:** 0.2 (draft) · **Status:** Living document · **License:** MIT

OPAS — *Open Pedagogy Agent Specification* — is a YAML schema + runtime contract for authoring tutoring skills once and running them on any LLM. This document is the formal spec.

If you're new, start with the [README](README.md) and the [hosted docs](https://opas-docs.vercel.app). This file is the reference.

---

## 1. Design goals

OPAS exists because pedagogy keeps getting locked inside whichever model happens to be popular this year. A skill written for GPT-4 in 2024 has to be re-prompt-engineered for Claude 4 in 2026 even though the *teaching* hasn't changed. OPAS makes the teaching the durable artifact and the model the swappable one.

Three commitments:

1. **Decoupled intelligence.** A skill is data, not code. The same YAML runs on Claude, GPT, or Gemini without modification.
2. **Sovereignty.** Institutions own the teaching logic. No foreign-model dependency, no proprietary lock-in.
3. **Evidence-based floor.** Every skill must declare its theoretical basis (retrieval practice, ZPD, spaced repetition, etc.). Pedagogy without grounding is rejected at parse time.

---

## 2. The four-layer stack

Every OPAS skill is assembled from four conceptual layers. Higher layers depend on lower layers. You can swap any single layer without rebuilding the system.

```
  ┌──────────────────────────────────────────────────────────┐
  │ L4 · Localisation        language · device · runtime     │
  ├──────────────────────────────────────────────────────────┤
  │ L3 · Contextual Modifiers connectivity · cohort · locale │
  ├──────────────────────────────────────────────────────────┤
  │ L2 · Pedagogical Engine  approach · techniques · modal.  │
  ├──────────────────────────────────────────────────────────┤
  │ L1 · Learning Science    evidence-based floor (always-on)│
  └──────────────────────────────────────────────────────────┘
                  ↑ ASSEMBLY DIRECTION ↑
```

| Layer | What it declares | Skill YAML keys |
|---|---|---|
| **L1 · Learning Science** | The non-negotiable theoretical commitments (constructivism, retrieval practice, ZPD, calibration). What MUST hold. | `pedagogy.theoretical_basis` |
| **L2 · Pedagogical Engine** | The educator-selectable instructional model (Socratic, Direct Instruction, Discovery, Mastery-Based, etc.) and the concrete techniques the agent is allowed to use. | `pedagogy.instructional_model`, `pedagogy.techniques` |
| **L3 · Contextual Modifiers** *(planned)* | Per-cohort and per-locale overrides — class size, prior knowledge band, time budget, connectivity. | `context.*` *(roadmap)* |
| **L4 · Localisation** *(planned)* | Per-learner runtime adaptation — language, device profile, accessibility surface. | `localisation.*` *(roadmap)* |

L1 and L2 are fully implemented in v0.2. L3 and L4 are in the roadmap; the schema reserves the keys.

---

## 3. Skill YAML schema (v0.2)

A reference skill lives at [`skills/api-318-unit-1/skill.opas.yaml`](skills/api-318-unit-1/skill.opas.yaml). The required top-level keys:

```yaml
opas_version: "0.2"

skill:
  id: string                    # globally unique, dotted (e.g. hks.api318.unit1.thinking-probabilistically)
  name: string
  version: semver
  status: draft | pilot | published
  license: SPDX expression
  authors: [{ name, role, org }]
  language: BCP-47 tag
  audience: string
  estimated_learner_hours: number

learning_objectives:
  primary: { id, statement }
  sub_objectives: [{ id, statement }]

# L1 — Learning Science
pedagogy:
  theoretical_basis: [string]          # the always-on floor

# L2 — Pedagogical Engine
  instructional_model:
    primary: string                    # one of the catalog ids in /opas/pedagogies
    description: string
    secondary_models_allowed: [string]
    forbidden_moves: [string]
  techniques:
    always_use: [{ id, description }]
    often_use:  [{ id, description }]
    avoid:      [{ id, description }]

persona:
  voice: string
  register: string
  opening_style: string
  closing_style: string
  disallowed_phrases: [string]

personalization:
  allowed_surfaces: [...]              # what the runtime MAY adapt per learner
  hard_locked: [...]                   # what it MUST NOT adapt (the pedagogical spine)

phases:
  - id: string
    name: string
    opening_prompt: string
    key_concepts: [string]
    follow_ups: [string]
    slos_covered: [string]             # references to learning_objectives.sub_objectives[].id
    mastery_probes: [...]

corpus:
  primary_sources: [...]               # canonical materials the agent must cite
  retrieval: { mode: rag | direct, embedding_model: string }
```

For the full schema with every optional key, validate against [`opas/schema.json`](opas/schema.json) *(in progress)*.

---

## 4. Runtime contract

A conformant OPAS runtime must:

1. **Load a skill YAML** and validate it against the schema. Reject skills missing L1 (`theoretical_basis`).
2. **Assemble a system prompt** by concatenating, in order: persona → learning objectives → current phase → L1 commitments → L2 forbidden moves → corpus context → safety preamble.
3. **Drive a phase state machine.** Track `current_phase_index`, `phase_turn_index`, `mastery_met`. Advance only when the phase's mastery probes pass.
4. **Route every learner message AND every model reply through the Soul enforcer** before display. (See [`soul/README.md`](soul/README.md).)
5. **Emit xAPI 2.0 events** for: session start/end, phase transition, probe attempt, distress trigger, mastery achieved.
6. **Persist session state** so a learner can resume across devices and sessions.

The reference implementation is in [`agent/`](agent/). It runs Claude / OpenAI / Gemini interchangeably via [`agent/app/model_adapter.py`](agent/app/model_adapter.py).

---

## 5. Pedagogy catalogue

The L2 `instructional_model.primary` field must reference one of the pedagogy IDs in [`opas/pedagogies/`](opas/pedagogies/) *(or via the live `/pedagogies` API endpoint)*. Currently catalogued:

| ID | Theoretical basis |
|---|---|
| `discovery-learning` | Bruner — learner constructs knowledge through exploration |
| `socratic` | Plato/Levy — agent asks; learner answers; agent probes |
| `spiral` | Bruner — revisit concepts at increasing depth |
| `direct-instruction` | Engelmann — explicit, sequenced explanation |
| `backward-design` | Wiggins & McTighe — start from the assessment |
| `mastery-based` | Bloom — no advancement without demonstrated competency |
| `inquiry-based` | Dewey — learner-driven question generation |
| `project-based` | Kilpatrick — sustained authentic problem |
| `flipped` | Bergmann & Sams — first exposure outside class, practice in |

New pedagogies are added by writing a YAML file in `opas/pedagogies/` and PR-ing.

---

## 6. Soul integration

The Soul is mandatory for any skill that ships to learners under 18 or to vulnerable adult populations. It is optional (but recommended) otherwise. The OPAS runtime MUST call the Soul enforcer twice per turn:

1. **Pre-model**: scan the learner's message for distress / harm patterns. If matched, short-circuit the model call and return the configured intervention.
2. **Post-model**: scan the agent's draft reply for forbidden patterns (epistemic dishonesty, shame, premature reassurance). If matched, regenerate or fall back.

See [`soul/README.md`](soul/README.md) for the integration recipe. The Soul has its own spec at [`soul/SOUL_SPEC.md`](soul/SOUL_SPEC.md) *(in progress)*.

---

## 7. Versioning & compatibility

- **Schema version** is declared in `opas_version` at the top of every skill YAML.
- **Major bumps** (0.x → 1.x) may break existing skills. Migration scripts ship in `deployment/migrations/`.
- **Minor bumps** (0.2 → 0.3) add fields. Existing skills remain valid.
- **Patch bumps** (0.2.0 → 0.2.1) fix wording and clarify constraints. Always backwards-compatible.

The current version is 0.2. The first frozen public release will be 1.0, expected once L3 (Contextual Modifiers) is implemented and validated against three independent skills from three different institutions.

---

## 8. Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The fastest way to contribute:

- **Author a skill** in your domain → submit it under `skills/<your-skill>/` for inclusion in the reference set.
- **Add a pedagogy** to the L2 catalogue → drop a YAML in `opas/pedagogies/` and document it.
- **File spec issues** → open a GitHub issue tagged `spec:vague` or `spec:gap` if a runtime behavior is underspecified.

---

*Specification authored by Lucas Kuziv & Krizia Delgado · MIT AI Studio Spring 2026. MIT-licensed; copy and adapt freely.*
