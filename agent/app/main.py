"""FastAPI entry point for the OPAS agent runtime."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import require_dev_bearer, require_learner_token
from .schemas import HealthResponse
from .config import get_settings
from .routers import session as session_router
from .routers import compare as compare_router
from .routers import dashboard as dashboard_router
from .routers import auth as auth_router
from .routers import groups as groups_router
from .routers import pedagogies as pedagogies_router
from .routers import skill_gen as skill_gen_router
from .routers import me as me_router

app = FastAPI(
    title="OPAS Agent Runtime",
    version="0.1.0",
    description=(
        "Reads an OPAS skill YAML from Supabase and runs it as a tutoring agent. "
        "Day 4-6 scope: Claude-only model adapter; 3-model adapter lands Day 7-9."
    ),
)

# CORS: allow local dev + the wizard/student frontends.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        env=s.env, claude_model=s.claude_model, embedding_model=s.embedding_model
    )


# /session/* — per-learner token OR dev bearer (require_learner_token accepts both).
app.include_router(
    session_router.router,
    prefix="/session",
    tags=["session"],
    dependencies=[Depends(require_learner_token)],
)

# /session/compare — teacher/dev only (dev bearer).
app.include_router(
    compare_router.router,
    prefix="/session",
    tags=["compare"],
    dependencies=[Depends(require_dev_bearer)],
)

# /admin/dashboard/* — read-only teacher dashboard (dev bearer).
app.include_router(
    dashboard_router.router,
    prefix="/admin/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_dev_bearer)],
)

# /auth/* — magic-link signup + callback. No auth dependency (these ARE the auth).
app.include_router(
    auth_router.router,
    prefix="/auth",
    tags=["auth"],
)

# /groups/* — study group management (learner token required).
app.include_router(
    groups_router.router,
    prefix="/groups",
    tags=["groups"],
    dependencies=[Depends(require_learner_token)],
)

# /pedagogies — public catalogue, no auth.
app.include_router(pedagogies_router.router, tags=["pedagogies"])

# /me/* — learner profile + prefs (learner token required).
app.include_router(
    me_router.router,
    prefix="/me",
    tags=["me"],
    dependencies=[Depends(require_learner_token)],
)

# /skills/* — student self-serve skill generation + lifecycle (learner token required).
app.include_router(
    skill_gen_router.router,
    prefix="/skills",
    tags=["skills"],
    dependencies=[Depends(require_learner_token)],
)
