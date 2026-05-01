"""ElevenLabs TTS proxy.

Why a proxy and not a direct browser call?
  - The ElevenLabs API key never touches the client.
  - We can rate-limit / log / swap providers later without touching the frontend.
  - CORS is handled by FastAPI; ElevenLabs would otherwise reject browser origins.

Endpoints
---------
GET  /voice/voices
        Static list of the 4 supported voices (see implementation handoff).

GET  /voice/preview?voice_id=...
        Stream a short canned preview phrase ("Hi! I'm your tutor.") rendered
        with the selected voice. Used by the onboarding voice picker.

POST /voice/tts
        Body: {"text": "...", "voice_id": "..."}
        Streams audio/mpeg for the supplied text. Used by the student-page
        speaker toggle to read each agent reply aloud.

The router is authenticated via the same learner-token dependency as the
session router; that's wired in main.py at include_router time.
"""
from __future__ import annotations

import os
from typing import Iterator

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()

# ---------------------------------------------------------------------------
# Static voice catalogue (4 ElevenLabs voices selected for the demo).
# ---------------------------------------------------------------------------
VOICES: list[dict] = [
    {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah",     "accent": "American",  "gender": "female"},
    {"id": "TX3LPaxmHKxFdv7VOQHJ", "name": "Liam",      "accent": "American",  "gender": "male"},
    {"id": "XB0fDUnXU5powFXDhCwa", "name": "Charlotte", "accent": "Swedish",   "gender": "female"},
    {"id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel",    "accent": "British",   "gender": "male"},
]
_VALID_IDS = {v["id"] for v in VOICES}

PREVIEW_TEXT = "Hi! I'm your tutor. Ready to learn together?"

# Use eleven_turbo_v2_5 — fastest model that still sounds natural; good for
# short responsive turns. Switch to eleven_multilingual_v2 for non-English.
ELEVEN_MODEL = "eleven_turbo_v2_5"
ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="Voice unavailable: ELEVENLABS_API_KEY not configured.",
        )
    return key


def _stream_tts(text: str, voice_id: str) -> Iterator[bytes]:
    """Generator that yields audio/mpeg chunks streamed from ElevenLabs."""
    if voice_id not in _VALID_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown voice_id: {voice_id}")
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="text too long (>4000 chars)")

    headers = {
        "xi-api-key": _api_key(),
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": ELEVEN_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    url = ELEVEN_TTS_URL.format(voice_id=voice_id)

    # Use a synchronous client — FastAPI will run StreamingResponse generators
    # in a worker thread, so blocking httpx is fine and simpler than aiohttp.
    with httpx.stream(
        "POST", url, json=payload, headers=headers, timeout=45.0
    ) as resp:
        if resp.status_code >= 400:
            # Drain body to surface the upstream error message.
            body = resp.read().decode("utf-8", errors="replace")
            raise HTTPException(
                status_code=502,
                detail=f"ElevenLabs upstream error {resp.status_code}: {body[:300]}",
            )
        for chunk in resp.iter_bytes(chunk_size=4096):
            if chunk:
                yield chunk


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/voices")
def list_voices() -> list[dict]:
    """Return the supported voice catalogue. Useful for clients that want to
    render the picker dynamically rather than hard-coding it."""
    return VOICES


@router.get("/preview")
def voice_preview(voice_id: str, request: Request) -> StreamingResponse:
    """Render the canned preview phrase with the chosen voice and stream it."""
    return StreamingResponse(
        _stream_tts(PREVIEW_TEXT, voice_id),
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    voice_id: str = Field(..., min_length=1, max_length=64)


@router.post("/tts")
def voice_tts(req: TTSRequest) -> StreamingResponse:
    """Stream audio for the supplied text. Used by the student-page speaker
    toggle to narrate agent replies."""
    return StreamingResponse(
        _stream_tts(req.text, req.voice_id),
        media_type="audio/mpeg",
    )


# ---------------------------------------------------------------------------
# Speech-to-text (Scribe) — student dictation
# ---------------------------------------------------------------------------
ELEVEN_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
SCRIBE_MODEL = "scribe_v1"  # ElevenLabs' multilingual STT model.


@router.post("/transcribe")
async def voice_transcribe(
    audio: UploadFile = File(..., description="Audio recording to transcribe."),
    language_code: str | None = None,
) -> dict:
    """Transcribe a learner's recorded audio to text via ElevenLabs Scribe.

    Frontend records via MediaRecorder (typically WebM/Opus or MP4/AAC),
    posts the blob here as multipart/form-data. We forward to ElevenLabs
    and return {"text": "..."}.
    """
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio upload")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="audio too large (>25 MB)")

    headers = {"xi-api-key": _api_key()}
    files = {
        "file": (audio.filename or "audio.webm", raw, audio.content_type or "audio/webm"),
    }
    data: dict = {"model_id": SCRIBE_MODEL}
    if language_code:
        data["language_code"] = language_code

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(ELEVEN_STT_URL, headers=headers, data=data, files=files)
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs STT error {resp.status_code}: {resp.text[:300]}",
        )
    payload = resp.json()
    text = (payload.get("text") or "").strip()
    return {"text": text, "language": payload.get("language_code")}
