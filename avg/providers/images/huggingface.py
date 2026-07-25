"""Hugging Face image backend.

Mirrors the legacy `Invoke-HuggingFaceImageGeneration` (`D:\\AI_Video_Gen\\
modules\\visuals.ps1`, line 906): `POST /hf-inference/models/<model>` on
HF's router, `Accept: image/png`, `x-wait-for-model: true`, body
`{"inputs": prompt}`. On success the response body IS the image (no JSON
envelope) - `Content-Type` starts with `image/`. Anything else is an
in-band failure: either a non-2xx (the shared `raise_for_status` already
turned it into an `AvgError` before this ever looks at `Content-Type`), or
- more unusually - a 200 whose body is a JSON error rather than image
bytes.

hf-inference's plain text-to-image pipeline has no width/height parameter
in the legacy call at all (`inputs` is the entire request body) - every
served model returns whatever fixed size it was trained/hosted at, always
square. `supports()` refuses anything but `1:1` rather than silently
stretching or center-cropping a mismatched aspect ratio into the requested
shape - the "clear refusal" alternative the brief describes for a
square-only provider, chosen over building a cropper for a provider this
package has no other reason to touch pixels for.

NOT ported: the legacy function's automatic model-fallback loop (retry with
a known-served model, `black-forest-labs/FLUX.1-schnell`, when the
configured one comes back "not supported by provider"). That decision -
"this failure means try a different model" - is exactly
`POLICY[ErrorKind.model_missing].on_exhausted == "switch_model"`'s job at a
router layer above individual providers (the same pattern `avg/providers/
text/router.py`'s per-phase backup already establishes for text), not
something a single provider should decide by grepping its own error text.
"""

from __future__ import annotations

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify
from avg.providers.images.base import raise_for_status
from avg.providers.transport import Transport

_URL_TEMPLATE = "https://router.huggingface.co/hf-inference/models/{model}"

# Matches the legacy backend's own Settings.ImageTimeout default (180s).
_GENERATE_TIMEOUT_SECONDS = 180.0


class HuggingFaceImageProvider:
    name = "huggingface"

    def __init__(self, *, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else Transport()

    def supports(self, aspect_ratio: str) -> bool:
        return aspect_ratio == "1:1"

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        key = get_key(self.name)
        if not key:
            raise AvgError(ErrorKind.auth, "Hugging Face API key is not set")
        if aspect_ratio != "1:1":
            raise AvgError(
                ErrorKind.bug,
                f"Hugging Face only produces square images, got aspect_ratio={aspect_ratio!r}",
            )

        response = self._transport.request(
            "POST",
            _URL_TEMPLATE.format(model=model),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "image/png",
                "x-wait-for-model": "true",
            },
            json={"inputs": prompt},
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            body_text = response.text
            kind = classify(response.status_code, body_text, None, self.name)
            raise AvgError(
                kind, f"Hugging Face returned no image (Content-Type: {content_type})", detail=body_text
            )

        return response.content
