# 🌼 OPAS · The Soul

> **Democratizing agentic AI tutoring for all.**
>
> Built by **Lucas Kuziv** & **Krizia Delgado** · MIT AI STUDIO Spring 2026 

Two open-source primitives for safe, portable AI tutoring agents — and a reference implementation (Poppy) you can fork.

- 🎓 **[OPAS](opas/README.md)** — a protocol for authoring tutoring skills once and running them on any LLM.
- 🛡️ **[The Soul](soul/README.md)** — a constitutional safety layer for any agent that talks to students.
- 🤖 **Poppy** *(reference implementation)* — the FastAPI runtime + web client in `agent/` and `app/` that wires both protocols together. Fork it, change the skill, deploy your own.

Each piece works on its own. Use one, two, or all three.

```
[ Skill YAML ] ──▶ [ OPAS runtime ] ──▶ [ Soul enforcer ] ──▶ [ Claude / GPT / Gemini ]
                                              │
                                              └── intercepts every learner turn
                                                  AND every agent reply
```

## 🌍 The gap we're closing

| | |
|---|---|
| **1.4B** | students globally lack access to quality tutoring |
| **83%** | of teachers can't personalize instruction at scale |
| **$50B** | tutoring market inaccessible to most families |

Tutoring breaks at scale because pedagogy is locked inside proprietary models. OPAS separates teaching logic from the AI model so educators own the pedagogy, models stay swappable, and any institution can run its own tutor.

## 👥 One platform · three agents

The reference implementation serves three personas, each with a dedicated agent and dashboard:

- 🔵 **Learner — *the protagonist*** · private tutor; personalises to style, pace, and interests; durable memory across sessions.
- 🟢 **Teacher — *the amplifier*** · authors pedagogy without code; aggregates anonymous signals from student agents; proposes interventions.
- 🟡 **Parent — *the ally*** · age-appropriate summaries (never raw transcripts); pull-based, consent-gated; COPPA 2.0 / FERPA / GDPR compliant.

## 🧭 OPAS principles

- 🟡 **Decoupled Intelligence** — AI provides reasoning; OPAS provides pedagogy. Swap models freely.
- 🔵 **Interoperability** — xAPI 2.0 · LTI 1.3 · UNESCO AI Framework · Bloom's taxonomy.
- 🟢 **Sovereignty** — institutions own the teaching logic. No foreign model dependency.
- 🔴 **Evidence-Based** — retrieval practice · spaced repetition · ZPD scaffolding · adaptive learning.

## ✨ Why two primitives, one repo

We built these together while shipping a tutoring product (Poppy) and noticed they're each useful in isolation:

- OPAS solves *"how do I describe a tutoring skill in a way that survives the next model release?"* — by making the skill data, not code, and the LLM swappable.
- The Soul solves *"how do I make a student-facing agent that doesn't fail catastrophically when the student is in crisis?"* — by adding a deterministic regex layer below the model.

You can adopt OPAS without the Soul (e.g., adult corporate L&D where wellbeing intercepts aren't relevant), or the Soul without OPAS (e.g., you're already running your own tutoring stack and just want the safety layer). Both work standalone. For the formal protocol definition, see [`SPEC.md`](SPEC.md).

## 🚀 Quickstart

You'll need: Python 3.11+, a [Supabase](https://supabase.com) project (free tier works), an [Anthropic API key](https://console.anthropic.com).

**1. Clone and install**

```bash
git clone https://github.com/kriziadelgado88/OPAS.git
cd OPAS/agent
pip install -r requirements.txt
```

**2. Set up Supabase**

Follow the walkthrough at [`deployment/supabase/runbook.md`](deployment/supabase/runbook.md) — about 15 minutes. It covers creating the project, copying credentials, applying the schema, and seeding the reference skill.

**3. Configure environment**

```bash
cp .env.example .env
# Edit .env: paste in your Supabase URL + service-role key + Anthropic key
```

**4. Start the runtime**

```bash
uvicorn app.main:app --reload --port 8001
```

Confirm it's up at http://localhost:8001/health.

**5. Mint a learner token (so you can log into the demo)**

```bash
python scripts/mint_learner_tokens.py --email you@example.com
# copy the printed token
```

**6. Serve the frontend**

```bash
# in another terminal
cd ../app
python -m http.server 8080
```

**7. Open the demo**

- **Wizard** (author a skill): http://localhost:8080/opas-wizard.html
- **Student** (run the reference skill): paste the token, then go to
  `http://localhost:8080/opas-student.html?skill_id=hks.api318.unit1.thinking-probabilistically&token=<your-token>`

For the protocol details and the Soul integration guide, see the per-primitive READMEs above. For deeper architecture and limitations, see [`docs/architecture.md`](docs/architecture.md) and [`docs/limitations.md`](docs/limitations.md).

## 🏗️ Project layout

```
opas-public/
├── agent/                            # FastAPI runtime (the Poppy reference)
│   ├── app/
│   │   ├── prompt_assembler.py       # builds system prompts from skill YAML + state
│   │   ├── model_adapter.py          # Claude / GPT / Gemini swappable
│   │   ├── routers/                  # session, auth, dashboard, groups, etc.
│   │   └── constitutions/
│   │       └── enforcer.py           # ★ canonical Soul enforcer
│   └── constitutions/
│       └── minerva.soul.v1.yaml      # ★ canonical Soul rules
├── app/                              # Static web frontend (wizard + student client)
├── opas/                             # OPAS-specific README + docs
├── soul/                             # Soul-specific README + integration examples
├── skills/
│   └── api-318-unit-1/
│       └── skill.opas.yaml           # reference skill (protocol example)
├── deployment/                       # Supabase schema + migrations
├── docs-site/                        # source for opas-docs.vercel.app
├── SPEC.md                           # formal OPAS skill schema (L1-L4 stack)
├── LICENSE                           # MIT
└── README.md                         # you are here
```

## 📄 License

**MIT.** Everything in this repo — OPAS, the Soul, and the Poppy reference implementation — is open source. Copy freely, modify freely, ship freely. See [LICENSE](LICENSE).

The hosted **Poppy** product (the brand, the deployed service at our institution) is what's commercial. Your fork is yours.

## 🙌 Credits & acknowledgments

Built as part of Project Minerva-Poppy by **Lucas Kuziv** and **Krizia Delgado** — SLAI Cohort 2026, Berkman Klein Center for Internet & Society.

Standing on the shoulders of: Vygotsky's ZPD, Black & Wiliam's formative assessment work, the Anthropic Constitutional AI team, UNESCO's AI in Education guidance, and every teacher whose practice this is trying to formalize.
