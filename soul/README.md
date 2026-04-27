# The Soul — Constitutional Safety for Tutoring Agents

Two files. A YAML constitution defining five always-on rules + 16 distress patterns + 9 harm patterns. And a Python enforcer that scans every learner message and every agent reply against those patterns deterministically.

That's it.

---

## The five rules

1. **Distress response** — if the learner expresses distress or frustration ("I can't do this", "I'm stupid"), stop teaching and respond with empathy first.
2. **No shame** — never use shame, comparison to other students, or pressure as motivation. Frame struggle as growth, not failure.
3. **Epistemic honesty** — admit when you don't know rather than guessing. Never present uncertainty as fact.
4. **Break respect** — when the learner wants to stop, respect that immediately. Do not pressure them to continue.
5. **Harm disclosure** — if the learner discloses harm (abuse, self-harm), respond with care and suggest a trusted adult. Do **not** probe for details.

These are not configurable per skill. That's the point: a tutoring agent that breaks any of these is unsafe regardless of pedagogy. Make them constitutional, not user-tunable.

---

## Why deterministic

LLM-based safety judges are probabilistic. They might catch "I want to die" in 99% of phrasings and miss the 1%. For five rules where the cost of a miss is genuine harm to a kid, 99% isn't enough.

The Soul uses a regex scanner that catches a curated list of phrases — 16 distress patterns, 9 harm patterns — and intercepts deterministically. Add patterns over time as you observe real user phrasings; the list grows, the determinism stays.

The model-level safety training (RLHF, constitutional AI, etc.) still runs above this layer. The Soul doesn't replace it. It's a backstop you can ship and verify in tests, not in production logs.

---

## The two canonical files

```
agent/app/constitutions/enforcer.py    # the regex scanner
agent/constitutions/minerva.soul.v1.yaml   # rules + patterns + injections
```

Copy these two files into any agent project to get the Soul. They have no dependencies beyond Python's stdlib `re` module and PyYAML.

---

## Drop it into any agent in 5 lines

```python
from soul import Soul

soul = Soul.load("constitutions/minerva.soul.v1.yaml")

def chat_turn(learner_msg: str, model_call) -> str:
    if (intercept := soul.scan_learner(learner_msg)):
        return intercept                          # wellbeing protocol fired
    reply = model_call(learner_msg)
    return soul.scan_agent(reply, learner_msg)    # blocks/rewrites bad agent replies
```

`soul.scan_learner()` returns a wellbeing-protocol response if a distress or harm pattern matches. Your agent never sees the message in those cases — the learner sees a care response.

`soul.scan_agent()` checks the LLM's reply for shame, pressure, or rule violations before it reaches the learner.

The OPAS runtime in this repo wires the Soul in at `agent/app/routers/session.py`. Use that as the integration reference.

---

## The 16 distress patterns

A 60-second cooldown applies — the same pattern won't re-trigger inside that window, so a learner repeatedly typing "i can't do this" doesn't loop them through the wellbeing protocol on every turn.

```
i'm stupid          im stupid          i am stupid
i'm dumb            im dumb            i am dumb
i can't do this     i cant do this
i give up           i quit             this is impossible
i'm not smart enough     im not smart enough
i hate myself
i'm worthless       im worthless
```

Match → inject this system note before the agent's next turn:
> Student is expressing emotional distress. Follow wellbeing protocol. Acknowledge feeling first before any teaching.

---

## The 9 harm patterns

**Zero cooldown.** Every match triggers, every time. There is no "this learner already disclosed once today, skip it." Intentional.

```
hits me             hurts me            abuses me
touches me
i don't feel safe   i dont feel safe
i want to hurt myself
i want to die       kill myself
```

Match → inject:
> Student may be disclosing harm. Follow harm-disclosure protocol. Do NOT probe for details. Respond with care and suggest a trusted adult or counselor.

The pattern list is intentionally narrow. It catches explicit disclosure phrasings — not implicit hints, not metaphor, not "I'm dying of boredom." For ambiguous cases, the model-level safety training is what catches them; the Soul covers the explicit ones the model might fumble.

---

## Adding patterns over time

Edit `agent/constitutions/minerva.soul.v1.yaml`. The patterns are case-insensitive substring matches. Bump `version` (e.g. `minerva.soul.v1.1`) when you add patterns so consumers can pin a version if they want stability.

If you observe a phrasing the scanner missed — please [open an issue](#) or PR. The pattern list is the single highest-leverage contribution to safety.

---

## What the Soul is grounded in

- **UN Convention on the Rights of the Child + UDHR** — the human-rights backbone for child-facing AI. Article 19 (protection from harm) and Article 28 (right to education) are the core anchors.
- **Anthropic's Constitutional AI** methodology — but narrower (5 rules, not dozens) and deterministic (regex, not LLM judge).
- **Educational psychology + child-protection guidance** — the patterns and interventions are reviewed against published clinical recommendations, not vibes.

Full grounding citations live in [`docs/the-soul.md`](../docs/the-soul.md) (TBD — coming in v0.2).

---

## When NOT to use it

The Soul is tuned for student-facing tutoring. It's probably wrong for:

- Adult professional contexts (its empathy thresholds will fire on normal frustration like "I can't do this" said about a difficult work problem)
- Code completion or productivity tools (no distress patterns relevant)
- Any context where users have explicitly opted into harsh / blunt feedback styles

In those cases you want a **different constitution**, not no constitution. The grammar (rules + patterns + interventions) is the reusable part — write your own YAML using `minerva.soul.v1.yaml` as a template.

---

## Soul context extensions (proposal — v0.2)

The Soul is **additive-only**. Future context-specific extensions would add detection patterns and rules without removing any of the five core rules:

- Neurodivergence (ADHD, autism, dyslexia, dyscalculia)
- Refugee / displaced populations
- Physical disability / chronic illness
- Economic hardship
- First-generation students
- Language learners (ELL/ESL)

These ship as separate YAML files that compose with the core constitution. PR your own.

---

## Limitations

- **English-only** in the current pattern lists. Multilingual patterns are a near-term priority.
- **Substring matching** — won't catch creative misspellings or l33tspeak. The threat model assumes good-faith learner phrasings, not adversarial input.
- **Not a replacement for human review** — a tutoring deployment that goes to real students should have a process for educators or counselors to review flagged sessions.

---

## License

MIT. See [`../LICENSE`](../LICENSE). The Soul is permissively licensed because we want it adopted as widely as possible — the more student-facing AI agents that route through a constitutional layer, the better.

If you ship the Soul (or a derivative) in a production agent, we'd love to hear about it.
