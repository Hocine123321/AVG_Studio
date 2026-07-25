"""Groq text backend.

Groq exposes an OpenAI-compatible chat-completions API - same request/
response shape as OpenAI's, different base URL, key, and model catalogue.
Mirrors the legacy `Invoke-GroqBackend` (`D:\\AI_Video_Gen\\modules\\ai.ps1`,
line 41) in endpoint and response parsing; retry/fallback-key logic from
that function is NOT ported (see this module's docstring in
`avg/providers/text/router.py` for where fallback now lives - per-phase
provider+model backup, driven by `POLICY`, not a second key tried inline).
"""

from __future__ import annotations

import httpx

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.models import get_models
from avg.providers.text.base import raise_for_status
from avg.providers.transport import Transport
from pathlib import Path

_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODELS_URL = "https://api.groq.com/openai/v1/models"

# Matches the legacy backend's own -TimeoutSec values (Invoke-GroqBackend:
# 300s for a generation call; Get-LiveGroqModels: 20s for a model list).
# Transport's own default (httpx.Client()'s, a few seconds) is far too
# short for a real generation call and was discovered the hard way while
# recording this task's fixtures - see s3-task-3-report.md.
_GENERATE_TIMEOUT_SECONDS = 300.0
_LIST_MODELS_TIMEOUT_SECONDS = 20.0

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
            _CHAT_URL,
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

        text = _extract_text(response)
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
                _MODELS_URL,
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


def _extract_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()
    except Exception:
        return ""
