"""Load an OPAS skill YAML from Supabase."""
from __future__ import annotations

from fastapi import HTTPException
from supabase import Client


def load_skill(skill_id: str, supabase: Client) -> dict:
    """Return {'yaml': {...}, 'version': '...', 'status': '...'}.

    No caching — the wizard may re-emit at any time.
    """
    result = (
        supabase.table("skills")
        .select("yaml,version,status")
        .eq("id", skill_id)
        .single()
        .execute()
    )
    row = result.data
    if not row or row["status"] not in ("pilot", "published"):
        raise HTTPException(
            status_code=404,
            detail=f"Skill {skill_id!r} not found or not available (status must be pilot/published)",
        )
    return row
