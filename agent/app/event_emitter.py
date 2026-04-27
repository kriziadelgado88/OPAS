"""Write xAPI-style events to the events table."""
from __future__ import annotations

from supabase import Client


def emit(
    *,
    verb: str,
    actor_id: str,
    session_id: str,
    skill_id: str,
    object_type: str,
    object_id: str,
    context: dict,
    result: dict,
    supabase: Client,
) -> None:
    supabase.table("events").insert(
        {
            "verb": verb,
            "actor_id": actor_id,
            "session_id": session_id,
            "skill_id": skill_id,
            "object_type": object_type,
            "object_id": object_id,
            "context": context,
            "result": result,
        }
    ).execute()
