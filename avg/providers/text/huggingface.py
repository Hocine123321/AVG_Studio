"""HuggingFace text backend.

Mirrors the legacy `Invoke-HuggingFaceBackend` (`D:\\AI_Video_Gen\\modules\\
ai.ps1`, line 272): `POST /v1/chat/completions` on HF's router, a single
user-role message, `max_tokens` capped. NOT ported: the legacy function's
`=== SYSTEM RULES ===` prompt-splitting logic, which parsed a marker
specific to the old app's own script-generation prompt template and
rebuilt it into system/user roles. That is prompt authoring, not a text-
backend request-shape quirk - `generate()`'s `prompt` here is opaque, so
splitting it on an app-specific string would be guessing at structure the
brief says not to port. A future phase-level caller that wants a system
role can extend this backend's signature deliberately; this file stays a
plain single-message passthrough like every other backend.

Listing models needs no key at all - `GET /api/models` is HF's public
search index, matching the legacy `Get-LiveHuggingFaceModels`.
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

_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
_MODELS_URL = "https://huggingface.co/api/models"

# See groq.py's comment: Transport's own default client timeout is far too
# short for a real generation call - matches the legacy backends'
# -TimeoutSec 300 / -TimeoutSec 20.
_GENERATE_TIMEOUT_SECONDS = 300.0
_LIST_MODELS_TIMEOUT_SECONDS = 20.0


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
            raise AvgError(ErrorKind.bug, "Hugging Face returned no text")
        return text

    def list_models(self) -> list[str]:
        def _fetch_live() -> list[str]:
            params = urlencode(
                {"pipeline_tag": "text-generation", "sort": "downloads", "direction": "-1", "limit": "20"}
            )
            response = self._transport.request(
                "GET",
                f"{_MODELS_URL}?{params}",
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
