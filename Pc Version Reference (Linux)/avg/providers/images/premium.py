"""The two paid image backends: Gemini and OpenAI.

Both bill per image against an account key, which is the distinction that
groups them - see `community.py` for the three free ones. As of 2026-07
neither of these works on this project's accounts (Gemini is at zero
free-tier quota, OpenAI has no billing set up), so the free providers are
what actually renders video today.

`base.py` (the `ImageProvider` protocol and `raise_for_status`) stays
separate - it is the contract, not an implementation of it.
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

# Both backends: Transport's own default client timeout is far too short for
# a real generation call - matches the legacy backends' own
# Settings.ImageTimeout default (180s).
_GENERATE_TIMEOUT_SECONDS = 180.0


# ── Gemini Image Provider ───────────────────────────────────
#
# Mirrors the legacy `Invoke-GeminiImageGeneration` (`D:\AI_Video_Gen\modules\
# visuals.ps1`, line 775): key sent both as the `x-goog-api-key` header and a
# `key=` query parameter (the same genuine REST-API quirk already ported in
# `avg/providers/text/gemini.py`), and two real APIs behind one model
# catalogue, routed by model id - the legacy function's own B2.17.9 fix,
# discovered the hard way when Google split Gemini image generation in two:
#
#   - Imagen models (`imagen-*`) use the Vertex-style `:predict` endpoint -
#     `{instances:[{prompt}], parameters:{sampleCount, aspectRatio,
#     outputMimeType}}`, response `predictions[].bytesBase64Encoded` (or one
#     of two older field names the legacy code also checks).
#   - Native Gemini image models (`gemini-*-image`, "Nano Banana") use
#     `:generateContent`, like the text API -
#     `{contents:[...], generationConfig:{responseModalities:['TEXT',
#     'IMAGE'], imageConfig:{aspectRatio}}}`, response
#     `candidates[0].content.parts[].inlineData.data`.
#
# Also ported: the `*-image-preview` -> GA model-id remap. Google shut the
# preview ids down on 2026-06-25; a stale config pointing at one 404s, which
# looked like "Gemini image gen just stopped working" until the legacy code
# traced it to this. Auto-remapping keeps an old config working without a
# manual edit.
#
# NOT ported: the legacy function's blind "try the request WITH aspectRatio;
# if that whole call raises anything at all, retry WITHOUT it" - a bare
# try/except around the entire native-model request with no inspection of
# what failed, there to tolerate older/preview models that reject
# `imageConfig.aspectRatio`. Skipped here for two reasons: it would mean up
# to two real HTTP calls per `generate()` (every other provider in this
# package makes exactly one), and both model families this provider serves
# today (Imagen, current GA Gemini image models) accept `aspectRatio`.
#
# A 200 with no image - the API's own in-band failure signal, a blocked
# prompt (`promptFeedback.blockReason`) or a candidate that didn't finish
# cleanly (`finishReason`) - is classified from that detail text via
# `avg.providers.errors.classify`, the same treatment the text Gemini
# backend gives an empty `candidates` list.

_GEMINI_MODELS_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Both the Imagen :predict endpoint and the native :generateContent
# imageConfig accept these three - see the section comment above.
_ASPECT_RATIOS = {"1:1", "9:16", "16:9"}

# Preview image model ids Google shut down 2026-06-25 -> their GA
# equivalents. Ported verbatim from the legacy B2.17.9 remap.
_DEAD_MODEL_REMAP = {
    "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
}


def _extract_inline_image(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    for part in content.get("parts") or []:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return str(inline["data"])
    return ""


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
        url = f"{_GEMINI_MODELS_BASE}/{quote(model_id, safe='')}:predict?key={quote(key, safe='')}"
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
        url = f"{_GEMINI_MODELS_BASE}/{quote(model_id, safe='')}:generateContent?key={quote(key, safe='')}"
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


# ── OpenAI Image Provider ───────────────────────────────────
#
# Mirrors the legacy `Invoke-OpenAIImageGeneration` (`D:\AI_Video_Gen\modules\
# visuals.ps1`, line 720): `POST /v1/images/generations`, `n=1`, response is
# either `b64_json` (decoded directly) or a `url` (fetched with a second
# request) - both paths ported, since the legacy function had to handle both.
#
# Ported: OpenAI's per-model size catalogue actually differs by model -
# `gpt-image-1` takes `1024x1024`/`1024x1536`/`1536x1024`; `dall-e-3` takes
# `1024x1024`/`1024x1792`/`1792x1024`; `dall-e-2` is square-only
# (`1024x1024`/`512x512`/`256x256`). `_size_for` maps this provider's
# protocol-level `aspect_ratio` onto the right absolute `size` string for
# whichever `model` is asked for, so a caller never has to know OpenAI's
# size catalogue itself.
#
# NOT ported: the legacy function's "retry without `quality` if OpenAI
# rejects it" fallback, which decided what to do next by regex-matching the
# error message returned from the first attempt. `avg.providers.errors.
# classify` is the only place a body string is ever examined (see
# `avg/providers/errors.py`'s module docstring) - re-deriving "did OpenAI
# reject this specific field" from error text here would be exactly the kind
# of ad hoc, per-call-site string matching that module exists to prevent.
# Instead, `quality` is only ever sent for models actually documented to
# accept it (`gpt-image-1`, `dall-e-3`); `dall-e-2` never receives the field,
# so there is nothing for it to reject.

_OPENAI_URL = "https://api.openai.com/v1/images/generations"

# A shorter timeout for the plain image download when the API answers with a
# `url` instead of inline base64.
_DOWNLOAD_TIMEOUT_SECONDS = 60.0

# Sizes each current OpenAI image model actually accepts, keyed by this
# package's canonical aspect-ratio strings. dall-e-2 has no non-square
# entry at all - handled explicitly in `_size_for` rather than silently
# falling back to a square it wasn't asked for.
_GPT_IMAGE_SIZES = {"1:1": "1024x1024", "9:16": "1024x1536", "16:9": "1536x1024"}
_DALLE3_SIZES = {"1:1": "1024x1024", "9:16": "1024x1792", "16:9": "1792x1024"}
_DALLE2_SIZE = "1024x1024"


def _size_for(model: str, aspect_ratio: str) -> str:
    model_l = model.lower()
    if "dall-e-2" in model_l:
        if aspect_ratio != "1:1":
            raise AvgError(
                ErrorKind.bug,
                f"OpenAI dall-e-2 only produces square images, got aspect_ratio={aspect_ratio!r}",
            )
        return _DALLE2_SIZE
    table = _DALLE3_SIZES if "dall-e-3" in model_l else _GPT_IMAGE_SIZES
    size = table.get(aspect_ratio)
    if size is None:
        raise AvgError(ErrorKind.bug, f"OpenAI: unsupported aspect_ratio {aspect_ratio!r}")
    return size


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, *, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else Transport()

    def supports(self, aspect_ratio: str) -> bool:
        # gpt-image-1's catalogue is the widest of the three models this
        # provider can serve; dall-e-2's square-only restriction is
        # enforced per-call in `generate()`/`_size_for` since it depends
        # on which `model` is actually requested, not on the provider as
        # a whole.
        return aspect_ratio in _GPT_IMAGE_SIZES

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        key = get_key(self.name)
        if not key:
            raise AvgError(ErrorKind.auth, "OpenAI API key is not set")

        size = _size_for(model, aspect_ratio)
        body: dict[str, object] = {"model": model, "prompt": prompt, "n": 1, "size": size}
        if "dall-e-2" not in model.lower():
            body["quality"] = "medium" if "gpt-image" in model.lower() else "standard"

        response = self._transport.request(
            "POST",
            _OPENAI_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        payload = response.json()
        data = payload.get("data") or []
        if not data:
            raise AvgError(ErrorKind.bug, "OpenAI returned no image data")
        first = data[0]

        b64 = first.get("b64_json")
        if b64:
            return base64.b64decode(b64)

        url = first.get("url")
        if url:
            img_response = self._transport.request(
                "GET", url, timeout=_DOWNLOAD_TIMEOUT_SECONDS, provider=self.name
            )
            raise_for_status(img_response, self.name)
            return img_response.content

        raise AvgError(ErrorKind.bug, "OpenAI returned no image data")
