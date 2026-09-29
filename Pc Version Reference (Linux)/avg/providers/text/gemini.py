"""Gemini text backend.

Mirrors the legacy `Invoke-GeminiBackend` (`D:\\AI_Video_Gen\\modules\\ai.ps1`,
line 194): `generateContent` on a specific model id, key sent BOTH as the
`x-goog-api-key` header and a `key=` query parameter (a genuine quirk of
Gemini's REST API - the old code sent both and this ports that rather than
guessing which one Gemini actually requires). `models/` is stripped from a
model id that carries the prefix, since the URL path adds it back.

One thing NOT ported as a generic "no text" bug: Gemini can return 200 with
an empty `candidates` list because the prompt was blocked
(`promptFeedback.blockReason`) or a candidate finished for a reason other
than a clean stop (`finishReason`). The legacy code surfaced that as a
plain string ("Gemini returned no text. FinishReason=... Feedback=...")
that then went through the *old* app's generic keyword classifier. Here,
that same detail string is run through `avg.providers.errors.classify` (the
one place a status/body is ever pattern-matched) so a safety block comes
back as `ErrorKind.content_filter` rather than the generic `ErrorKind.bug`
every other backend's empty-response case falls back to - Gemini is the
only provider in this file with a real signal to classify from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify
from avg.providers.models import get_models
from avg.providers.text.base import raise_for_status
from avg.providers.transport import Transport

_MODELS_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# See groq.py's comment: Transport's own default client timeout is far too
# short for a real generation call - matches the legacy backend's own
# ai.gemini.timeout_seconds default (300s) / Get-LiveGeminiModels' 20s.
_GENERATE_TIMEOUT_SECONDS = 300.0
_LIST_MODELS_TIMEOUT_SECONDS = 20.0

# Matches the legacy Get-LiveGeminiModels filter: keep only models that
# support generateContent and aren't an image/embedding/audio/video variant.
_EXCLUDE_MODEL_SUBSTRINGS = ("image", "imagen", "embedding", "audio", "tts", "veo", "vision")


class GeminiBackend:
    name = "gemini"

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        cache_path: Path | str | None = None,
    ) -> None:
        self._transport = transport if transport is not None else Transport()
        self._cache_path = cache_path

    def generate(
        self,
        prompt: str,
        model: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        key = get_key(self.name)
        if not key:
            raise AvgError(ErrorKind.auth, "Gemini API key is not set")

        model_id = model.removeprefix("models/")
        url = f"{_MODELS_BASE}/{quote(model_id, safe='')}:generateContent?key={quote(key, safe='')}"

        response = self._transport.request(
            "POST",
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
            },
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        payload = response.json()
        candidates = payload.get("candidates") or []
        text = _extract_text(candidates)
        if text:
            return text

        finish_reason = str(candidates[0].get("finishReason", "")) if candidates else ""
        feedback = payload.get("promptFeedback")
        detail = f"finishReason={finish_reason} promptFeedback={feedback}"
        kind = classify(200, detail, None, self.name)
        raise AvgError(kind, f"Gemini returned no text ({detail})")

    def list_models(self) -> list[str]:
        def _fetch_live() -> list[str]:
            key = get_key(self.name)
            if not key:
                return []
            url = f"{_MODELS_BASE}?key={quote(key, safe='')}"
            response = self._transport.request(
                "GET", url, timeout=_LIST_MODELS_TIMEOUT_SECONDS, provider=self.name
            )
            raise_for_status(response, self.name)
            data = response.json()
            models = data.get("models") or []
            ids: set[str] = set()
            for m in models:
                if not isinstance(m, dict):
                    continue
                name = m.get("name") or ""
                methods = m.get("supportedGenerationMethods") or []
                if not name or "generateContent" not in methods:
                    continue
                model_id = name.removeprefix("models/")
                if any(bad in model_id.lower() for bad in _EXCLUDE_MODEL_SUBSTRINGS):
                    continue
                ids.add(model_id)
            return sorted(ids)

        return get_models(self.name, _fetch_live, cache_path=self._cache_path)


def _extract_text(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    return "\n".join(texts).strip()
