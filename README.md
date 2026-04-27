# 🧱 OPAS · The Soul

Two open-source primitives for safe, portable AI tutoring agents.

- 🎓 **[OPAS](opas/README.md)** — a protocol for authoring tutoring skills once and running them on any LLM.
- 🛡️ **[The Soul](soul/README.md)** — a constitutional safety layer for any agent that talks to students.

Each primitive has its own README. Use both, or use either independently.

```
[ Skill YAML ] ──▶ [ OPAS runtime ] ──▶ [ Soul enforcer ] ──▶ [ Claude / GPT / Gemini ]
                                              │
                                              └── intercepts every learner turn
                                                  AND every agent reply
```

## ✨ Why two primitives, one repo

We built these together while shipping a tutoring product (Poppy) and noticed they're each useful in isolation:

- OPAS solves "how do I describe a tutoring skill in a way that survives the next model release?" — by making the skill data, not code, and the LLM swappable.
- The Soul solves "how do I make a student-facing agent that doesn't fail catastrophically when the student is in crisis?" — by adding a deterministic regex layer below the model.

You can adopt OPAS without the Soul (e.g., adult corporate L&D where wellbeing intercepts aren't relevant), or the Soul without OPAS (e.g., you're already running your own tutoring stack and just want the safety layer). Both work standalone.

## 🚀 Quickstart

You'll need: Python 3.11+, a [Supabase](https://supabase.com) project (free tier works), an [Anthropic API key](https://console.anthropic.com).

**1. Clone and install**

```bash
git clone https://github.com/<your-org>/opas.git
cd opas/agent
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
opas/
├── agent/                            # FastAPI runtime
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
├── LICENSE                           # MIT
└── README.md                         # you are here
```

## 📄 License

MIT. Copy freely, modify freely, ship freely. See [LICENSE](LICENSE).

## 🙌 Credits & acknowledgments

Built as part of Project Minerva-Poppy by Lucas Kuziv and Krizia Delgado.

Standing on the shoulders of: Vygotsky's ZPD, Black & Wiliam's formative assessment work, the Anthropic Constitutional AI team, UNESCO's AI in Education guidance, and every teacher whose practice this is trying to formalize.

A consumer product called **Poppy** is built on top of these primitives. Poppy is not open source; OPAS and the Soul are.
