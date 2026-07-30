"""Ask each of a provider's models to say one word, and see which answer.

The port of `Invoke-WorkingModelsCacheUpdate`
(`D:\\AI_Video_Gen\\modules\\visuals.ps1`): list a keyed provider's models,
send each the smallest generation the API allows, and record a bool. The
result feeds `avg.core.model_cache.ModelCache`, which is what turns the
settings screen's free-text model fields into a picker of models that are
known to work.

Three properties are load-bearing, and all three come from the legacy
function:

  - **No key means no probe.** A provider with no key returns `{}` rather
    than a sweep of failures - otherwise every model would be marked broken
    for a reason that has nothing to do with the model.
  - **One dead model never aborts the sweep.** Every failure is caught and
    recorded as `False`; the loop always finishes.
  - **`False` is recorded, not dropped.** The cache stores both verdicts so
    the UI can say "probed and broken" rather than "never probed".

This is the only module in this slice allowed to touch the network, and it
does so exclusively through an injectable `Transport` - so no test can
reach a real provider.
"""

from __future__ import annotations

from typing import Iterable

import httpx

from avg.config.secrets import get_key
from avg.providers import catalogues
from avg.providers.text.base import TextBackend
from avg.providers.text.router import _BACKENDS, _build_backend
from avg.providers.transport import Transport

#: The prompt every probe sends. Deliberately trivial and answerable in a
#: single token: this measures whether the model responds at all, and a
#: longer prompt would cost more on the paid providers for no more signal.
PROBE_PROMPT = "Reply with the word ok."


#: Per-request ceiling for a probe. Generous enough for a cold model to
#: answer one token, short enough that twenty dead ones cannot hold the
#: sweep open for ten minutes. Observed: a Hugging Face sweep with the
#: client's default timeout never finished at all - several listed models
#: sit there loading, and a probe that waits for them is measuring the
#: wrong thing anyway. "Did not answer promptly" is the same verdict as
#: "did not answer" for a control whose whole job is to offer a model the
#: user can pick right now.
PROBE_TIMEOUT_S = 20.0


def _probe_transport() -> Transport:
    """A live transport built for a sweep: bounded, and never sleeping.

    `POLICY`'s backoff exists so one important call survives a blip. A
    sweep is the opposite case - dozens of calls, most of them expected to
    fail - and every failure classified `rate_limit` or `quota` slept
    through its exponential backoff before giving up, turning a sweep of
    ~60 models into minutes of mostly sleeping. The retries still happen;
    they just happen immediately.
    """
    return Transport(
        mode="live",
        client=httpx.Client(follow_redirects=False, timeout=PROBE_TIMEOUT_S),
        sleep=lambda _s: None,
    )


def known_providers() -> list[str]:
    """The text providers that can be probed at all.

    Read from the router's backend table rather than restated here -
    `avg/providers/text/router.py` is already the one place that knows
    which provider names map to an implementation.
    """
    return sorted(_BACKENDS)


def list_models(provider: str, transport: Transport | None) -> list[str]:
    """The provider's advertised models.

    A thin seam over the backend's own `list_models()` so a caller (and a
    test) has one name to reach for, and so nothing here grows a second
    model lister - `avg.providers.models` already owns fetch-then-cache.
    """
    backend: TextBackend = _build_backend(provider, transport)
    return backend.list_models()


def probe_provider(provider: str, *, transport: Transport | None = None) -> dict[str, bool]:
    """Probe every model `provider` advertises. `{model_id: worked}`.

    Never raises: an unknown provider, a missing key, an unreachable list
    endpoint and a dead model all resolve to an empty or partial result,
    because this runs as a sweep over every configured provider and one
    bad entry must not cost the rest.
    """
    if provider not in _BACKENDS:
        return {}
    if not get_key(provider):
        # No key means no call at all - never a probe that fails for the
        # wrong reason and marks every model broken.
        return {}

    # Only close what this function opened - an injected transport belongs
    # to the caller and may be reused across providers.
    owned = transport is None
    transport = transport if transport is not None else _probe_transport()
    try:
        try:
            models = list_models(provider, transport)
        except Exception:
            return {}

        results: dict[str, bool] = {}
        for model in models:
            results[model] = _model_answers(provider, model, transport)
        return results
    finally:
        if owned:
            transport.close()


def _model_answers(provider: str, model: str, transport: Transport | None) -> bool:
    """One model, one minimal generation. Any failure at all is `False`.

    Deliberately does not distinguish *why* a model failed. A 404 (retired
    model), a 429 (out of quota on this model tier) and a timeout are the
    same answer to the question the cache asks - "can I pick this today?"
    - and the error taxonomy already exists for the cases where the
    difference matters.
    """
    try:
        backend = _build_backend(provider, transport)
        text = backend.generate(PROBE_PROMPT, model, max_tokens=1, temperature=0.0)
    except Exception:
        return False
    return isinstance(text, str)


def probe_all(
    providers: Iterable[str] | None = None, *, transport: Transport | None = None
) -> dict[str, dict[str, bool]]:
    """Probe several providers. Unkeyed ones come back as an empty dict.

    The empty entry is kept rather than omitted: "keyed but nothing
    works" and "no key" are different states, and the UI has to be able
    to say which.
    """
    names = list(providers) if providers is not None else known_providers()
    return {name: probe_provider(name, transport=transport) for name in names}


def probe_models(
    selection: dict[str, list[str]], *, transport: Transport | None = None
) -> dict[str, dict[str, bool]]:
    """Probe exactly the models named, provider by provider. `{provider: {model: worked}}`.

    The difference from `probe_all` is the whole point of the Live List: a
    sweep that tests everything a provider advertises spends a call per
    listed model on models the user will never choose. Here the caller
    passes the shortlist and nothing else is touched - no catalogue is even
    fetched, because the caller already knows the ids.

    Never raises, for the same reason `probe_all` does not: this runs as a
    sweep and one dead provider must not cost the rest. An unkeyed or
    unknown provider comes back as an empty dict, which reads as "nothing
    proved" rather than "everything broken".
    """
    owned = transport is None
    transport = transport if transport is not None else _probe_transport()
    try:
        results: dict[str, dict[str, bool]] = {}
        for provider, models in selection.items():
            if provider not in _BACKENDS or not get_key(provider):
                # No key means no call at all - never a probe that fails for
                # the wrong reason and marks every model broken.
                results[provider] = {}
                continue
            results[provider] = {
                model: _model_answers(provider, model, transport) for model in models
            }
        return results
    finally:
        if owned:
            transport.close()


def check_reachable(
    kind: str, provider: str, *, transport: Transport | None = None
) -> bool | None:
    """Did this provider answer its own catalogue? `None` means untested.

    Three values, and the third is the one that matters:

      - `True`  - the provider answered with this key. THIS MODEL is still
                  unproven; reachability is a fact about a provider.
      - `False` - it has a catalogue endpoint and that endpoint did not answer.
      - `None`  - there was nothing to ask. OpenAI and Gemini publish no
                  image catalogue at all, and an unkeyed ElevenLabs has
                  nothing to say - reporting either `unreachable` would be a
                  verdict nothing earned.

    **Nothing here costs money.** Probing an image model means paying for an
    image and probing an ElevenLabs voice costs characters, which is exactly
    why Slice 8 made images and voices listed rather than probed. A
    catalogue GET is free on every provider that has one.
    """
    if kind == "image":
        if provider not in catalogues.image_providers():
            return None
        return bool(catalogues.list_image_models(provider, transport=transport))
    if kind == "voice":
        if provider == "elevenlabs" and not get_key("elevenlabs"):
            return None
        return bool(catalogues.list_voices(provider, transport=transport))
    return None
