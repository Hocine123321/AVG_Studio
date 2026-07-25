"""Pollinations image backend.

Mirrors the legacy `Invoke-PollinationsImageGeneration` (`D:\\AI_Video_Gen\\
modules\\visuals.ps1`, line 864): `GET https://gen.pollinations.ai/image/
<url-encoded prompt>?model=&width=&height=&safe=&negative_prompt=...`. The
response body IS the image itself - no JSON envelope, no base64 - unlike
every other provider in this package except Hugging Face.

Ported: the "foreign model id" guard. Pollinations only accepts its own
short model names (`flux`, `kontext`, `seedream`, ...) - a namespaced HF id
or another provider's model name left over from switching providers
(`gpt-image-1`, `dall-e-3`, `black-forest-labs/FLUX.1-schnell`, ...) gets a
plain 400 that failed the *whole* phase in the legacy app. Normalising any
obviously-foreign model to `flux` (the legacy guard's own fallback)
prevents that. Also ported: the default `negative_prompt` and `safe=true`,
the legacy config's own defaults for this provider.

Key optional, same as the text Pollinations backend (`avg/providers/text/
pollinations.py`) - Pollinations works keyless, and a missing key is never
`ErrorKind.auth` here.

Aspect ratio is a width/height pair here, not a ratio string or an enum -
`_SIZE_BY_RATIO` picks round, multiple-of-8 dimensions for each of this
package's three canonical ratios (Pollinations' models are far less picky
about exact dimensions than Stable Horde's SDXL-based ones - see
`stablehorde.py`, which needs multiples of 64 instead and so uses its own,
different table).
"""

from __future__ import annotations

import re
from urllib.parse import quote

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.images.base import raise_for_status
from avg.providers.transport import Transport

_BASE_URL = "https://gen.pollinations.ai/image"

# Matches the legacy backend's own Settings.ImageTimeout default (180s).
_GENERATE_TIMEOUT_SECONDS = 180.0

_SIZE_BY_RATIO = {"1:1": (1024, 1024), "9:16": (768, 1344), "16:9": (1344, 768)}

_DEFAULT_NEGATIVE_PROMPT = "text, watermark, logo, blurry, low quality"

# Matches the legacy guard: reject anything that looks like another
# provider's namespaced/foreign model id, fall back to Pollinations' own
# 'flux'.
_FOREIGN_MODEL_RE = re.compile(
    r"/|^(gpt-image|dall-e|imagen|gemini|black-forest-labs|stabilityai|sdxl|albedo)", re.IGNORECASE
)


def _normalize_model(model: str) -> str:
    model = (model or "").strip()
    if not model or _FOREIGN_MODEL_RE.search(model):
        return "flux"
    return model


class PollinationsImageProvider:
    name = "pollinations"

    def __init__(self, *, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else Transport()

    def supports(self, aspect_ratio: str) -> bool:
        return aspect_ratio in _SIZE_BY_RATIO

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        size = _SIZE_BY_RATIO.get(aspect_ratio)
        if size is None:
            raise AvgError(ErrorKind.bug, f"Pollinations: unsupported aspect_ratio {aspect_ratio!r}")
        width, height = size

        headers = {}
        key = get_key(self.name)
        if key:
            headers["Authorization"] = f"Bearer {key}"

        params = {
            "model": _normalize_model(model),
            "width": str(width),
            "height": str(height),
            "safe": "true",
            "negative_prompt": _DEFAULT_NEGATIVE_PROMPT,
        }
        query = "&".join(f"{quote(k)}={quote(v)}" for k, v in params.items())
        url = f"{_BASE_URL}/{quote(prompt, safe='')}?{query}"

        response = self._transport.request(
            "GET", url, headers=headers, timeout=_GENERATE_TIMEOUT_SECONDS, provider=self.name
        )
        raise_for_status(response, self.name)
        if not response.content:
            raise AvgError(ErrorKind.bug, "Pollinations returned no image data")
        return response.content
