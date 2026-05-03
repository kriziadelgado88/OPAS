"""Agent-to-agent connections — V1 of the A2A protocol.

Lets two learners' agents form a mutual handshake within a shared group:
  - GET    /connections/discoverable?group_id=X — list other agents in this
              group I could connect to (and the state of any existing
              connections with them).
  - POST   /connections/request                — create a pending request.
  - POST   /connections/{id}/accept            — flip a pending request to
              accepted. Only the recipient can call this.
  - POST   /connections/{id}/decline           — flip to declined.
  - DELETE /connections/{id}                   — revoke (either party).
  - GET    /connections/mine?skill_id=X        — list connections for one
              of my agents (any status, both directions).

V1 is discovery + state only. V2 (post-demo) wires the prompt assembler so
agents can reference their connected peers in conversation. V3 (post-demo)
adds permissioned cross-agent memory sharing.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..auth import LearnerContext, require_learner_token
from ..db import get_supabase

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_pair(skill_a: str, skill_b: str) -> tuple[str, str]:
    """Return (requester, recipient) consistently so we can de-dup queries."""
    return (skill_a, skill_b) if skill_a < skill_b else (skill_b, skill_a)


def _check_skill_ownership(skill_id: str, learner_id: str, sb) -> dict:
    """Return the skill row, or raise 403 if caller doesn't own it."""
    row = (
        sb.table("skills")
        .select("id, name, owner_learner_id, group_id, yaml")
        .eq("id", skill_id)
        .single()
        .execute()
        .data or {}
    )
    if not row:
        raise HTTPException(status_code=404, detail="Skill not found.")
    if row.get("owner_learner_id") != learner_id:
        raise HTTPException(
            status_code=403,
            detail="You can only manage connections for agents you own.",
        )
    return row


def _existing_connection(sk_a: str, sk_b: str, sb) -> Optional[dict]:
    """Return the existing row in either direction, or None."""
    rows = (
        sb.table("agent_connections")
        .select("*")
        .or_(
            f"and(requester_skill_id.eq.{sk_a},recipient_skill_id.eq.{sk_b}),"
            f"and(requester_skill_id.eq.{sk_b},recipient_skill_id.eq.{sk_a})"
        )
        .execute()
        .data or []
    )
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ConnectionRequest(BaseModel):
    my_skill_id: str
    their_skill_id: str
    group_id: Optional[str] = None


# ---------------------------------------------------------------------------
# GET /connections/discoverable?group_id=X
# Returns peers in the group(s) the caller is in, with connection state.
# ---------------------------------------------------------------------------

@router.get("/discoverable")
def discoverable(
    group_id: Optional[str] = None,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()

    # 1. Find all groups the caller belongs to (or just the requested one).
    if group_id:
        member = (
            sb.table("group_members")
            .select("group_id")
            .eq("learner_id", learner.learner_id)
            .eq("group_id", group_id)
            .execute()
            .data or []
        )
        if not member:
            raise HTTPException(status_code=403, detail="You are not in this group.")
        my_group_ids = [group_id]
    else:
        memberships = (
            sb.table("group_members")
            .select("group_id")
            .eq("learner_id", learner.learner_id)
            .execute()
            .data or []
        )
        my_group_ids = [m["group_id"] for m in memberships]

    if not my_group_ids:
        return {"groups": [], "agents": []}

    # 2. Find all skills tagged to any of my groups (excluding my own skills).
    skills = (
        sb.table("skills")
        .select("id, name, owner_learner_id, group_id, yaml")
        .in_("group_id", my_group_ids)
        .neq("owner_learner_id", learner.learner_id)
        .in_("status", ["pilot", "published"])
        .execute()
        .data or []
    )

    # 3. Find learner display info for the owners.
    owner_ids = list({s["owner_learner_id"] for s in skills})
    owners: dict[str, dict] = {}
    if owner_ids:
        rows = (
            sb.table("learner_accounts")
            .select("id, name, email, profile_prefs")
            .in_("id", owner_ids)
            .execute()
            .data or []
        )
        for r in rows:
            prefs = r.get("profile_prefs") or {}
            owners[r["id"]] = {
                "learner_id": r["id"],
                # Prefer first name; fall back to email local part for privacy
                "display_name": (r.get("name") or r.get("email", "").split("@")[0] or "Anonymous").split()[0],
                "agent_name": prefs.get("agent_name") or "",
                "agent_face": prefs.get("agent_face") or "",
                "agent_voice_id": prefs.get("agent_voice_id") or "",
            }

    # 4. Find existing connections for the caller (any direction, any status)
    #    so we can render the connection state per peer.
    my_skill_ids = [
        s["id"] for s in (
            sb.table("skills")
            .select("id")
            .eq("owner_learner_id", learner.learner_id)
            .execute()
            .data or []
        )
    ]
    existing: dict[tuple[str, str], dict] = {}
    if my_skill_ids:
        cs = (
            sb.table("agent_connections")
            .select("*")
            .or_(
                f"requester_skill_id.in.({','.join(my_skill_ids)}),"
                f"recipient_skill_id.in.({','.join(my_skill_ids)})"
            )
            .execute()
            .data or []
        )
        for c in cs:
            key = tuple(sorted([c["requester_skill_id"], c["recipient_skill_id"]]))
            existing[key] = c

    # 5. Build the discoverable list, joining skill + owner + connection state.
    agents = []
    for s in skills:
        owner_info = owners.get(s["owner_learner_id"]) or {
            "display_name": "Anonymous",
            "agent_name": "",
            "agent_face": "",
        }
        # Find which of my skills (if any) is on the same skill_id (it'd be
        # weird to connect across different skills — UI shows agents on the
        # SAME skill first). For V1 we let any-of-mine connect to any-of-theirs.
        my_matching_skill_id = None
        for ms in my_skill_ids:
            key = tuple(sorted([ms, s["id"]]))
            if key in existing:
                # If there's a connection in either direction, show that state.
                conn = existing[key]
                agents.append({
                    "skill_id": s["id"],
                    "skill_name": s["name"],
                    "their_learner_id": s["owner_learner_id"],
                    "their_display_name": owner_info["display_name"],
                    "their_agent_name": owner_info["agent_name"],
                    "their_agent_face": owner_info["agent_face"],
                    "group_id": s.get("group_id"),
                    "connection_id": conn["id"],
                    "connection_status": conn["status"],
                    "i_requested": conn["requester_learner_id"] == learner.learner_id,
                    "my_skill_id": ms,
                })
                my_matching_skill_id = ms
                break
        if my_matching_skill_id:
            continue

        # No existing connection — agent is discoverable.
        agents.append({
            "skill_id": s["id"],
            "skill_name": s["name"],
            "their_learner_id": s["owner_learner_id"],
            "their_display_name": owner_info["display_name"],
            "their_agent_name": owner_info["agent_name"],
            "their_agent_face": owner_info["agent_face"],
            "group_id": s.get("group_id"),
            "connection_id": None,
            "connection_status": None,
            "i_requested": False,
            "my_skill_id": None,
        })

    return {"groups": my_group_ids, "agents": agents}


# ---------------------------------------------------------------------------
# POST /connections/request
# ---------------------------------------------------------------------------

@router.post("/request", status_code=status.HTTP_201_CREATED)
def request_connection(
    body: ConnectionRequest,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()

    # Verify ownership of my_skill
    my_skill = _check_skill_ownership(body.my_skill_id, learner.learner_id, sb)

    # Verify their_skill exists and get its owner
    their_skill = (
        sb.table("skills")
        .select("id, name, owner_learner_id, group_id")
        .eq("id", body.their_skill_id)
        .single()
        .execute()
        .data or {}
    )
    if not their_skill:
        raise HTTPException(status_code=404, detail="Target agent not found.")
    if their_skill["owner_learner_id"] == learner.learner_id:
        raise HTTPException(status_code=400, detail="Cannot connect to your own agent.")

    # If a group_id was passed, verify both learners are members.
    # If not passed, default to a shared group between requester and recipient.
    shared_group = body.group_id
    if not shared_group:
        my_groups = {m["group_id"] for m in
            (sb.table("group_members").select("group_id")
                .eq("learner_id", learner.learner_id).execute().data or [])}
        their_groups = {m["group_id"] for m in
            (sb.table("group_members").select("group_id")
                .eq("learner_id", their_skill["owner_learner_id"]).execute().data or [])}
        shared = my_groups & their_groups
        if not shared:
            raise HTTPException(
                status_code=403,
                detail="You don't share a group with that learner. Join a group together first.",
            )
        shared_group = next(iter(shared))

    # Reject duplicates (any direction, any status)
    existing = _existing_connection(body.my_skill_id, body.their_skill_id, sb)
    if existing:
        return {
            "connection_id": existing["id"],
            "status": existing["status"],
            "already_existed": True,
        }

    # Create the request
    inserted = (
        sb.table("agent_connections")
        .insert({
            "requester_skill_id": body.my_skill_id,
            "recipient_skill_id": body.their_skill_id,
            "requester_learner_id": learner.learner_id,
            "recipient_learner_id": their_skill["owner_learner_id"],
            "status": "pending",
            "group_id": shared_group,
        })
        .execute()
        .data or []
    )
    if not inserted:
        raise HTTPException(status_code=500, detail="Failed to create connection request.")

    return {
        "connection_id": inserted[0]["id"],
        "status": "pending",
        "already_existed": False,
    }


# ---------------------------------------------------------------------------
# POST /connections/{connection_id}/accept
# ---------------------------------------------------------------------------

@router.post("/{connection_id}/accept")
def accept_connection(
    connection_id: str,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()

    row = (
        sb.table("agent_connections")
        .select("*")
        .eq("id", connection_id)
        .single()
        .execute()
        .data or {}
    )
    if not row:
        raise HTTPException(status_code=404, detail="Connection not found.")
    if row["recipient_learner_id"] != learner.learner_id:
        raise HTTPException(
            status_code=403,
            detail="Only the recipient of a connection request can accept it.",
        )
    if row["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot accept a {row['status']} connection.",
        )

    updated = (
        sb.table("agent_connections")
        .update({"status": "accepted", "responded_at": "now()"})
        .eq("id", connection_id)
        .execute()
        .data or []
    )
    return {"connection_id": connection_id, "status": "accepted"}


# ---------------------------------------------------------------------------
# POST /connections/{connection_id}/decline
# ---------------------------------------------------------------------------

@router.post("/{connection_id}/decline")
def decline_connection(
    connection_id: str,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()
    row = (
        sb.table("agent_connections")
        .select("*")
        .eq("id", connection_id)
        .single()
        .execute()
        .data or {}
    )
    if not row:
        raise HTTPException(status_code=404, detail="Connection not found.")
    if row["recipient_learner_id"] != learner.learner_id:
        raise HTTPException(status_code=403, detail="Only the recipient can decline.")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot decline a {row['status']} connection.")
    sb.table("agent_connections").update(
        {"status": "declined", "responded_at": "now()"}
    ).eq("id", connection_id).execute()
    return {"connection_id": connection_id, "status": "declined"}


# ---------------------------------------------------------------------------
# DELETE /connections/{connection_id}  (either party can revoke)
# ---------------------------------------------------------------------------

@router.delete("/{connection_id}")
def delete_connection(
    connection_id: str,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()
    row = (
        sb.table("agent_connections")
        .select("requester_learner_id, recipient_learner_id")
        .eq("id", connection_id)
        .single()
        .execute()
        .data or {}
    )
    if not row:
        raise HTTPException(status_code=404, detail="Connection not found.")
    if learner.learner_id not in (row["requester_learner_id"], row["recipient_learner_id"]):
        raise HTTPException(status_code=403, detail="Not your connection.")
    sb.table("agent_connections").delete().eq("id", connection_id).execute()
    return {"connection_id": connection_id, "deleted": True}


# ---------------------------------------------------------------------------
# GET /connections/mine?skill_id=X
# ---------------------------------------------------------------------------

@router.get("/mine")
def my_connections(
    skill_id: Optional[str] = None,
    learner: LearnerContext = Depends(require_learner_token),
) -> list[dict]:
    sb = get_supabase()

    # Find all skills owned by this learner (or filter to one)
    q = sb.table("skills").select("id").eq("owner_learner_id", learner.learner_id)
    if skill_id:
        q = q.eq("id", skill_id)
    my_skill_ids = [s["id"] for s in (q.execute().data or [])]
    if not my_skill_ids:
        return []

    rows = (
        sb.table("agent_connections")
        .select("*")
        .or_(
            f"requester_skill_id.in.({','.join(my_skill_ids)}),"
            f"recipient_skill_id.in.({','.join(my_skill_ids)})"
        )
        .execute()
        .data or []
    )
    return rows
