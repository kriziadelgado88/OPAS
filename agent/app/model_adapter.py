"""Multi-model adapter: Anthropic, OpenAI, Gemini.

Public interface:
    call_model(*, system, messages, skill, settings, model=None) -> str
    ModelAdapterError  — raised on any provider failure; safe to catch in compare.py

Dispatch by model name prefix:
    claude-* / anthropic:*  → Anthropic Messages API
    gpt-* / openai:*        → OpenAI Chat Completions
    gemini-* / google:*     → Gemini REST via httpx
"""
from __future__ import annotations

import time

import anthropic
import httpx
from openai import OpenAI

from .config import Settings


class ModelAdapterError(Exception):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider


# ── inner functions ─────────────────────────────────────────────────────────

def _call_anthropic(
    system: str, messages: list[dict], skill: dict, settings: Settings, model_name: str
) -> str:
    max_tokens = skill.get("runtime_hints", {}).get("max_response_tokens", 1024)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model_name,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
            )
            return resp.content[0].text
        except anthropic.RateLimitError as e:
            raise ModelAdapterError("anthropic", f"Rate limited: {e}") from e
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise ModelAdapterError("anthropic", f"HTTP {e.status_code}: {e.message}") from e
        except Exception as e:
            raise ModelAdapterError("anthropic", str(e)) from e
    raise ModelAdapterError("anthropic", "Exhausted retries on 529")


def _call_openai(
    system: str, messages: list[dict], skill: dict, settings: Settings, model_name: str
) -> str:
    max_tokens = skill.get("runtime_hints", {}).get("max_response_tokens", 1024)
    client = OpenAI(api_key=settings.openai_api_key)
    openai_messages = [{"role": "system", "content": system}] + messages
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=openai_messages,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except Exception as e:
        raise ModelAdapterError("openai", str(e)) from e


def _call_gemini(
    system: str, messages: list[dict], skill: dict, settings: Settings, model_name: str
) -> str:
    api_key = settings.gemini_api_key
    if not api_key:
        raise ModelAdapterError("gemini", "GEMINI_API_KEY not configured")
    max_tokens = skill.get("runtime_hints", {}).get("max_response_tokens", 1024)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    gemini_contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": gemini_contents,
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    try:
        resp = httpx.post(url, params={"key": api_key}, json=payload, timeout=90.0)
        if resp.status_code != 200:
            raise ModelAdapterError("gemini", f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except ModelAdapterError:
        raise
    except Exception as e:
        raise ModelAdapterError("gemini", str(e)) from e


# ── dispatcher ───────────────────────────────────────────────────────────────

def call_model(
    *,
    system: str,
    messages: list[dict],
    skill: dict,
    settings: Settings,
    model: str | None = None,
) -> str:
    """Dispatch to the correct provider based on the model name prefix.

    Falls back to settings.claude_model (Anthropic) when model is not supplied,
    preserving the existing single-model session path unchanged.
    """
    resolved = model or settings.claude_model
    if resolved.startswith(("claude-", "anthropic:")):
        return _call_anthropic(system, messages, skill, settings, resolved)
    if resolved.startswith(("gpt-", "openai:")):
        return _call_openai(system, messages, skill, settings, resolved)
    if resolved.startswith(("gemini-", "google:")):
        return _call_gemini(system, messages, skill, settings, resolved)
    raise ModelAdapterError(resolved, f"Unknown model prefix: {resolved!r}")
