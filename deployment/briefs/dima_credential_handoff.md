# Credential handoff — Dima (do this Apr 22 AM)

Dima needs credentials before his Day-4 brief can start. ~10 minutes total.

## 1. Invite him to Supabase as a Developer

- Supabase dashboard → **Team** (bottom-left) → **Invite member**.
- Email: Dima's work email.
- Role: **Developer** (NOT Owner). Developer gives him DB + SQL Editor + Table Editor access without Billing/Team-management reach.

## 2. Share `.env` via 1Password (not Slack, not email)

- 1Password → **Shared vault** (create "OPAS dev secrets" if it doesn't exist).
- Add a **Secure Note** titled `OPAS .env (dev, Apr 21 2026)`.
- Paste the full contents of `deployment/supabase/.env`.
- Share access with Dima's 1Password email.

Why not Slack: Slack retains message history server-side and messages are searchable by admins / ex-admins. 1Password shared vaults are end-to-end encrypted and access-controlled.

## 3. Send him three links (Slack or email is fine — no secrets in the message)

- Supabase dashboard URL: `https://bhpfpespvjiolsglymbk.supabase.co` (+ a link to the project dashboard page he just got invited to).
- His brief: `deployment/briefs/dima_agent_brief.md`.
- Unit 1 YAML: `skills/api-318-unit-1/skill.opas.yaml`.

## 4. One-line Slack message suggestion

> "Hey Dima — invited you to the Supabase project (`opas-poc` / Project Minerva), .env is in our shared 1Password vault ("OPAS dev secrets"), brief is in the repo at `deployment/briefs/dima_agent_brief.md`. Daily 10-min sync starting tomorrow work for you?"

## 5. What NOT to do

- Don't paste the Service Role Key or DB password into Slack, email, Notion, or any chat — even briefly. Treat them like real production secrets from day one, because they already are.
- Don't send him the Anthropic/OpenAI/Google keys until Krizia's rotation work is done. The current keys in `.env` are still the ones baked into HTMLs. If a key leaks, the blast radius doubles for every person who has them.
