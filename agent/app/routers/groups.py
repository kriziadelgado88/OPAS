"""Study group endpoints.

All routes sit behind require_learner_token (applied at router level in main.py).
Each endpoint also injects LearnerContext directly to read learner_id.

Group memory purge policy (default-on per product model):
  Leaving a group deletes the leaving learner's learner_memories rows that are
  tagged with that group_id. Other members' memories are untouched.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..auth import LearnerContext, require_learner_token
from ..db import get_supabase

router = APIRouter()

INVITE_URL_BASE = "http://localhost:8080/opas-group.html?invite="


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateGroupRequest(BaseModel):
    name: str


class JoinGroupRequest(BaseModel):
    invite_code: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _member_count(group_id: str, sb) -> int:
    result = (
        sb.table("group_members")
        .select("learner_id", count="exact")
        .eq("group_id", group_id)
        .execute()
    )
    return result.count or 0


# ---------------------------------------------------------------------------
# POST /groups — create a group
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED)
def create_group(
    req: CreateGroupRequest,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()

    # Generate an 8-char URL-safe invite code
    invite_code = secrets.token_urlsafe(6)[:8]

    grp = (
        sb.table("groups")
        .insert({
            "name": req.name,
            "owner_learner_id": learner.learner_id,
            "invite_code": invite_code,
        })
        .execute()
        .data[0]
    )
    group_id = grp["id"]

    # Auto-add owner as first member
    sb.table("group_members").insert(
        {"group_id": group_id, "learner_id": learner.learner_id}
    ).execute()

    return {
        "group_id": group_id,
        "invite_code": invite_code,
        "invite_url": f"{INVITE_URL_BASE}{invite_code}",
    }


# ---------------------------------------------------------------------------
# POST /groups/join — join via invite code (idempotent)
# ---------------------------------------------------------------------------

@router.post("/join")
def join_group(
    req: JoinGroupRequest,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()

    rows = (
        sb.table("groups")
        .select("id, name")
        .eq("invite_code", req.invite_code)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No group found for invite code '{req.invite_code}'.",
        )

    grp = rows[0]
    group_id = grp["id"]

    # Idempotent upsert on composite PK (group_id, learner_id)
    sb.table("group_members").upsert(
        {"group_id": group_id, "learner_id": learner.learner_id},
        on_conflict="group_id,learner_id",
    ).execute()

    return {
        "group_id": group_id,
        "name": grp["name"],
        "member_count": _member_count(group_id, sb),
    }


# ---------------------------------------------------------------------------
# GET /groups/mine — list groups the caller belongs to
# ---------------------------------------------------------------------------

@router.get("/mine")
def my_groups(
    learner: LearnerContext = Depends(require_learner_token),
) -> list[dict]:
    sb = get_supabase()

    memberships = (
        sb.table("group_members")
        .select("group_id")
        .eq("learner_id", learner.learner_id)
        .execute()
        .data or []
    )
    if not memberships:
        return []

    group_ids = [m["group_id"] for m in memberships]
    groups = (
        sb.table("groups")
        .select("id, name, owner_learner_id")
        .in_("id", group_ids)
        .execute()
        .data or []
    )

    return [
        {
            "group_id": g["id"],
            "name": g["name"],
            "role": "owner" if g["owner_learner_id"] == learner.learner_id else "member",
            "member_count": _member_count(g["id"], sb),
        }
        for g in groups
    ]


# ---------------------------------------------------------------------------
# POST /groups/{group_id}/leave — leave and purge shared memories
# ---------------------------------------------------------------------------

@router.post("/{group_id}/leave")
def leave_group(
    group_id: str,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()

    # Purge caller's memories that are tagged to this group
    purged = (
        sb.table("learner_memories")
        .delete()
        .eq("learner_id", learner.learner_id)
        .eq("group_id", group_id)
        .execute()
        .data or []
    )

    # Remove from group_members
    sb.table("group_members").delete()\
        .eq("group_id", group_id)\
        .eq("learner_id", learner.learner_id)\
        .execute()

    return {"left": True, "memories_purged": len(purged)}
