"""In-memory session state store.

Single-process uvicorn only — fine for Days 4-6.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException


@dataclass
class SessionState:
    skill_id: str
    learner_id: str
    skill: dict
    current_phase_index: int
    phase_turn_index: int
    messages: list[dict]
    session_db_id: str
    pending_probe: Optional[dict] = None
    constitution: Optional[dict] = None
    constitution_id: Optional[str] = None
    distress_cooldown_until: Optional[float] = None
    consecutive_failures: int = 0
    memory_context: list[dict] = field(default_factory=list)
    time_budget_minutes: Optional[int] = None
    mode: str = "auto"
    resolved_mode: Optional[str] = None
    skill_group_id: Optional[str] = None


_store: dict[str, SessionState] = {}


def get_state(session_id: str) -> SessionState:
    state = _store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    return state


def set_state(session_id: str, state: SessionState) -> None:
    _store[session_id] = state
