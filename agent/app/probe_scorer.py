"""Probe scoring: deterministic fast-path + LLM-as-judge fallback."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import anthropic

from .config import get_settings

logger = logging.getLogger(__name__)

PROBE_TAG_RE = re.compile(r"""<probe\s+id=["']([^"']+)["']\s*/\s*>""", re.I)

_JUDGE_PROMPT = """\
You are an assessment scorer. A learner has responded to a probe question.
Decide whether the response demonstrates the target understanding.

Probe: {question}
Concept being assessed: {concept}
Learner response: {response}

Reply with ONLY valid JSON (no markdown fences), exactly:
{{"score": <0.0-1.0>, "rationale": "<one sentence>"}}

Score 1.0 if the learner clearly demonstrates understanding, 0.0 if clearly wrong/absent,
and a value in between for partial understanding."""


def extract_probe_tag(text: str) -> tuple[str, Optional[str]]:
    """Strip <probe id='...'/> tag from model reply. Returns (cleaned_text, probe_id_or_None)."""
    m = PROBE_TAG_RE.search(text)
    if m:
        return PROBE_TAG_RE.sub("", text).strip(), m.group(1)
    return text, None


def _llm_judge(probe: dict, learner_response: str) -> dict:
    settings = get_settings()
    question = probe.get("prompt", probe.get("question", ""))
    concept = probe.get("concept", "")
    prompt = _JUDGE_PROMPT.format(
        question=question, concept=concept, response=learner_response
    )
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.claude_model,
            system="You are a precise assessment scorer. Output only valid JSON.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )
        raw = resp.content[0].text.strip()
        parsed = json.loads(raw)
        return {
            "probe_id": probe["id"],
            "score": float(parsed["score"]),
            "scorer": settings.claude_model,
            "rationale": parsed.get("rationale", ""),
        }
    except (json.JSONDecodeError, KeyError, Exception) as e:
        logger.warning("LLM judge parse failed for probe %s: %s", probe.get("id"), e)
        return {"probe_id": probe["id"], "score": None, "scorer": "parse_failed", "rationale": ""}


def score_single_probe(probe: dict, learner_response: str) -> dict:
    """Score a probe. Fast deterministic path first; LLM judge for free_response."""
    p_type = probe.get("type", "")

    if p_type == "numeric_range":
        accept = probe.get("accept", {})
        nums = re.findall(r"\d+(?:\.\d+)?", learner_response)
        if nums:
            val = float(nums[0])
            lo = float(accept.get("min", 0))
            hi = float(accept.get("max", 1))
            return {
                "probe_id": probe["id"],
                "score": 1.0 if lo <= val <= hi else 0.0,
                "scorer": "numeric_range",
                "rationale": "",
            }

    elif p_type in ("multiple_choice", "multiple_choice_with_justification"):
        correct = probe.get("correct", "").strip().lower()
        matched = bool(correct and correct in learner_response.lower())
        return {
            "probe_id": probe["id"],
            "score": 1.0 if matched else 0.0,
            "scorer": "exact_match",
            "rationale": "",
        }

    # free_response or unrecognised type → LLM judge
    return _llm_judge(probe, learner_response)
