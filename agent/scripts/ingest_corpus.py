"""Corpus ingestion CLI.

Reads PDFs from Supabase Storage (storage_path) or local disk (local_path),
chunks them, embeds with OpenAI, and upserts to corpus_chunks.
Run once per skill before starting any sessions.

Usage:
    python scripts/ingest_corpus.py --skill-id hks.api318.unit1.thinking-probabilistically

Source resolution order (first match wins):
  1. storage_path — bucket-relative path in Supabase Storage "corpus" bucket.
                    Format: "{skill_id}/{source_id}.{ext}"
                    e.g.  "hks.api318.unit1.thinking-probabilistically/handout-1-course-overview.pdf"
                    This is what Krizia's wizard writes on file-upload.
  2. local_path   — absolute path on disk (dev fallback only).

Re-upload semantics:
  - The wizard computes SHA-256 of the uploaded file and writes it to content_hash.
  - If content_hash in YAML matches the hash stored in the first chunk's metadata,
    the source is skipped (same content already ingested).
  - If they differ, old chunks are deleted and the source is re-ingested.
  - Sources with no content_hash fall back to the old "any chunks exist → skip" behaviour.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tiktoken
from openai import OpenAI
from pypdf import PdfReader

from app.config import get_settings
from app.db import get_supabase
from app.skill_loader import load_skill

CORPUS_BUCKET = "corpus"
CHUNK_TOKENS = 300
OVERLAP_TOKENS = 50
EMBED_BATCH = 16


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_pdf_bytes(data: bytes) -> list[tuple[int, str]]:
    reader = PdfReader(BytesIO(data))
    return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]


def _fetch_bytes(source: dict, supabase) -> bytes:
    """Return raw file bytes using storage_path → local_path fallback."""
    storage_path = source.get("storage_path")
    local_path = source.get("local_path")

    if storage_path:
        return supabase.storage.from_(CORPUS_BUCKET).download(storage_path)

    if local_path:
        p = Path(local_path)
        if not p.exists():
            raise FileNotFoundError(f"local file not found: {local_path}")
        return p.read_bytes()

    raise ValueError("source has neither storage_path nor local_path")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _tokenize(text: str, enc: tiktoken.Encoding) -> list[int]:
    return enc.encode(text)


def _chunk_tokens(tokens: list[int], enc: tiktoken.Encoding) -> list[str]:
    chunks = []
    step = CHUNK_TOKENS - OVERLAP_TOKENS
    for i in range(0, max(1, len(tokens)), step):
        chunk_toks = tokens[i : i + CHUNK_TOKENS]
        if not chunk_toks:
            break
        chunks.append(enc.decode(chunk_toks))
    return chunks


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def _should_skip(source_id: str, skill_id: str, yaml_hash: str | None, supabase) -> bool:
    """Return True if existing chunks are up-to-date and ingestion can be skipped."""
    existing = (
        supabase.table("corpus_chunks")
        .select("id,metadata")
        .eq("skill_id", skill_id)
        .eq("source_id", source_id)
        .eq("chunk_index", 0)
        .limit(1)
        .execute()
    )
    if not existing.data:
        return False  # nothing ingested yet

    if yaml_hash is None:
        # No hash in YAML — legacy behaviour: any existing chunks → skip
        print(f"  {source_id}: already ingested (no content_hash to compare), skipping")
        return True

    stored_hash = (existing.data[0].get("metadata") or {}).get("content_hash")
    if stored_hash == yaml_hash:
        print(f"  {source_id}: content_hash matches, skipping")
        return True

    # Hash mismatch — teacher re-uploaded; delete stale chunks before re-ingesting
    print(f"  {source_id}: content_hash changed ({stored_hash[:8] if stored_hash else 'none'} → {yaml_hash[:8]}), deleting stale chunks")
    supabase.table("corpus_chunks").delete().eq("skill_id", skill_id).eq("source_id", source_id).execute()
    return False


def ingest(skill_id: str) -> None:
    settings = get_settings()
    supabase = get_supabase()
    openai_client = OpenAI(api_key=settings.openai_api_key)
    enc = tiktoken.get_encoding("cl100k_base")

    row = load_skill(skill_id, supabase)
    skill = row["yaml"]
    corpus = skill.get("corpus", {})
    sources = corpus.get("primary_sources") or corpus.get("sources") or []

    if not sources:
        print(f"No corpus sources found for {skill_id}")
        return

    for source in sources:
        source_id = source.get("id") or source.get("title", "unknown")
        source_title = source.get("title", source_id)
        fmt = source.get("format", "pdf").lower()
        yaml_hash = source.get("content_hash")

        has_storage = bool(source.get("storage_path"))
        has_local = bool(source.get("local_path"))

        if not has_storage and not has_local:
            print(f"  Skipping {source_id} — no storage_path or local_path")
            continue

        if fmt != "pdf":
            print(f"  Skipping {source_id} — unsupported format: {fmt!r} (only pdf supported)")
            continue

        if _should_skip(source_id, skill_id, yaml_hash, supabase):
            continue

        # Fetch raw bytes
        origin = f"Storage:{source['storage_path']}" if has_storage else f"local:{source['local_path']}"
        print(f"  Ingesting {source_id} from {origin}")
        try:
            data = _fetch_bytes(source, supabase)
        except Exception as e:
            print(f"  ERROR fetching {source_id}: {e}")
            continue

        actual_hash = _sha256(data)
        pages = _read_pdf_bytes(data)
        full_text = "\n".join(text for _, text in pages)
        tokens = _tokenize(full_text, enc)
        raw_chunks = _chunk_tokens(tokens, enc)

        chunk_rows = []
        for idx, chunk_text in enumerate(raw_chunks):
            chunk_rows.append({
                "skill_id": skill_id,
                "source_id": source_id,
                "chunk_index": idx,
                "chunk_text": chunk_text,
                "metadata": {
                    "source_title": source_title,
                    "chunk_index": idx,
                    "content_hash": actual_hash,
                },
            })

        # Embed in batches
        all_embeddings: list[list[float]] = []
        texts = [r["chunk_text"] for r in chunk_rows]
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i : i + EMBED_BATCH]
            resp = openai_client.embeddings.create(model=settings.embedding_model, input=batch)
            all_embeddings.extend([d.embedding for d in resp.data])
            print(f"    embedded chunks {i}-{i + len(batch) - 1}")

        for row_data, emb in zip(chunk_rows, all_embeddings):
            row_data["embedding"] = emb

        supabase.table("corpus_chunks").upsert(
            chunk_rows,
            on_conflict="skill_id,source_id,chunk_index",
        ).execute()

        print(f"  {source_id}: inserted {len(chunk_rows)} chunks (hash {actual_hash[:8]})")

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest corpus PDFs for an OPAS skill")
    parser.add_argument("--skill-id", required=True)
    args = parser.parse_args()
    ingest(args.skill_id)
