"""Supabase client singleton.

Uses the service_role key — this bypasses RLS. The FastAPI app layer is the
access-control surface; don't expose this client anywhere a non-server caller
could reach.
"""
from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from .config import get_settings


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)
