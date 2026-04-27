"""Learner self-service profile endpoints.

GET  /me                  → {learner_id, name, email, profile_prefs}
PUT  /me/profile_prefs    → merges supplied fields into existing jsonb, returns updated prefs

All routes require require_learner_token (applied at router level in main.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..auth import LearnerContext, require_learner_token
from ..db import get_supabase

router = APIRouter()


class ProfilePrefsUpdate(BaseModel):
    interests: list[str] | None = None
    language: str | None = None
    bandwidth: str | None = None   # "low" | "high"
    timezone: str | None = None


@router.get("")
def get_me(learner: LearnerContext = Depends(require_learner_token)) -> dict:
    sb = get_supabase()
    row = (
        sb.table("learner_accounts")
        .select("id, name, email, profile_prefs")
        .eq("id", learner.learner_id)
        .single()
        .execute()
        .data or {}
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learner not found.")
    return {
        "learner_id": row["id"],
        "name": row.get("name"),
        "email": row.get("email"),
        "profile_prefs": row.get("profile_prefs") or {},
    }


@router.put("/profile_prefs")
def update_profile_prefs(
    body: ProfilePrefsUpdate,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    """Merge supplied fields into the existing profile_prefs jsonb.

    Only the provided fields are updated; omitted fields are left unchanged.
    """
    sb = get_supabase()

    row = (
        sb.table("learner_accounts")
        .select("profile_prefs")
        .eq("id", learner.learner_id)
        .single()
        .execute()
        .data or {}
    )
    current = dict(row.get("profile_prefs") or {})

    updates = body.model_dump(exclude_none=True)
    current.update(updates)

    sb.table("learner_accounts").update({"profile_prefs": current}).eq("id", learner.learner_id).execute()
    return current
