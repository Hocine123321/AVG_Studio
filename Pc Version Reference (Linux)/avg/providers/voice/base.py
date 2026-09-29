"""The `VoiceProvider` protocol.

Mirrors `avg/providers/text/base.py` and `avg/providers/images/base.py` in
shape and reasoning - see either module's docstring for the full
explanation of why a provider cannot assume a response it got back from
`Transport.request()` succeeded (record mode never returns a failing
response; replay mode has no retry loop and hands back whatever status a
fixture holds). This copy exists rather than importing either sibling
package's version so `avg.providers.voice` has no dependency on
`avg.providers.text` or `avg.providers.images` - three sibling packages,
none built on another.

Unlike `TextBackend`/`ImageProvider`, this protocol does NOT pin down a
`transport`-shaped constructor. The two providers in this package need
genuinely different constructors: `ElevenLabsVoiceProvider` is an HTTP
provider and takes a `Transport`; `EdgeVoiceProvider` never touches
`avg.providers.transport` at all (it speaks to Microsoft's endpoint over a
websocket via the `edge-tts` package - see that module's docstring), so
giving it a `transport` parameter it would never use would be a lie about
what the parameter does. What every provider *does* share is `name`,
`synthesize`, and `list_voices` - that shared surface is this Protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from avg.core.errors import AvgError
from avg.providers.errors import classify


@runtime_checkable
class VoiceProvider(Protocol):
    """One text-to-speech provider.

    No retry/backoff belongs on an implementation of this protocol for the
    HTTP-based case - that is `avg.providers.transport.Transport`'s job
    (record mode) or simply not applicable (replay mode has nothing to
    retry). `ElevenLabsVoiceProvider` additionally owns its own primary/
    backup *key* switch (see that module's docstring for the exact rule) -
    that is provider-specific account logic, not a retry, and does not
    belong here or in any shared orchestrator.
    """

    name: str

    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        """Return raw audio bytes, or raise `AvgError` describing the failure."""
        ...

    def list_voices(self) -> list[dict[str, str]]:
        """The provider's available voices as `{"voice_id": ..., "name": ...}`
        dicts (extra provider-specific keys, e.g. Edge's `locale`/`gender`,
        may also be present). Never a hardcoded list - always fetched from
        the provider's own live/local catalogue.
        """
        ...


def raise_for_status(response: httpx.Response, provider: str) -> None:
    """Raise `AvgError` if `response` represents a provider failure.

    A no-op for any status under 400. See this module's docstring (and
    `text/base.py`'s, in more detail) for why a provider must call this
    explicitly rather than trusting that a response it received back from
    `Transport.request()` was a success.
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
