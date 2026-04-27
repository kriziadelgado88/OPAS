# Contributing

Thanks for your interest. This project ships as two open primitives — OPAS (the protocol) and the Soul (the safety layer) — and contributions to either are welcome.

This guide is short on purpose. Most contributions don't need ceremony.

---

## What we most need

Ranked roughly by leverage:

1. **New Soul patterns.** If you observe a learner phrasing the regex scanner missed (and a rule should have triggered), that's the highest-leverage contribution to safety. [Open an issue](#) with the phrasing and which rule should have caught it.
2. **New pedagogy templates.** Add a YAML template to `agent/app/routers/pedagogies.py`. We ship 4; reasoned additions welcome.
3. **New model adapters.** The interface in `agent/app/model_adapter.py` is one method. Adding Mistral, Llama (via vLLM or Ollama), Cohere, etc. is usually a couple of hours of work.
4. **Sample skills.** A real skill in your domain (intro CS, freshman writing, statistics, civics, anything) is more valuable than ten stars. PR the YAML to `skills/`.
5. **Documentation.** If anything in the schema, runtime, or this guide is wrong, unclear, or missing — that's a real bug for an open-source protocol.
6. **Multilingual Soul patterns.** Currently English-only. Adding distress/harm patterns in Spanish, French, Arabic, Mandarin, Hindi, Portuguese, etc. is high-priority for v0.2.

## What we won't merge

To set expectations:

- **Removing or weakening any of the 5 Soul rules.** Not negotiable. The whole point of constitutional safety is that it's not configurable per skill.
- **Pedagogy templates that contradict the existing forbidden-moves vocabulary** (e.g., "give the student the answer immediately"). The Soul + the OPAS forbidden-move grammar are designed to compose; PRs that fight that are rejected.
- **Adapters for models we can't verify pass basic tutoring quality.** If the model can't follow the prompt assembler's structure reliably, the adapter doesn't ship. We test against the reference skill before merging adapters.

---

## How to PR

Standard GitHub flow:

1. **Fork** the repo.
2. **Branch** off `main` with a descriptive name (e.g., `add-spanish-distress-patterns`, `pedagogy/inquiry-based`, `adapter/mistral`).
3. **Make your change.** Keep PRs small and focused — one PR per concept.
4. **Run the tests** if applicable: `pytest agent/` from the repo root.
5. **Update relevant docs** in the same PR. If you're adding a pedagogy template, also add a 2-line note to `agent/app/routers/pedagogies.py`. If you're adding a Soul pattern, also bump the version in `minerva.soul.v1.yaml`.
6. **Open the PR** with a clear description: what you changed, why, and how you tested it.

We aim to review within a week. If we go silent for longer, ping the PR.

---

## Adding a Soul pattern

The single most welcome contribution. Two-line PR template:

```yaml
# in agent/constitutions/minerva.soul.v1.yaml
distress:
  patterns:
    - "i'll never get this"  # ← your new pattern
```

Before submitting, please:

- Confirm the phrasing is something a real learner would actually type (not just a grammatical possibility).
- Pick the right list — `distress` for frustration/self-criticism, `harm_disclosure` for abuse/self-harm.
- Bump the constitution version in the YAML's `version` field if your PR is the first to land in a new patch (e.g., `1.0` → `1.1`).
- Add a one-line note in the PR description explaining where you observed the phrasing (a session log, a published study, your own teaching experience).

Multi-language patterns are a separate file (e.g., `minerva.soul.v1.es.yaml`) — see [`docs/limitations.md`](docs/limitations.md) for the v0.2 plan.

---

## Adding a pedagogy template

Each template in `agent/app/routers/pedagogies.py` has this shape:

```python
{
    "id": "your-template-id",
    "name": "Human Readable Name",
    "description": (
        "One paragraph: theoretical basis, when this approach works, "
        "and what the agent will and won't do."
    ),
    "techniques": [
        "five short imperative-mood techniques the agent should follow",
        "...",
    ],
}
```

The `techniques` list becomes the wizard's "what does this pedagogy do?" preview, and seeds the prompt assembler's forbidden-moves and instructional-model sections when an educator selects this template.

Please ground your template in published pedagogy research (cite sources in the PR description). We don't merge novel ad-hoc approaches; this catalogue is meant to be evidence-based.

---

## Adding a model adapter

The interface is one class with one async method. See `agent/app/model_adapter.py` for the Claude implementation as the template.

Things to watch:

- **System prompt handling** — most providers accept system prompts as a separate field; OpenAI's older models nest it as the first message.
- **Token counting** — the prompt assembler doesn't truncate; if your provider has tighter context windows, add a graceful error in your adapter.
- **Retries** — at minimum, exponential backoff on 429s. The reference Claude adapter has the pattern.
- **Tests** — add a smoke test that runs the reference skill end-to-end against your adapter. We won't merge an adapter without one.

---

## Code style

- **Python** — Black-formatted, 88-char line length, type hints for public functions. We don't enforce stricter typing; pragmatism wins.
- **JavaScript** — vanilla, no build step. If you add a dependency to a static page, prefer CDN over `npm install`.
- **SQL** — the schema lives in `deployment/supabase/schema.sql`. Keep migrations idempotent and reversible.
- **YAML** — 2-space indent. Keep skills under ~500 lines; if it's getting longer, split phases or move corpus refs to a separate file.

---

## Reporting bugs

[Open an issue](#) (TODO: replace with actual repo URL once published) with:

- What you expected to happen
- What actually happened
- Minimal repro steps
- Your environment (OS, Python version, model provider)

Bonus points for a failing test or a one-line fix in the same issue.

---

## Reporting safety issues

If you find a way for the Soul to miss an explicit harm-disclosure pattern, **don't** open a public issue. Email the maintainers directly (contact in [`README.md`](README.md) credits section) so we can patch quickly. We treat these as critical and aim to ship a fix within 48 hours.

---

## Code of conduct

Be kind. We're building tools for kids; the standard for how contributors treat each other should match. Disagreements are welcome; insults aren't.

If something goes wrong, contact the maintainers in private first. Most things resolve with one good-faith conversation.
