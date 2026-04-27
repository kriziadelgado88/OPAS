# OPAS — Local Testing Setup

**Prerequisites:** Python 3.11+, git

---

## 1. Clone & install

```bash
git clone <repo-url> opas
cd opas/agent
python -m venv .venv

# Windows
.venv\Scripts\activate
# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## 2. Add secrets

Create `agent/.env` (Lucas will send this file — do not commit it):

```
SUPABASE_URL=https://bhpfpespvjiolsglymbk.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<from Lucas>
ANTHROPIC_API_KEY=<from Lucas>
OPENAI_API_KEY=<from Lucas>
OPAS_ENV=dev
OPAS_DEV_BEARER_TOKEN=dev-token-change-me
CLAUDE_MODEL=claude-sonnet-4-6
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
```

## 3. Start the backend

```bash
# from agent/
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8001
# Mac/Linux: .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Confirm it's up: http://localhost:8001/health

## 4. Start the frontend

Open a second terminal:

```bash
# from app/
python -m http.server 8080
```

## 5. Open the student UI

**Standard (student view):**
http://localhost:8080/opas-student.html?skill_id=hks.iga250.week2.emerging-technologies

**Demo mode (shows live YAML highlights):**
http://localhost:8080/opas-student.html?skill_id=hks.iga250.week2.emerging-technologies&demo=1

Also works with the API 318 skill:
http://localhost:8080/opas-student.html?skill_id=hks.api318.unit1.thinking-probabilistically&demo=1

---

## What to test

- [ ] Session starts with a Socratic question (not a lecture)
- [ ] In demo mode: YAML pane populates on load; lines highlight after each turn
- [ ] Citations appear in agent replies (`[source, §section]` format)
- [ ] Off-topic question → polite refusal + `refuse_if_ungrounded` highlights in demo mode
- [ ] "Just give me the answer" → agent stays Socratic
- [ ] End session button works

## Wizard UI (skill authoring, optional)

http://localhost:8080/opas-wizard.html
