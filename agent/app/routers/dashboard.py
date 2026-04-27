"""Read-only teacher dashboard endpoints.

All routes require the dev/teacher bearer (require_dev_bearer).
No write operations — every action here is a SELECT.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..db import get_supabase
from ..session_store import _store

router = APIRouter()


# ---------------------------------------------------------------------------
# Skills list (populates the dropdown in the dashboard UI)
# ---------------------------------------------------------------------------

@router.get("/skills")
def list_skills() -> list[dict]:
    """Teacher-authored skills only (owner_learner_id IS NULL + pilot/published).

    Student-generated skills have owner_learner_id set and are excluded so the
    teacher dashboard dropdown stays clean for demo day.
    """
    sb = get_supabase()
    rows = (
        sb.table("skills")
        .select("id, yaml, status")
        .in_("status", ["pilot", "published"])
        .is_("owner_learner_id", "null")
        .execute()
        .data or []
    )
    return [
        {
            "skill_id": r["id"],
            "name": (r.get("yaml") or {}).get("skill", {}).get("name", r["id"]),
            "status": r["status"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Sessions list for a skill
# ---------------------------------------------------------------------------

@router.get("/skills/{skill_id}/sessions")
def list_sessions(skill_id: str) -> list[dict]:
    """Sessions for a teacher-authored skill, newest first.

    Returns 404 if the skill is student-generated (owner_learner_id IS NOT NULL)
    so the teacher dashboard cannot be used to inspect student-private agents.
    """
    sb = get_supabase()

    skill_row = (
        sb.table("skills")
        .select("owner_learner_id")
        .eq("id", skill_id)
        .single()
        .execute()
        .data or {}
    )
    if skill_row.get("owner_learner_id"):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Skill not found in teacher dashboard.")

    sessions = (
        sb.table("sessions")
        .select("id, learner_id, status, started_at, completed_at, last_activity")
        .eq("skill_id", skill_id)
        .order("started_at", desc=True)
        .execute()
        .data or []
    )
    if not sessions:
        return []

    # Learner names — one batch query
    learner_ids = list({s["learner_id"] for s in sessions if s.get("learner_id")})
    accounts: dict[str, str] = {}
    if learner_ids:
        acct_rows = (
            sb.table("learner_accounts")
            .select("id, name")
            .in_("id", learner_ids)
            .execute()
            .data or []
        )
        accounts = {r["id"]: r["name"] for r in acct_rows}

    result = []
    for s in sessions:
        sid = s["id"]

        # Turn count = number of "responded" events (one per learner message)
        resp_events = (
            sb.table("events")
            .select("id")
            .eq("session_id", sid)
            .eq("verb", "responded")
            .execute()
            .data or []
        )

        # Pass rate from probe_attempts
        probes = (
            sb.table("probe_attempts")
            .select("score")
            .eq("session_id", sid)
            .execute()
            .data or []
        )
        pass_rate = None
        if probes:
            passed = sum(
                1 for p in probes
                if p.get("score") is not None and float(p["score"]) >= 0.5
            )
            pass_rate = round(passed / len(probes), 2)

        result.append({
            "session_id": sid,
            "learner_name": accounts.get(s.get("learner_id", ""), "unknown"),
            "status": s["status"],
            "started_at": s["started_at"],
            "completed_at": s.get("completed_at"),
            "turn_count": len(resp_events),
            "pass_rate": pass_rate,
            "last_activity_at": s.get("last_activity") or s["started_at"],
        })

    return result


# ---------------------------------------------------------------------------
# Full session detail
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}")
def session_detail(session_id: str) -> dict:
    """All data for one session: meta, events, probe attempts, memories, turns."""
    sb = get_supabase()

    # Session row
    sess = (
        sb.table("sessions")
        .select("id, learner_id, skill_id, status, started_at, completed_at")
        .eq("id", session_id)
        .single()
        .execute()
        .data or {}
    )

    learner_id = sess.get("learner_id", "")
    acct = (
        sb.table("learner_accounts")
        .select("name")
        .eq("id", learner_id)
        .single()
        .execute()
        .data or {}
    )
    learner_name = acct.get("name", "unknown")

    # Events — all, reverse-chronological
    events = (
        sb.table("events")
        .select("id, verb, object_type, object_id, context, result, occurred_at")
        .eq("session_id", session_id)
        .order("occurred_at", desc=True)
        .execute()
        .data or []
    )

    # Probe attempts
    probe_attempts = (
        sb.table("probe_attempts")
        .select("id, phase_id, probe_id, score, scorer, occurred_at")
        .eq("session_id", session_id)
        .order("occurred_at")
        .execute()
        .data or []
    )

    # Memories written during this session
    memories = (
        sb.table("learner_memories")
        .select("category, memory_text, created_at")
        .eq("session_id", session_id)
        .order("created_at")
        .execute()
        .data or []
    )

    # Conversation turns — from in-memory store if the session is still live
    turns: list[dict] = []
    state = _store.get(session_id)
    if state:
        # Skip the synthetic "ready" opener message
        turns = [
            {"role": m["role"], "content": m["content"]}
            for m in state.messages
            if not (m["role"] == "user" and m["content"] == "ready")
        ]

    return {
        "session_meta": {
            "session_id": session_id,
            "learner_name": learner_name,
            "skill_id": sess.get("skill_id"),
            "status": sess.get("status"),
            "started_at": sess.get("started_at"),
            "completed_at": sess.get("completed_at"),
        },
        "events": events,
        "probe_attempts": probe_attempts,
        "memories": memories,
        "turns": turns,
    }
