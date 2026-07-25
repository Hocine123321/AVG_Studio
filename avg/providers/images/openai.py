"""OpenAI image backend.

Mirrors the legacy `Invoke-OpenAIImageGeneration` (`D:\\AI_Video_Gen\\modules\\
visuals.ps1`, line 720): `POST /v1/images/generations`, `n=1`, response is
either `b64_json` (decoded directly) or a `url` (fetched with a second
request) - both paths ported, since the legacy function had to handle both.

Ported: OpenAI's per-model size catalogue actually differs by model -
`gpt-image-1` takes `1024x1024`/`1024x1536`/`1536x1024`; `dall-e-3` takes
`1024x1024`/`1024x1792`/`1792x1024`; `dall-e-2` is square-only
(`1024x1024`/`512x512`/`256x256`). `_size_for` maps this provider's
protocol-level `aspect_ratio` onto the right absolute `size` string for
whichever `model` is asked for, so a caller never has to know OpenAI's
size catalogue itself.

NOT ported: the legacy function's "retry without `quality` if OpenAI
rejects it" fallback, which decided what to do next by regex-matching the
error message returned from the first attempt. `avg.providers.errors.
classify` is the only place a body string is ever examined (see
`avg/providers/errors.py`'s module docstring) - re-deriving "did OpenAI
reject this specific field" from error text here would be exactly the kind
of ad hoc, per-call-site string matching that module exists to prevent.
Instead, `quality` is only ever sent for models actually documented to
accept it (`gpt-image-1`, `dall-e-3`); `dall-e-2` never receives the field,
so there is nothing for it to reject.
"""

from __future__ import annotations

import base64

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.images.base import raise_for_status
from avg.providers.transport import Transport

_URL = "https://api.openai.com/v1/images/generations"

# Matches the legacy backend's own -TimeoutSec (Settings.ImageTimeout,
# default 180s in the legacy config) / a shorter one for the plain image
# download when the API answers with a `url` instead of inline base64.
_GENERATE_TIMEOUT_SECONDS = 180.0
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
            _URL,
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
