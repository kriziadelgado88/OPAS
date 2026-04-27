"""Stateless multi-model compare endpoint.

POST /session/compare fans out one learner turn to N models in parallel.
No DB writes. No mastery tracking. No probe scoring.
This is a Demo-Day reviewer tool — the student session path is untouched.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..constitutions.loader import ConstitutionNotFound, load_constitution
from ..db import get_supabase
from ..model_adapter import ModelAdapterError, call_model
from ..probe_scorer import extract_probe_tag
from ..prompt_assembler import build_system_prompt, off_corpus_augmentation
from ..rag import retrieve_chunks
from ..skill_loader import load_skill

router = APIRouter()


class CompareRequest(BaseModel):
    skill_id: str
    learner_msg: str
    models: list[str]
    history: list[dict] = []


class CompareModelResponse(BaseModel):
    model: str
    reply: str | None
    citations: list[dict]
    yaml_refs: list[str]
    latency_ms: int
    error: str | None


class CompareResponse(BaseModel):
    skill_id: str
    learner_msg: str
    responses: list[CompareModelResponse]


@router.post("/compare", response_model=CompareResponse)
def compare_turn(req: CompareRequest) -> CompareResponse:
    settings = get_settings()
    supabase = get_supabase()

    row = load_skill(req.skill_id, supabase)
    skill = row["yaml"]

    # Load constitution if declared by skill — fail fast with 409 if missing.
    # Compare path includes constitution rules in system prompt;
    # active scanning is session-scoped and not applied here.
    constitution = None
    constitution_id = skill.get("constitution")
    if constitution_id:
        try:
            constitution = load_constitution(constitution_id)
        except ConstitutionNotFound:
            raise HTTPException(
                status_code=409,
                detail=f"Skill requires constitution '{constitution_id}' which is not available on this runtime.",
            )

    # Retrieve chunks ONCE — same corpus for all models (fairness guarantee).
    phase = skill["phases"][0]
    grounding_policy = skill.get("corpus", {}).get("grounding_policy", {})
    chunks = retrieve_chunks(req.learner_msg, req.skill_id, grounding_policy, supabase, settings)
    system, yaml_refs = build_system_prompt(skill, phase, chunks, constitution=constitution)

    # Mirror session_turn's off-corpus augmentation so all models see the refusal directive.
    if not chunks and grounding_policy.get("refuse_if_ungrounded"):
        system += off_corpus_augmentation()
        yaml_refs = ["corpus.grounding_policy.refuse_if_ungrounded"]

    messages = list(req.history) + [{"role": "user", "content": req.learner_msg}]
    chunk_citations = [
        {
            "source_id": c.get("source_id"),
            "chunk_text": c.get("chunk_text"),
            "similarity": c.get("similarity"),
            "metadata": c.get("metadata"),
        }
        for c in chunks
    ]

    def call_one(model_name: str) -> CompareModelResponse:
        t0 = time.perf_counter()
        try:
            raw = call_model(
                system=system,
                messages=messages,
                skill=skill,
                settings=settings,
                model=model_name,
            )
            reply, _ = extract_probe_tag(raw)
            elapsed = int((time.perf_counter() - t0) * 1000)
            return CompareModelResponse(
                model=model_name,
                reply=reply,
                citations=chunk_citations,
                yaml_refs=yaml_refs,
                latency_ms=elapsed,
                error=None,
            )
        except (ModelAdapterError, Exception) as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return CompareModelResponse(
                model=model_name,
                reply=None,
                citations=[],
                yaml_refs=[],
                latency_ms=elapsed,
                error=str(e),
            )

    with ThreadPoolExecutor(max_workers=len(req.models)) as executor:
        futures = [executor.submit(call_one, m) for m in req.models]
        responses = [f.result() for f in futures]

    return CompareResponse(
        skill_id=req.skill_id,
        learner_msg=req.learner_msg,
        responses=responses,
    )
