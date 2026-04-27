"""Per-turn RAG retrieval via pgvector HNSW index.

Corpus ingestion lives in scripts/ingest_corpus.py (CLI).
This module only queries existing corpus_chunks rows.
"""
from __future__ import annotations

from openai import OpenAI
from supabase import Client

from .config import Settings


def _embed(text: str, settings: Settings) -> list[float]:
    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.embeddings.create(model=settings.embedding_model, input=text)
    return resp.data[0].embedding


def retrieve_chunks(
    query: str,
    skill_id: str,
    grounding_policy: dict,
    supabase: Client,
    settings: Settings,
) -> list[dict]:
    """Return corpus chunks above min_similarity threshold.

    Returns [] if nothing passes. Caller handles refuse_if_ungrounded.
    """
    query_embedding = _embed(query, settings)
    result = supabase.rpc(
        "match_corpus_chunks",
        {
            "query_embedding": query_embedding,
            "p_skill_id": skill_id,
            "match_threshold": grounding_policy.get("min_similarity", 0.72),
            "match_count": 5,
        },
    ).execute()
    return result.data or []
