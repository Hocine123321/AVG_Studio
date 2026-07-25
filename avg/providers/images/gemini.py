"""Gemini image backend.

Mirrors the legacy `Invoke-GeminiImageGeneration` (`D:\\AI_Video_Gen\\modules\\
visuals.ps1`, line 775): key sent both as the `x-goog-api-key` header and a
`key=` query parameter (the same genuine REST-API quirk already ported in
`avg/providers/text/gemini.py`), and two real APIs behind one model
catalogue, routed by model id - the legacy function's own B2.17.9 fix,
discovered the hard way when Google split Gemini image generation in two:

  - Imagen models (`imagen-*`) use the Vertex-style `:predict` endpoint -
    `{instances:[{prompt}], parameters:{sampleCount, aspectRatio,
    outputMimeType}}`, response `predictions[].bytesBase64Encoded` (or one
    of two older field names the legacy code also checks).
  - Native Gemini image models (`gemini-*-image`, "Nano Banana") use
    `:generateContent`, like the text API -
    `{contents:[...], generationConfig:{responseModalities:['TEXT',
    'IMAGE'], imageConfig:{aspectRatio}}}`, response
    `candidates[0].content.parts[].inlineData.data`.

Also ported: the `*-image-preview` -> GA model-id remap. Google shut the
preview ids down on 2026-06-25; a stale config pointing at one 404s, which
looked like "Gemini image gen just stopped working" until the legacy code
traced it to this. Auto-remapping keeps an old config working without a
manual edit.

NOT ported: the legacy function's blind "try the request WITH aspectRatio;
if that whole call raises anything at all, retry WITHOUT it" - a bare
try/except around the entire native-model request with no inspection of
what failed, there to tolerate older/preview models that reject
`imageConfig.aspectRatio`. Skipped here for two reasons: it would mean up
to two real HTTP calls per `generate()` (every other provider in this
package makes exactly one), and both model families this provider serves
today (Imagen, current GA Gemini image models) accept `aspectRatio`.

A 200 with no image - the API's own in-band failure signal, a blocked
prompt (`promptFeedback.blockReason`) or a candidate that didn't finish
cleanly (`finishReason`) - is classified from that detail text via
`avg.providers.errors.classify`, the same treatment the text Gemini
backend gives an empty `candidates` list.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify
from avg.providers.images.base import raise_for_status
from avg.providers.transport import Transport

_MODELS_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# See groq.py's comment in the text package: Transport's own default client
# timeout is far too short for a real generation call - matches the legacy
# backend's own Settings.ImageTimeout default (180s).
_GENERATE_TIMEOUT_SECONDS = 180.0

# Both the Imagen :predict endpoint and the native :generateContent
# imageConfig accept these three - see module docstring.
_ASPECT_RATIOS = {"1:1", "9:16", "16:9"}

# Preview image model ids Google shut down 2026-06-25 -> their GA
# equivalents. Ported verbatim from the legacy B2.17.9 remap.
_DEAD_MODEL_REMAP = {
    "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
}


class GeminiImageProvider:
    name = "gemini"

    def __init__(self, *, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else Transport()

    def supports(self, aspect_ratio: str) -> bool:
        return aspect_ratio in _ASPECT_RATIOS

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        key = get_key(self.name)
        if not key:
            raise AvgError(ErrorKind.auth, "Gemini API key is not set")
        if aspect_ratio not in _ASPECT_RATIOS:
            raise AvgError(ErrorKind.bug, f"Gemini: unsupported aspect_ratio {aspect_ratio!r}")

        model_id = model.removeprefix("models/")
        model_id = _DEAD_MODEL_REMAP.get(model_id.lower(), model_id)

        if "imagen" in model_id.lower():
            return self._predict(model_id, prompt, aspect_ratio, key)
        return self._generate_content(model_id, prompt, aspect_ratio, key)

    def _predict(self, model_id: str, prompt: str, aspect_ratio: str, key: str) -> bytes:
        url = f"{_MODELS_BASE}/{quote(model_id, safe='')}:predict?key={quote(key, safe='')}"
        response = self._transport.request(
            "POST",
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "instances": [{"prompt": prompt}],
                "parameters": {"sampleCount": 1, "aspectRatio": aspect_ratio, "outputMimeType": "image/jpeg"},
            },
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        predictions = response.json().get("predictions") or []
        if not predictions:
            raise AvgError(ErrorKind.bug, f"Gemini returned no image data (model={model_id})")
        first = predictions[0]
        b64 = (
            first.get("bytesBase64Encoded")
            or first.get("imageBytes")
            or (first.get("image") or {}).get("imageBytes")
        )
        if not b64:
            raise AvgError(ErrorKind.bug, f"Gemini returned no image data (model={model_id})")
        return base64.b64decode(b64)

    def _generate_content(self, model_id: str, prompt: str, aspect_ratio: str, key: str) -> bytes:
        url = f"{_MODELS_BASE}/{quote(model_id, safe='')}:generateContent?key={quote(key, safe='')}"
        response = self._transport.request(
            "POST",
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": aspect_ratio},
                },
            },
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        payload = response.json()
        candidates = payload.get("candidates") or []
        b64 = _extract_inline_image(candidates)
        if b64:
            return base64.b64decode(b64)

        finish_reason = str(candidates[0].get("finishReason", "")) if candidates else ""
        feedback = payload.get("promptFeedback")
        detail = f"finishReason={finish_reason} promptFeedback={feedback}"
        kind = classify(200, detail, None, self.name)
        raise AvgError(kind, f"Gemini returned no image data ({detail})")


def _extract_inline_image(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    for part in content.get("parts") or []:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return str(inline["data"])
    return ""
