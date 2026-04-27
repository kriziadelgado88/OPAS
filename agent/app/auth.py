"""Auth dependencies shared across routers.

Two tiers:
- require_learner_token: resolves a per-classmate token (or dev bearer) to a LearnerContext.
  Applied to /session/* routes.
- require_dev_bearer: validates only the dev bearer. Applied to /admin/* routes.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .db import get_supabase

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Bearer token — per-learner or dev bearer.",
)

DEV_LEARNER_ID = "00000000-0000-0000-0000-000000000001"


@dataclass
class LearnerContext:
    learner_id: str
    name: str


def require_learner_token(
    creds: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> LearnerContext:
    """Resolve a bearer token to a LearnerContext.

    Priority:
    1. Matches OPAS_DEV_BEARER_TOKEN → synthetic dev learner (backwards-compat).
    2. Matches a row in learner_tokens with revoked_at IS NULL → real learner.
    3. Otherwise → 401.
    """
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
        )

    token = creds.credentials
    settings = get_settings()

    if token == settings.dev_bearer_token:
        return LearnerContext(learner_id=DEV_LEARNER_ID, name="dev")

    supabase = get_supabase()
    rows = (
        supabase.table("learner_tokens")
        .select("learner_id")
        .eq("token", token)
        .is_("revoked_at", "null")
        .execute()
    )
    if not rows.data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked learner token.",
        )

    learner_id = rows.data[0]["learner_id"]
    account = (
        supabase.table("learner_accounts")
        .select("name")
        .eq("id", learner_id)
        .single()
        .execute()
    )
    name = account.data["name"] if account.data else "unknown"
    return LearnerContext(learner_id=str(learner_id), name=name)


def require_dev_bearer(
    creds: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    """Validate that the request carries the dev/teacher bearer token.

    Used for admin and dashboard routes — classmates cannot access these.
    """
    expected = get_settings().dev_bearer_token
    if (
        creds is None
        or creds.scheme.lower() != "bearer"
        or creds.credentials != expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token (admin-only route).",
        )
