#!/usr/bin/env python3
"""Mint per-learner tokens for classmate testing.

Usage:
  python scripts/mint_learner_tokens.py "Alice:alice@hks.edu" "Bob:bob@hks.edu"

Or edit the CLASSMATES list below and run without args.

Each classmate gets a unique URL printed to stdout. Email them their line.
The student page extracts ?token= from the URL, stores it in localStorage,
and strips it from the URL bar.

Prerequisite: sessions.learner_id must FK-reference learner_accounts(id), not profiles(id).
Run the migration SQL in _deliverables/ws1_sessions_fk_migration.sql before using this script.
"""
from __future__ import annotations

import secrets
import sys
import uuid
from pathlib import Path

# Allow running from agent/ or agent/scripts/
AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))

from app.db import get_supabase  # noqa: E402 — needs sys.path fix above

SKILL_ID = "hks.iga250.week2.emerging-technologies"
STUDENT_BASE = "http://localhost:8080/opas-student.html"
TOKEN_LABEL = "classmate-apr-cohort"

# Edit this list if you prefer not to pass CLI args.
CLASSMATES: list[tuple[str, str | None]] = [
    # ("Alice Smith", "alice@hks.harvard.edu"),
    # ("Bob Jones",   "bob@hks.harvard.edu"),
]


def mint(name: str, email: str | None = None) -> str:
    supabase = get_supabase()

    learner_id = str(uuid.uuid4())
    supabase.table("learner_accounts").insert(
        {"id": learner_id, "name": name, "email": email}
    ).execute()

    token = secrets.token_urlsafe(32)
    supabase.table("learner_tokens").insert(
        {"token": token, "learner_id": learner_id, "label": TOKEN_LABEL}
    ).execute()

    url = f"{STUDENT_BASE}?skill_id={SKILL_ID}&token={token}"
    display_email = f"<{email}>" if email else ""
    print(f"{name} {display_email} -> {url}")
    return token


def main() -> None:
    pairs: list[tuple[str, str | None]] = []

    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if ":" in arg:
                name, email = arg.split(":", 1)
                pairs.append((name.strip(), email.strip() or None))
            else:
                pairs.append((arg.strip(), None))
    elif CLASSMATES:
        pairs = list(CLASSMATES)
    else:
        print(
            "Usage: python scripts/mint_learner_tokens.py 'Alice:alice@hks.edu' 'Bob:bob@hks.edu'\n"
            "Or edit the CLASSMATES list in this file."
        )
        sys.exit(1)

    for name, email in pairs:
        mint(name, email)


if __name__ == "__main__":
    main()
