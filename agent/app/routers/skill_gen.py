"""Student self-serve skill generation and lifecycle endpoints.

E1 — Core generation:
    POST /skills/generate

E2 — Lifecycle:
    POST /skills/{skill_id}/materials   (text or PDF append)
    GET  /skills/mine
    DELETE /skills/{skill_id}

All routes sit behind require_learner_token (applied at router level in main.py).
"""
from __future__ import annotations

import re
import time
from io import BytesIO

import anthropic
import tiktoken
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from openai import OpenAI
from pydantic import BaseModel
from pypdf import PdfReader

from ..auth import LearnerContext, require_learner_token
from ..config import get_settings
from ..db import get_supabase

router = APIRouter()

# ── chunking constants (match ingest_corpus.py) ────────────────────────────
CHUNK_TOKENS = 300
OVERLAP_TOKENS = 50
EMBED_BATCH = 16

# ── supported MIME types for /materials ────────────────────────────────────
SUPPORTED_TYPES = {"text/plain", "application/pdf"}
SUPPORTED_LABEL = "text/plain, application/pdf"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower())[:30].strip("-")


def _chunk_and_embed(
    skill_id: str,
    source_id: str,
    text: str,
    sb,
    settings,
) -> int:
    """Chunk text, embed with OpenAI, upsert to corpus_chunks. Returns chunk count."""
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if not tokens:
        return 0

    chunks: list[str] = []
    step = CHUNK_TOKENS - OVERLAP_TOKENS
    for i in range(0, len(tokens), step):
        chunk_toks = tokens[i : i + CHUNK_TOKENS]
        if not chunk_toks:
            break
        chunks.append(enc.decode(chunk_toks))

    client = OpenAI(api_key=settings.openai_api_key)
    all_embeddings: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        resp = client.embeddings.create(model=settings.embedding_model, input=batch)
        all_embeddings.extend([d.embedding for d in resp.data])

    rows = [
        {
            "skill_id": skill_id,
            "source_id": source_id,
            "chunk_index": idx,
            "chunk_text": chunk_text,
            "embedding": emb,
            "metadata": {"source_title": source_id, "chunk_index": idx},
        }
        for idx, (chunk_text, emb) in enumerate(zip(chunks, all_embeddings))
    ]

    sb.table("corpus_chunks").upsert(
        rows, on_conflict="skill_id,source_id,chunk_index"
    ).execute()
    return len(rows)


def _get_total_chunks(skill_id: str, sb) -> int:
    result = (
        sb.table("corpus_chunks")
        .select("id", count="exact")
        .eq("skill_id", skill_id)
        .execute()
    )
    return result.count or 0


def _check_skill_write_access(skill_id: str, learner_id: str, sb) -> dict:
    """Return skill row or raise 404/403. Allows owner or group member."""
    row = (
        sb.table("skills")
        .select("owner_learner_id, group_id, status")
        .eq("id", skill_id)
        .single()
        .execute()
        .data or {}
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found.")

    owner_id   = row.get("owner_learner_id")
    skill_gid  = row.get("group_id")

    if owner_id == learner_id:
        return row
    if skill_gid:
        member = (
            sb.table("group_members")
            .select("learner_id")
            .eq("group_id", skill_gid)
            .eq("learner_id", learner_id)
            .execute()
            .data
        )
        if member:
            return row
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")


def _generate_opas_yaml(title: str, materials_text: str, pedagogy_id: str, settings) -> dict:
    """Call Claude to generate a minimal valid OPAS skill YAML dict."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    system = f"""\
You are an OPAS skill YAML generator. Produce a minimal but valid OPAS skill YAML \
based on the provided title, pedagogy, and materials. Output ONLY raw YAML — no \
markdown fences, no commentary.

Required top-level keys and shapes:
  skill:
    id: <slug derived from title>
    name: <human-readable title>
    version: "1.0.0"
  pedagogy:
    instructional_model:
      primary: <pedagogy_id>
      description: <1-2 sentence description>
      forbidden_moves: [<2-3 moves to avoid>]
  corpus:
    grounding_policy:
      min_similarity: 0.72
      require_citation: false
      refuse_if_ungrounded: false
  phases:
    - id: <phase-id>
      objectives: [<2-3 learning objectives>]
      probe_set:
        - id: <probe-id>
          question: "<probe question>"
          scorer: numeric_range
          expected_range: [0.5, 1.0]
      mastery:
        min_turns: 3
        advance_threshold: 0.7

Generate 1-3 phases appropriate for the materials complexity. Keep it tight.\
"""

    user_msg = (
        f"Title: {title}\n"
        f"Pedagogy: {pedagogy_id}\n\n"
        f"Materials (first 3000 chars):\n{materials_text[:3000]}"
    )

    resp = client.messages.create(
        model=settings.claude_model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text.strip()
    # Strip markdown fences if Claude wrapped the output
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return yaml.safe_load(raw)


# ---------------------------------------------------------------------------
# POST /skills/generate
# ---------------------------------------------------------------------------

class GenerateSkillRequest(BaseModel):
    title: str
    materials_text: str
    pedagogy_id: str
    visibility: str = "private"   # "private" | "group"
    group_id: str | None = None


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_skill(
    req: GenerateSkillRequest,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    settings = get_settings()
    sb = get_supabase()

    if req.visibility == "group" and not req.group_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="group_id is required when visibility='group'.",
        )

    # 1. Generate YAML via Claude
    try:
        generated_yaml = _generate_opas_yaml(
            req.title, req.materials_text, req.pedagogy_id, settings
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"YAML generation failed: {exc}",
        )

    # 2. Build skill ID and insert into skills table
    slug      = _slugify(req.title)
    ts        = int(time.time())
    skill_id  = f"student.{learner.learner_id[:8]}.{slug}.{ts}"

    skill_name = (generated_yaml.get("skill") or {}).get("name") or req.title
    sb.table("skills").insert({
        "id": skill_id,
        "name": skill_name,
        "yaml": generated_yaml,
        "version": "1.0.0",
        # pilot: load_skill accepts it; "draft" would be rejected by the existing check
        "status": "pilot",
        "owner_learner_id": learner.learner_id,
        "group_id": req.group_id if req.visibility == "group" else None,
    }).execute()

    # 3. Ingest corpus chunks from materials_text
    ingested = _chunk_and_embed(skill_id, "initial-materials", req.materials_text, sb, settings)

    yaml_str = yaml.dump(generated_yaml, default_flow_style=False)
    return {
        "skill_id": skill_id,
        "yaml_preview": yaml_str[:500],
        "ingested_chunks": ingested,
    }


# ---------------------------------------------------------------------------
# POST /skills/{skill_id}/materials — append text or PDF
# ---------------------------------------------------------------------------

@router.post("/{skill_id}/materials")
async def append_materials(
    skill_id: str,
    request: Request,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    settings = get_settings()
    sb = get_supabase()

    _check_skill_write_access(skill_id, learner.learner_id, sb)

    ct_header = request.headers.get("content-type", "").lower()

    if ct_header.startswith("application/json"):
        body = await request.json()
        text = body.get("materials_text", "")
        if not text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide 'materials_text' in JSON body.",
            )
        source_id = f"text-{int(time.time())}"

    elif ct_header.startswith("multipart/form-data"):
        form = await request.form()
        uploaded = form.get("file")
        if uploaded is None or not hasattr(uploaded, "filename"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Multipart upload must include a 'file' field.",
            )
        file_ct = (uploaded.content_type or "").lower().split(";")[0].strip()
        if file_ct not in SUPPORTED_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported type '{file_ct}'. Supported: {SUPPORTED_LABEL}.",
            )
        raw = await uploaded.read()
        if file_ct == "application/pdf":
            reader = PdfReader(BytesIO(raw))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            text = raw.decode("utf-8", errors="replace")
        source_id = uploaded.filename or f"upload-{int(time.time())}"

    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Content-Type must be application/json or multipart/form-data. Supported file types: {SUPPORTED_LABEL}.",
        )

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Extracted text is empty. Check the uploaded content.",
        )

    chunks_added = _chunk_and_embed(skill_id, source_id, text, sb, settings)

    # Emit audit event
    sb.table("events").insert({
        "verb": "materials.appended",
        "actor_id": learner.learner_id,
        "session_id": None,
        "skill_id": skill_id,
        "object_type": "skill",
        "object_id": skill_id,
        "context": {"source_id": source_id, "chunks_added": chunks_added},
        "result": {},
    }).execute()

    return {
        "chunks_added": chunks_added,
        "total_chunks": _get_total_chunks(skill_id, sb),
    }


# ---------------------------------------------------------------------------
# GET /skills/mine
# ---------------------------------------------------------------------------

@router.get("/mine")
def my_skills(
    learner: LearnerContext = Depends(require_learner_token),
) -> list[dict]:
    sb = get_supabase()
    # skills.status is an enum; query valid values and exclude soft-deleted marker.
    # "archived" cannot be added to the enum without a migration, so we physically
    # delete skills on DELETE and this filter covers all remaining valid statuses.
    rows = (
        sb.table("skills")
        .select("id, yaml, status, group_id, owner_learner_id")
        .eq("owner_learner_id", learner.learner_id)
        .in_("status", ["pilot", "published", "draft"])
        .execute()
        .data or []
    )
    result = []
    for r in rows:
        skill_id = r["id"]
        name     = (r.get("yaml") or {}).get("skill", {}).get("name", skill_id)
        visibility = "group" if r.get("group_id") else "private"
        result.append({
            "skill_id": skill_id,
            "name": name,
            "status": r["status"],
            "visibility": visibility,
            "group_id": r.get("group_id"),
            "total_chunks": _get_total_chunks(skill_id, sb),
        })
    return result


# ---------------------------------------------------------------------------
# DELETE /skills/{skill_id}
# ---------------------------------------------------------------------------

@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    sb = get_supabase()

    row = (
        sb.table("skills")
        .select("owner_learner_id")
        .eq("id", skill_id)
        .single()
        .execute()
        .data or {}
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found.")
    if row.get("owner_learner_id") != learner.learner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the skill owner can delete it.",
        )

    # Archive linked sessions — preserve event log, phase_states, probe_attempts.
    # sessions.status enum now includes "archived" (added via migration).
    sessions = (
        sb.table("sessions")
        .select("id")
        .eq("skill_id", skill_id)
        .execute()
        .data or []
    )
    session_ids = [s["id"] for s in sessions]

    if session_ids:
        sb.table("sessions").update({"status": "archived"}).in_("id", session_ids).execute()

    # Delete corpus chunks and memories — these are derivable/regenerable, not audit data.
    sb.table("corpus_chunks").delete().eq("skill_id", skill_id).execute()
    sb.table("learner_memories").delete().eq("skill_id", skill_id).execute()

    # Archive the skill row (keeps FK from sessions.skill_id satisfied;
    # GET /skills/mine filters to pilot/published/draft so it disappears naturally).
    sb.table("skills").update({"status": "archived"}).eq("id", skill_id).execute()

    return {"deleted": True, "sessions_archived": len(session_ids)}
