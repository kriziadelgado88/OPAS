"""Central configuration.

Loads .env, exposes settings as a singleton. Fail fast on missing secrets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env from (in order): agent/.env, then repo-level deployment/supabase/.env
AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parent
load_dotenv(AGENT_DIR / ".env", override=False)
load_dotenv(REPO_ROOT / "deployment" / "supabase" / ".env", override=False)


def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(
            f"Missing required env var: {name}. "
            f"Check agent/.env or deployment/supabase/.env."
        )
    return v


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Supabase
    supabase_url: str
    supabase_service_role_key: str

    # LLM
    anthropic_api_key: str
    openai_api_key: str
    google_api_key: str
    gemini_api_key: str | None

    # Runtime
    env: str
    dev_bearer_token: str
    claude_model: str
    openai_chat_model: str
    gemini_model: str
    embedding_model: str
    embedding_dim: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        supabase_url=_require("SUPABASE_URL"),
        supabase_service_role_key=_require("SUPABASE_SERVICE_ROLE_KEY"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        openai_api_key=_require("OPENAI_API_KEY"),
        google_api_key=_optional("GOOGLE_API_KEY"),
        gemini_api_key=_optional("GEMINI_API_KEY") or None,
        env=_optional("OPAS_ENV", "dev"),
        dev_bearer_token=_optional("OPAS_DEV_BEARER_TOKEN", "dev-token-change-me"),
        claude_model=_optional("CLAUDE_MODEL", "claude-sonnet-4-6"),
        openai_chat_model=_optional("OPENAI_CHAT_MODEL", "gpt-4o"),
        gemini_model=_optional("GEMINI_MODEL", "gemini-2.5-flash"),
        embedding_model=_optional("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dim=int(_optional("EMBEDDING_DIM", "1536")),
    )
