"""The `ImageProvider` protocol, plus the one piece of response-handling
every provider needs and would otherwise duplicate five times.

Mirrors `avg/providers/text/base.py`'s shape and reasoning exactly - see
that module's docstring for the full explanation of *why* a provider
cannot assume a response it got back from `Transport.request()` succeeded
(record mode never returns a failing response; replay mode has no retry
loop and hands back whatever status a fixture holds). This copy exists
rather than importing the text package's version so `avg.providers.images`
has no dependency on `avg.providers.text` - the two provider kinds are
siblings, not one built on the other.

Each provider owns its own endpoint(s), request shape, response parsing,
and aspect-ratio handling - that is provider knowledge, and belongs in
`openai.py` / `gemini.py` / `pollinations.py` / `huggingface.py` /
`stablehorde.py`, not here.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from avg.core.errors import AvgError
from avg.providers.errors import classify
from avg.providers.transport import Transport


@runtime_checkable
class ImageProvider(Protocol):
    """One image-generation provider.

    No retry/backoff belongs on an implementation of this protocol - that
    is `avg.providers.transport.Transport`'s job (record mode) or simply
    not applicable (replay mode has nothing to retry). A provider's whole
    job is: build one request (or, for Stable Horde, one submit-then-poll
    sequence) for `prompt`/`aspect_ratio`/`model`, parse the response, and
    return raw image bytes or raise `AvgError`.

    The constructor signature is part of the protocol (just `transport`,
    unlike `TextBackend` - no `cache_path`, since this package has no
    `list_models()` equivalent) so callers can construct any provider by
    name through a plain `dict[str, type[ImageProvider]]`, the same
    pattern `avg/providers/text/router.py` uses.
    """

    name: str

    def __init__(self, *, transport: Transport | None = None) -> None: ...

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        """Return raw image bytes, or raise `AvgError` describing the failure."""
        ...

    def supports(self, aspect_ratio: str) -> bool:
        """Whether this provider can natively produce `aspect_ratio`
        (this package's three canonical ratio strings: `"1:1"`, `"9:16"`,
        `"16:9"`).

        A provider that cannot produce a ratio natively (Hugging Face's
        hf-inference text-to-image pipeline only ever returns whatever
        square size a model was trained/hosted at) refuses here rather
        than silently stretching or center-cropping a mismatched image
        into the requested shape - callers ask for `9:16` and get `9:16`,
        or get a clear `False` from this method before ever calling
        `generate()`.
        """
        ...


def raise_for_status(response: httpx.Response, provider: str) -> None:
    """Raise `AvgError` if `response` represents a provider failure.

    A no-op for any status under 400. See this module's docstring for why
    a provider must call this explicitly rather than trusting that a
    response it received back from `Transport.request()` was a success.
    """
    if response.status_code < 400:
        return
    body_text = response.text
    kind = classify(response.status_code, body_text, None, provider)
    raise AvgError(
        kind,
        f"{provider} request failed with HTTP {response.status_code}",
        detail=body_text,
    )
