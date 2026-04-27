"""Pydantic request/response schemas for the FastAPI endpoints."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================================
# Session lifecycle
# ============================================================================

class SessionStartRequest(BaseModel):
    skill_id: str = Field(..., description="Skill id in the skills table.")
    # learner_id is resolved from the Bearer token — not passed in the body.
    time_budget_minutes: Optional[int] = Field(
        None,
        description="Session time budget in minutes (15/30/45/60 or null = open-ended).",
    )
    mode: str = Field(
        "auto",
        description=(
            "Instructional mode: 'auto' (pre-probe then calibrate), "
            "'teach' (explain first), 'review' (practice first, assume read)."
        ),
    )


class SessionStartResponse(BaseModel):
    session_id: str
    skill_id: str
    phase_id: str
    opening_turn: str
    yaml_refs: list[str] = Field(default_factory=list)
    memory_context: list[dict] = Field(default_factory=list)
    mode: str = "auto"


class SessionTurnRequest(BaseModel):
    session_id: str
    learner_msg: str


class SessionTurnResponse(BaseModel):
    agent_reply: str
    phase_id: str
    phase_turn_index: int
    mastery_met: bool
    yaml_refs: list[str] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    resolved_mode: Optional[str] = None


class SessionEndResponse(BaseModel):
    session_id: str
    ended_at: str
    turn_count: int


class SessionStateResponse(BaseModel):
    session_id: str
    skill_id: str
    learner_id: str
    status: Literal["active", "completed", "archived"]
    current_phase_id: Optional[str]
    phase_states: list[dict]
    recent_events: list[dict]
    resolved_mode: Optional[str] = None


class SessionProgressResponse(BaseModel):
    current_phase: str
    current_phase_index: int
    total_phases: int
    minutes_elapsed: float
    estimated_remaining_minutes: float
    probes_passed: int
    probes_total: int
    resolved_mode: Optional[str] = None


class SkillMetaResponse(BaseModel):
    skill_id: str
    name: str
    yaml: dict


# ============================================================================
# Health
# ============================================================================

class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    env: str
    claude_model: str
    embedding_model: str
