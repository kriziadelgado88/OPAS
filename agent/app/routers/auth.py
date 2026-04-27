"""Self-serve magic-link auth endpoints.

These routes carry NO auth dependency — they ARE the auth.
Wired in main.py under prefix="/auth" with no Depends().

Flow:
  POST /auth/signup  → sends magic link email via Supabase OTP.
  POST /auth/callback → verifies Supabase access_token, upserts learner_accounts,
                        mints a row in learner_tokens, returns our opaque bearer.

Design note: learner_accounts is created (or confirmed) at callback time, not
signup time, because sign_in_with_otp does not return the user UUID until the
magic link is clicked. This is correct: application records should not exist
before email is verified.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ..db import get_supabase

router = APIRouter()

MAGIC_LINK_REDIRECT = "http://localhost:8080/opas-auth-callback.html"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: str
    name: str


class CallbackRequest(BaseModel):
    access_token: str


# ---------------------------------------------------------------------------
# POST /auth/signup
# ---------------------------------------------------------------------------

@router.post("/signup")
def signup(req: SignupRequest) -> dict:
    """Send a magic-link email to the given address.

    Returns {sent: true} immediately. The learner_accounts row is created
    when the magic link is clicked and /auth/callback is called.
    """
    sb = get_supabase()
    try:
        sb.auth.sign_in_with_otp({
            "email": req.email,
            "options": {
                "data": {"name": req.name},          # stored in user_metadata
                "email_redirect_to": MAGIC_LINK_REDIRECT,
            },
        })
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"sent": True}


# ---------------------------------------------------------------------------
# POST /auth/callback
# ---------------------------------------------------------------------------

@router.post("/callback")
def callback(req: CallbackRequest) -> dict:
    """Exchange a Supabase access_token for an OPAS learner bearer token.

    Verifies the JWT, upserts learner_accounts, mints a fresh learner_tokens row.
    Returns {token, learner_id, name} — token is the opaque bearer for /session/*.
    """
    sb = get_supabase()

    # Verify the JWT with Supabase
    try:
        user_resp = sb.auth.get_user(req.access_token)
        user = user_resp.user
        if not user:
            raise ValueError("empty user in response")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid access token: {exc}",
        )

    auth_id = str(user.id)
    email = user.email or ""
    name = (user.user_metadata or {}).get("name") or email.split("@")[0]

    # Upsert learner_accounts — idempotent on repeat clicks
    sb.table("learner_accounts").upsert(
        {"id": auth_id, "name": name, "email": email},
        on_conflict="id",
    ).execute()

    # Mint a fresh opaque learner token
    token = secrets.token_urlsafe(32)
    sb.table("learner_tokens").insert({
        "token": token,
        "learner_id": auth_id,
        "label": "magic-link",
    }).execute()

    return {"token": token, "learner_id": auth_id, "name": name}
