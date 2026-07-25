"""Pollinations text backend.

Pollinations' text API (`POST https://text.pollinations.ai/openai`) is
OpenAI-compatible, mirroring `groq.py` almost exactly - matches the legacy
`Invoke-PollinationsTextBackend` (`D:\\AI_Video_Gen\\modules\\ai.ps1`, line
117). The one real difference, ported deliberately: a key is OPTIONAL.
Pollinations works keyless - that is its whole appeal - so a missing key is
NOT `ErrorKind.auth` here the way it is for every other backend; `generate`
and `list_models` simply omit the `Authorization` header.

Also ported: some Pollinations models answer with a bare JSON string
instead of the OpenAI choices/message envelope (noted in the legacy
function's comment) - `_extract_text` handles both shapes.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from avg.core.errors import AvgError, ErrorKind
from avg.config.secrets import get_key
from avg.providers.models import get_models
from avg.providers.text.base import raise_for_status
from avg.providers.transport import Transport

_CHAT_URL = "https://text.pollinations.ai/openai"
_MODELS_URL = "https://text.pollinations.ai/models"

# See groq.py's comment: Transport's own default client timeout is far too
# short for a real generation call - matches the legacy backends'
# -TimeoutSec 300 / -TimeoutSec 20.
_GENERATE_TIMEOUT_SECONDS = 300.0
_LIST_MODELS_TIMEOUT_SECONDS = 20.0


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
            _CHAT_URL,
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

        text = _extract_text(response)
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
                _MODELS_URL,
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


def _extract_text(response: httpx.Response) -> str:
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
