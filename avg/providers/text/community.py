"""The three free/community text backends: Groq, Hugging Face, Pollinations.

All three speak the same OpenAI-compatible chat-completions shape (one
user-role message, `max_tokens`, `temperature`, a `choices[0].message.
content` reply), differing only in base URL, key handling and how their
`/models` endpoint has to be filtered. They live together for that reason;
`gemini.py` stays separate because it speaks Google's own generateContent
shape, and `router.py` stays separate because routing is not a backend.

Grouping is free-vs-paid, not primary-vs-secondary: as of 2026-07, **Groq is
the only text backend that actually works** - Gemini is at zero free-tier
quota, Pollinations at zero pollen budget, and Hugging Face returns
`model_not_supported`.

None of these three carries retry or fallback-key logic. Fallback lives in
`avg/providers/text/router.py` as per-phase provider+model backup driven by
`POLICY`, never as a second key tried inline (which is what the legacy
`Invoke-*Backend` functions did).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import httpx

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.models import get_models
from avg.providers.text.base import raise_for_status
from avg.providers.transport import Transport

# See the per-backend notes below: Transport's own default client timeout
# (httpx.Client()'s, a few seconds) is far too short for a real generation
# call, discovered the hard way while recording this task's fixtures - see
# s3-task-3-report.md. These match the legacy backends' own -TimeoutSec
# values: 300s to generate, 20s to list models.
_GENERATE_TIMEOUT_SECONDS = 300.0
_LIST_MODELS_TIMEOUT_SECONDS = 20.0


def _extract_openai_text(response: httpx.Response) -> str:
    """Pull the reply out of a standard OpenAI-shaped envelope.

    Shared by Groq and Hugging Face, which return that envelope and nothing
    else. Pollinations gets its own extractor below rather than reusing this
    one - see `_extract_pollinations_text` for why the difference is real.
    """
    try:
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()
    except Exception:
        return ""


# ── Groq Backend ────────────────────────────────────────────
#
# Groq exposes an OpenAI-compatible chat-completions API - same request/
# response shape as OpenAI's, different base URL, key, and model catalogue.
# Mirrors the legacy `Invoke-GroqBackend` (`D:\AI_Video_Gen\modules\ai.ps1`,
# line 41) in endpoint and response parsing; retry/fallback-key logic from
# that function is NOT ported (see this module's docstring).

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"

# Groq's /models endpoint lists every model it hosts, including non-chat
# ones (speech-to-text, TTS, moderation) that would 400 on the chat-
# completions endpoint used by generate(). Matches the legacy
# Get-LiveGroqModels' $blocked pattern.
_BLOCKED_MODEL_SUBSTRINGS = ("whisper", "tts", "guard", "embed", "vision")


class GroqBackend:
    name = "groq"

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
            raise AvgError(ErrorKind.auth, "Groq API key is not set")

        response = self._transport.request(
            "POST",
            _GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        text = _extract_openai_text(response)
        if not text:
            raise AvgError(ErrorKind.bug, "Groq returned no text")
        return text

    def list_models(self) -> list[str]:
        def _fetch_live() -> list[str]:
            key = get_key(self.name)
            if not key:
                return []
            response = self._transport.request(
                "GET",
                _GROQ_MODELS_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=_LIST_MODELS_TIMEOUT_SECONDS,
                provider=self.name,
            )
            raise_for_status(response, self.name)
            data = response.json().get("data") or []
            ids = sorted(
                item["id"]
                for item in data
                if isinstance(item, dict)
                and item.get("id")
                and not any(bad in item["id"] for bad in _BLOCKED_MODEL_SUBSTRINGS)
            )
            return ids

        return get_models(self.name, _fetch_live, cache_path=self._cache_path)


# ── Hugging Face Backend ────────────────────────────────────
#
# Mirrors the legacy `Invoke-HuggingFaceBackend` (`D:\AI_Video_Gen\modules\
# ai.ps1`, line 272): `POST /v1/chat/completions` on HF's router, a single
# user-role message, `max_tokens` capped. NOT ported: the legacy function's
# `=== SYSTEM RULES ===` prompt-splitting logic, which parsed a marker
# specific to the old app's own script-generation prompt template and
# rebuilt it into system/user roles. That is prompt authoring, not a text-
# backend request-shape quirk - `generate()`'s `prompt` here is opaque, so
# splitting it on an app-specific string would be guessing at structure the
# brief says not to port. A future phase-level caller that wants a system
# role can extend this backend's signature deliberately; this stays a plain
# single-message passthrough like every other backend.
#
# Listing models needs no key at all - `GET /api/models` is HF's public
# search index, matching the legacy `Get-LiveHuggingFaceModels`.

_HF_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
_HF_MODELS_URL = "https://huggingface.co/api/models"


class HuggingFaceBackend:
    name = "huggingface"

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
            raise AvgError(ErrorKind.auth, "Hugging Face API key is not set")

        response = self._transport.request(
            "POST",
            _HF_CHAT_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        text = _extract_openai_text(response)
        if not text:
            raise AvgError(ErrorKind.bug, "Hugging Face returned no text")
        return text

    def list_models(self) -> list[str]:
        def _fetch_live() -> list[str]:
            params = urlencode(
                {"pipeline_tag": "text-generation", "sort": "downloads", "direction": "-1", "limit": "20"}
            )
            response = self._transport.request(
                "GET",
                f"{_HF_MODELS_URL}?{params}",
                timeout=_LIST_MODELS_TIMEOUT_SECONDS,
                provider=self.name,
            )
            raise_for_status(response, self.name)
            data = response.json()
            if not isinstance(data, list):
                return []
            ids = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                model_id = item.get("modelId") or item.get("id") or ""
                if model_id:
                    ids.append(model_id)
            return ids

        return get_models(self.name, _fetch_live, cache_path=self._cache_path)


# ── Pollinations Backend ────────────────────────────────────
#
# Pollinations' text API (`POST https://text.pollinations.ai/openai`) is
# OpenAI-compatible, mirroring the Groq section above almost exactly -
# matches the legacy `Invoke-PollinationsTextBackend` (`D:\AI_Video_Gen\
# modules\ai.ps1`, line 117). The one real difference, ported deliberately:
# a key is OPTIONAL. Pollinations works keyless - that is its whole appeal -
# so a missing key is NOT `ErrorKind.auth` here the way it is for every
# other backend; `generate` and `list_models` simply omit the
# `Authorization` header.
#
# Also ported: some Pollinations models answer with a bare JSON string
# instead of the OpenAI choices/message envelope (noted in the legacy
# function's comment) - `_extract_pollinations_text` handles both shapes,
# which is why it is a separate function from `_extract_openai_text`.

_POLLINATIONS_CHAT_URL = "https://text.pollinations.ai/openai"
_POLLINATIONS_MODELS_URL = "https://text.pollinations.ai/models"


def _extract_pollinations_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        choices = payload.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            return str(message.get("content") or "").strip()
    return ""


class PollinationsBackend:
    name = "pollinations"

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
        headers = {"Content-Type": "application/json"}
        key = get_key(self.name)
        if key:
            headers["Authorization"] = f"Bearer {key}"

        response = self._transport.request(
            "POST",
            _POLLINATIONS_CHAT_URL,
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        text = _extract_pollinations_text(response)
        if not text:
            raise AvgError(ErrorKind.bug, "Pollinations returned no text")
        return text

    def list_models(self) -> list[str]:
        def _fetch_live() -> list[str]:
            headers = {}
            key = get_key(self.name)
            if key:
                headers["Authorization"] = f"Bearer {key}"
            response = self._transport.request(
                "GET",
                _POLLINATIONS_MODELS_URL,
                headers=headers,
                timeout=_LIST_MODELS_TIMEOUT_SECONDS,
                provider=self.name,
            )
            raise_for_status(response, self.name)
            data = response.json()
            if not isinstance(data, list):
                return []
            names = sorted(
                item["name"]
                for item in data
                if isinstance(item, dict)
                and item.get("name")
                and (
                    "output_modalities" not in item
                    or "text" in (item.get("output_modalities") or [])
                )
            )
            return names

        return get_models(self.name, _fetch_live, cache_path=self._cache_path)
