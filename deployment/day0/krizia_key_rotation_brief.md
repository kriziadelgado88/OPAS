# Brief for Krizia — Rotate & refactor API keys in the HTML prototypes

**From:** Lucas  **Date:** April 20, 2026  **Priority:** Blocker for Demo Day build.
**Time estimate:** 60–90 min total.

---

## Why this matters

Our current `poppy-builder.html` and `poppy-student.html` prototypes have **three live API keys hardcoded into client-side JavaScript** — Anthropic, Google, and ElevenLabs. These files have been shared around; the keys must be treated as compromised. Until they're rotated and moved out of the client, we cannot share the prototypes with Levy, faculty, or any pilot student without risking real cost exposure and potential abuse.

This is Day-0 of our PoC build sequence. Nothing else ships safely until it's done.

---

## What I need you to do

### Step 1 — Locate every key in the repo (10 min)

Open the folder containing the HTML prototypes and run, from its root:

```bash
grep -rEin "sk-ant-|sk-[a-z0-9]{20,}|AIza|xi-api-key|elevenlabs" .
```

That regex catches Anthropic (`sk-ant-...`), generic OpenAI-style (`sk-...`), Google (`AIza...`), and ElevenLabs patterns. Make a list of every hit — file + line number.

If you find keys in anything other than HTML (e.g., stray `.env`, JSON, or markdown), flag those too.

### Step 2 — Rotate each key at the provider (20 min)

Rotate *before* you touch the code, so the old values become dead as soon as possible.

- **Anthropic Console** → https://console.anthropic.com/settings/keys → revoke the old key, create a new one, copy it once (it's only shown once), paste into a secure note.
- **Google AI Studio / Cloud Console** → https://aistudio.google.com/app/apikey (or the GCP project that owns it) → delete old key, create new.
- **ElevenLabs** → https://elevenlabs.io/app/settings/api-keys → revoke and regenerate.

Save the three new values in a password manager (1Password / Bitwarden), **not** in plaintext in the repo.

### Step 3 — Refactor the HTML to stop holding keys client-side (30 min)

The prototypes were client-only, which is why the keys ended up in the HTML. For the PoC we'll route LLM calls through our backend (FastAPI, coming in workstream 1). But we shouldn't block on that.

**Interim pattern:** add a small proxy endpoint for now (even a single-file Node or Python script, or a Supabase Edge Function), and change the HTMLs to call `/api/claude`, `/api/gemini`, `/api/tts` instead of the provider URLs directly. The proxy reads the keys from environment variables and injects them server-side.

Minimum changes to each HTML file:

1. Remove every literal key.
2. Replace `fetch("https://api.anthropic.com/...", { headers: { "x-api-key": "sk-ant-..." } })` with `fetch("/api/claude", { ... })` (same payload, no key header).
3. Same pattern for Gemini and ElevenLabs.
4. Add a `<!-- SECURITY: all LLM calls routed through /api proxy; keys server-side only -->` comment near the top of each file.

If setting up a proxy today is too much scope, the **minimum acceptable fallback** is: remove the keys from HTML entirely and leave the fetch calls broken, with a `TODO: wire to backend proxy` comment. Better to have obviously-broken prototypes than live secrets in shared files.

### Step 4 — Add `.env.example` + `.gitignore` hygiene (10 min)

In the prototype repo root:

1. Create `.env.example` with **names only, no values**:
   ```
   ANTHROPIC_API_KEY=
   GOOGLE_API_KEY=
   ELEVENLABS_API_KEY=
   ```
2. Ensure `.env` is in `.gitignore`. If it isn't, add it.
3. If any `.env` file was ever committed with real keys, check `git log -- .env` — if the answer is yes, tell me immediately; we'll need to purge history.

### Step 5 — Git hygiene (15 min)

```bash
git log -p | grep -Ei "sk-ant-|AIza|xi-api-key|elevenlabs" | head
```

If that returns nothing, you're clean — just commit your refactor on a branch `day0/rotate-keys`, push, and open a PR I can review.

If it returns hits, **do not just commit over the top** — the old keys are in history and remain findable. In that case stop, message me, and we'll use `git filter-repo` or BFG to scrub them. The rotation you did in Step 2 already neutralises the risk, but repo hygiene still matters for reputation and compliance.

---

## Definition of done

- Zero literal API keys in any file in the repo.
- Three new keys live in a password manager; old ones revoked at all three providers.
- `.env.example` present; `.env` git-ignored.
- Either a working proxy pattern or clearly-marked TODOs pointing at `/api/*`.
- Branch pushed, PR open, me tagged as reviewer.

## Do NOT

- Paste the new keys into Slack, email, or any chat — even a DM. Password manager only.
- Commit a real `.env`. Only `.env.example`.
- Skip Step 5 (git history check). A rotated key you committed yesterday is still worth scrubbing.

Ping me when Step 2 is done — that's the moment the risk is actually contained. The rest is hygiene.

— L.
