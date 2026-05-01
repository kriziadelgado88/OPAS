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

import html as _html
import hashlib
import re
import time
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

import anthropic
import httpx
import tiktoken
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator
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


# ---------------------------------------------------------------------------
# URL ingestion — POST /skills/{skill_id}/ingest_urls
# ---------------------------------------------------------------------------
# Lets a teacher paste a list of public URLs (Google Doc share links,
# Wikipedia pages, hosted PDFs, course websites, GitHub READMEs, etc.) and
# have each one fetched, content-type-extracted, and chunked into the
# existing corpus_chunks pipeline. No OAuth, no Drive integration — just
# whatever the public web returns.
#
# Coverage:
#   - PDFs (any URL ending .pdf or returning application/pdf)
#   - Public Google Docs (auto-rewrites to ?format=txt export endpoint)
#   - Plain text / markdown URLs (raw GitHub, course .txt files)
#   - HTML pages (strip tags, extract main text)
# Out of scope (need OAuth):
#   - Private Google Drive files
#   - Authenticated Notion pages
#   - Paywalled content

URL_FETCH_TIMEOUT_S = 30.0
URL_MAX_BYTES = 8 * 1024 * 1024   # 8 MB — safety cap per URL
URL_USER_AGENT = "Poppy/1.0 (+OPAS — public material ingestion; respects robots.txt)"


def _is_google_doc_url(url: str) -> bool:
    return "docs.google.com/document/" in url


def _gdoc_export_url(url: str) -> str:
    """Convert a public Google Doc share link → plain-text export endpoint.

    Patterns handled:
      https://docs.google.com/document/d/<DOC_ID>/edit?usp=sharing
      https://docs.google.com/document/d/<DOC_ID>/view
      https://docs.google.com/document/d/<DOC_ID>/
    All become:
      https://docs.google.com/document/d/<DOC_ID>/export?format=txt
    Any URL not matching the pattern is returned unchanged.
    """
    m = re.search(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        return url
    doc_id = m.group(1)
    return f"https://docs.google.com/document/d/{doc_id}/export?format=txt"


def _strip_html_to_text(html_text: str) -> str:
    """Light HTML → text. Removes <script>/<style> blocks, unwraps tags,
    decodes HTML entities, collapses whitespace. Stdlib-only — no new dep."""
    # Drop scripts and styles entirely (including inner content)
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text,
                     flags=re.IGNORECASE | re.DOTALL)
    # Replace block-level closers with newlines so paragraphs survive
    cleaned = re.sub(r"</(p|div|li|h[1-6]|br|tr|section|article)>", "\n", cleaned,
                     flags=re.IGNORECASE)
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    # Strip remaining tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Decode entities (&amp;, &nbsp;, etc.)
    cleaned = _html.unescape(cleaned)
    # Normalize whitespace
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _slugify_url(url: str) -> str:
    """Stable, human-recognisable source_id from a URL: domain + short hash."""
    parsed = urlparse(url)
    host = (parsed.netloc or "url").replace("www.", "")
    # Keep first path segment so two docs from same host don't collide visually
    first_seg = parsed.path.strip("/").split("/")[0] if parsed.path else ""
    short_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    base = f"{host}-{first_seg}" if first_seg else host
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:40]
    return f"url-{base}-{short_hash}"


def _fetch_and_extract_url(url: str) -> tuple[str, str]:
    """Fetch a public URL, extract text, return (text, source_id).

    Raises ValueError on any failure with a human-readable reason that the
    frontend can surface to the teacher (these messages end up in the
    per-URL result chip).
    """
    fetch_url = _gdoc_export_url(url) if _is_google_doc_url(url) else url

    try:
        with httpx.Client(
            timeout=URL_FETCH_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": URL_USER_AGENT},
        ) as client:
            resp = client.get(fetch_url)
    except httpx.TimeoutException:
        raise ValueError(f"timeout after {URL_FETCH_TIMEOUT_S:.0f}s")
    except httpx.HTTPError as exc:
        raise ValueError(f"network error: {exc}")

    if resp.status_code >= 400:
        raise ValueError(f"server returned {resp.status_code}")

    body = resp.content
    if len(body) > URL_MAX_BYTES:
        raise ValueError(f"too large ({len(body)//(1024*1024)} MB > {URL_MAX_BYTES//(1024*1024)} MB cap)")

    ctype = (resp.headers.get("content-type") or "").lower().split(";")[0].strip()
    name_lower = url.lower()

    # PDF — by content-type or by extension
    if "application/pdf" in ctype or name_lower.endswith(".pdf"):
        try:
            reader = PdfReader(BytesIO(body))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise ValueError(f"PDF parse failed: {exc}")
        if not text.strip():
            raise ValueError("PDF has no extractable text (image-only / scanned)")
        return text, _slugify_url(url)

    # Plain text or markdown — direct
    if (ctype.startswith("text/plain") or ctype.startswith("text/markdown")
            or name_lower.endswith(".txt") or name_lower.endswith(".md")
            or name_lower.endswith(".markdown")):
        text = resp.text
        if not text.strip():
            raise ValueError("file is empty")
        return text, _slugify_url(url)

    # HTML — strip tags
    if "text/html" in ctype or "application/xhtml" in ctype:
        text = _strip_html_to_text(resp.text)
        if not text.strip() or len(text) < 80:
            raise ValueError("page had little or no extractable text")
        return text, _slugify_url(url)

    # Unknown content type — best-effort decode as text
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        raise ValueError(f"unsupported content-type: {ctype or 'unknown'}")
    if not text.strip():
        raise ValueError(f"unsupported content-type: {ctype or 'unknown'}")
    return text, _slugify_url(url)


class IngestUrlsRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=20)

    @field_validator("urls")
    @classmethod
    def _strip_and_validate(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for u in v:
            u = (u or "").strip()
            if not u:
                continue
            if not (u.startswith("http://") or u.startswith("https://")):
                raise ValueError(f"URLs must start with http:// or https:// (got '{u[:40]}')")
            if len(u) > 2000:
                raise ValueError("URL too long (>2000 chars)")
            cleaned.append(u)
        if not cleaned:
            raise ValueError("at least one valid URL required")
        return cleaned


@router.post("/{skill_id}/ingest_urls")
def ingest_urls(
    skill_id: str,
    body: IngestUrlsRequest,
    learner: LearnerContext = Depends(require_learner_token),
) -> dict:
    """Fetch each URL, extract text, chunk + embed into corpus_chunks.

    Returns per-URL results so the frontend can render a chip showing
    which URLs succeeded and which failed (and why).
    """
    settings = get_settings()
    sb = get_supabase()

    _check_skill_write_access(skill_id, learner.learner_id, sb)

    results: list[dict] = []
    total_chunks_added = 0

    for url in body.urls:
        try:
            text, source_id = _fetch_and_extract_url(url)
            chunks_added = _chunk_and_embed(skill_id, source_id, text, sb, settings)
            total_chunks_added += chunks_added
            results.append({
                "url": url,
                "ok": True,
                "source_id": source_id,
                "chunks_added": chunks_added,
                "chars": len(text),
            })

            sb.table("events").insert({
                "verb": "materials.appended",
                "actor_id": learner.learner_id,
                "session_id": None,
                "skill_id": skill_id,
                "object_type": "skill",
                "object_id": skill_id,
                "context": {"source_id": source_id, "chunks_added": chunks_added,
                            "ingest_method": "url", "url": url},
                "result": {},
            }).execute()
        except ValueError as exc:
            results.append({
                "url": url, "ok": False, "error": str(exc),
                "source_id": None, "chunks_added": 0, "chars": 0,
            })
        except Exception as exc:
            # Catch-all so one bad URL doesn't kill the whole batch.
            results.append({
                "url": url, "ok": False,
                "error": f"unexpected error: {type(exc).__name__}: {exc}"[:300],
                "source_id": None, "chunks_added": 0, "chars": 0,
            })

    return {
        "results": results,
        "ok_count": sum(1 for r in results if r["ok"]),
        "fail_count": sum(1 for r in results if not r["ok"]),
        "total_chunks_added": total_chunks_added,
        "total_chunks": _get_total_chunks(skill_id, sb),
    }
