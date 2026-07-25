"""The `TextBackend` protocol, plus the one piece of response-handling every
backend needs and would otherwise duplicate four times.

Each backend owns its own endpoints, request shape, and response parsing -
that is provider knowledge, and belongs in `groq.py` / `gemini.py` /
`huggingface.py` / `pollinations.py`, not here. What *is* shared: turning a
non-2xx `httpx.Response` into the right `AvgError`.

That sharing is more than a DRY convenience - it is load-bearing for replay
mode. `Transport.request()` behaves differently by mode:

  - **record mode** never returns a failing response to its caller. A
    non-2xx status is retried per `POLICY` and, once exhausted, raised as
    `AvgError` from *inside* `Transport` - so a fixture is only ever written
    for a call that ultimately succeeded (see `transport.py`'s
    `_send_with_retry`). There is structurally no way to record a fixture
    file for a failure.
  - **replay mode** has no retry loop at all - it deserialises whatever
    status code a fixture holds and hands it straight back, including a
    non-2xx one. That only happens for the hand-written error-shape
    fixtures this task records outside the normal record path (a real
    request captured with the raw client, or a synthetic one built from
    Task 1's mined patterns) - see `.superpowers/sdd/s3-task-3-report.md`.

So a backend cannot assume `transport.request(...)` succeeded just because
it returned. `raise_for_status` is the one place that gap is closed: every
backend calls it immediately after the transport call, before touching the
response body.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

from avg.core.errors import AvgError
from avg.providers.errors import classify
from avg.providers.transport import Transport


@runtime_checkable
class TextBackend(Protocol):
    """One text-generation provider.

    No retry/backoff belongs on an implementation of this protocol - that
    is `avg.providers.transport.Transport`'s job (record mode) or simply
    not applicable (replay mode has nothing to retry). A backend's whole
    job is: build one request for `prompt`/`model`, parse one response,
    return the text or raise `AvgError`.

    The constructor signature is part of the protocol (not just `name`/
    `generate`/`list_models`) so `router.py` can construct any backend by
    name through a plain `dict[str, type[TextBackend]]` - both `transport`
    (every test in this package injects a replay-mode one) and `cache_path`
    (`avg.providers.models`' on-disk cache location) are how tests keep a
    backend from ever touching the real network or the real user's
    `%APPDATA%`.
    """

    name: str

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        cache_path: Path | str | None = None,
    ) -> None: ...

    def generate(
        self,
        prompt: str,
        model: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """Return generated text, or raise `AvgError` describing the failure."""
        ...

    def list_models(self) -> list[str]:
        """The provider's available text models, live-fetched-then-cached.

        Implementations delegate to `avg.providers.models.get_models` -
        this method never returns a hardcoded list.
        """
        ...


def raise_for_status(response: httpx.Response, provider: str) -> None:
    """Raise `AvgError` if `response` represents a provider failure.

    A no-op for any status under 400. See this module's docstring for why
    a backend must call this explicitly rather than trusting that a
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
