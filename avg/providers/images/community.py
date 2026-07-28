"""The three free/community image backends: Hugging Face, Pollinations,
Stable Horde.

None of them bills per image, which is the distinction that groups them -
see `premium.py` for Gemini and OpenAI. As of 2026-07 **Pollinations and
Stable Horde are what actually renders video on this project's accounts**;
Hugging Face returns `model_not_supported` and both paid providers are
unusable.

They are otherwise quite different, and the differences are load-bearing:
Hugging Face is square-only and returns raw image bytes, Pollinations puts
the whole request in a URL and works keyless, and Stable Horde is the only
asynchronous provider in the package (submit, poll a community queue,
fetch). Each section below keeps its own constants; in particular
Pollinations and Stable Horde keep **separate** `_SIZE_BY_RATIO` tables
even though their current values coincide - the two APIs constrain
dimensions differently (multiples of 8 vs multiples of 64) and a test
monkeypatches Stable Horde's table in isolation.

`base.py` (the `ImageProvider` protocol and `raise_for_status`) stays
separate - it is the contract, not an implementation of it.
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any, Callable
from urllib.parse import quote

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify
from avg.providers.images.base import raise_for_status
from avg.providers.transport import Transport

# Hugging Face and Pollinations both match the legacy backends' own
# Settings.ImageTimeout default (180s). Stable Horde is asynchronous and
# has its own per-request timeouts in its section below.
_GENERATE_TIMEOUT_SECONDS = 180.0


# ── Hugging Face Image Provider ─────────────────────────────
#
# Mirrors the legacy `Invoke-HuggingFaceImageGeneration` (`D:\AI_Video_Gen\
# modules\visuals.ps1`, line 906): `POST /hf-inference/models/<model>` on
# HF's router, `Accept: image/png`, `x-wait-for-model: true`, body
# `{"inputs": prompt}`. On success the response body IS the image (no JSON
# envelope) - `Content-Type` starts with `image/`. Anything else is an
# in-band failure: either a non-2xx (the shared `raise_for_status` already
# turned it into an `AvgError` before this ever looks at `Content-Type`), or
# - more unusually - a 200 whose body is a JSON error rather than image
# bytes.
#
# hf-inference's plain text-to-image pipeline has no width/height parameter
# in the legacy call at all (`inputs` is the entire request body) - every
# served model returns whatever fixed size it was trained/hosted at, always
# square. `supports()` refuses anything but `1:1` rather than silently
# stretching or center-cropping a mismatched aspect ratio into the requested
# shape - the "clear refusal" alternative the brief describes for a
# square-only provider, chosen over building a cropper for a provider this
# package has no other reason to touch pixels for.
#
# NOT ported: the legacy function's automatic model-fallback loop (retry with
# a known-served model, `black-forest-labs/FLUX.1-schnell`, when the
# configured one comes back "not supported by provider"). That decision -
# "this failure means try a different model" - is exactly
# `POLICY[ErrorKind.model_missing].on_exhausted == "switch_model"`'s job at a
# router layer above individual providers (the same pattern `avg/providers/
# text/router.py`'s per-phase backup already establishes for text), not
# something a single provider should decide by grepping its own error text.

_HF_URL_TEMPLATE = "https://router.huggingface.co/hf-inference/models/{model}"


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
            _HF_URL_TEMPLATE.format(model=model),
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


# ── Pollinations Image Provider ─────────────────────────────
#
# Mirrors the legacy `Invoke-PollinationsImageGeneration` (`D:\AI_Video_Gen\
# modules\visuals.ps1`, line 864): `GET https://gen.pollinations.ai/image/
# <url-encoded prompt>?model=&width=&height=&safe=&negative_prompt=...`. The
# response body IS the image itself - no JSON envelope, no base64 - unlike
# every other provider in this package except Hugging Face.
#
# Ported: the "foreign model id" guard. Pollinations only accepts its own
# short model names (`flux`, `kontext`, `seedream`, ...) - a namespaced HF id
# or another provider's model name left over from switching providers
# (`gpt-image-1`, `dall-e-3`, `black-forest-labs/FLUX.1-schnell`, ...) gets a
# plain 400 that failed the *whole* phase in the legacy app. Normalising any
# obviously-foreign model to `flux` (the legacy guard's own fallback)
# prevents that. Also ported: the default `negative_prompt` and `safe=true`,
# the legacy config's own defaults for this provider.
#
# Key optional, same as the text Pollinations backend (`avg/providers/text/
# community.py`) - Pollinations works keyless, and a missing key is never
# `ErrorKind.auth` here.
#
# Aspect ratio is a width/height pair here, not a ratio string or an enum -
# `_POLLINATIONS_SIZE_BY_RATIO` picks round, multiple-of-8 dimensions for
# each of this package's three canonical ratios (Pollinations' models are far
# less picky about exact dimensions than Stable Horde's SDXL-based ones - see
# the Stable Horde section below, which needs multiples of 64 instead and so
# keeps its own, separate table).

_POLLINATIONS_BASE_URL = "https://gen.pollinations.ai/image"

_POLLINATIONS_SIZE_BY_RATIO = {"1:1": (1024, 1024), "9:16": (768, 1344), "16:9": (1344, 768)}

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
        return aspect_ratio in _POLLINATIONS_SIZE_BY_RATIO

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        size = _POLLINATIONS_SIZE_BY_RATIO.get(aspect_ratio)
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
        url = f"{_POLLINATIONS_BASE_URL}/{quote(prompt, safe='')}?{query}"

        response = self._transport.request(
            "GET", url, headers=headers, timeout=_GENERATE_TIMEOUT_SECONDS, provider=self.name
        )
        raise_for_status(response, self.name)
        if not response.content:
            raise AvgError(ErrorKind.bug, "Pollinations returned no image data")
        return response.content


# ── Stable Horde Image Provider ─────────────────────────────
#
# The one provider in this package that is asynchronous: submit a job, then
# poll a shared community queue until it completes.
#
# Mirrors the legacy `Invoke-StableHordeImageGeneration` (`D:\AI_Video_Gen\
# modules\visuals.ps1`, line 977): `POST /api/v2/generate/async` to submit,
# then `GET /api/v2/generate/check/<id>` on a growing interval (starts at 4s,
# +1s per poll, capped at 8s - ported verbatim) until the job is `done` or
# `faulted`, then `GET /api/v2/generate/status/<id>` for the final result.
# `r2=true` asks the API to return a CDN URL instead of inline base64
# (avoids a huge response body); this backend downloads that URL when
# present, falling back to inline base64 otherwise - both paths ported since
# the legacy function handles both.
#
# Free and keyless-capable (`'0000000000'` is the community's anonymous key,
# ported here as this provider's default when no key is configured), but
# with failure modes none of the synchronous providers in this package have:
#
#   - **faulted** - no worker picked the job up, or (the API's own genuinely
#     ambiguous flag) a content decision. `avg/providers/errors.py`'s module
#     comment documents in detail why this deliberately does NOT get guessed
#     at a more specific kind: it is run through `classify()` from generic
#     text and falls through to `ErrorKind.bug`, the safe default - guessing
#     wrong in either direction (retrying a real content block, or failing a
#     transient capacity gap) is worse.
#   - **censored** - an explicit, unambiguous flag on a completed generation,
#     checked separately from `faulted` (matching the legacy code's own
#     separate `if ($gen.censored)` branch) - classifies as
#     `ErrorKind.content_filter`.
#   - **timed out** - this provider's own poll budget (`max_wait_seconds`,
#     default matching the legacy `images.image_timeout_seconds` config
#     default of 180) is exceeded before the job finishes - classifies as
#     `ErrorKind.timeout`. The stalled job is cancelled (a best-effort
#     DELETE, swallowing any failure - matching the legacy function's own
#     `-EA SilentlyContinue`) before raising.
#
# The poll loop cannot spin forever: it is bounded by `max_wait_seconds`
# regardless of what the API itself reports, and accepts an optional
# `cancel` callback - checked once per iteration - so a caller can abort a
# long wait early, cancelling the remote job the same way a timeout does
# rather than abandoning it silently server-side. `sleep` is injectable
# (mirroring `avg.providers.transport.Transport`'s own constructor) so tests
# can drive the poll loop with an instant fake instead of real wall-clock
# delays.
#
# The API also reports an ETA (`wait_time`) and a queue position on every
# `check` response - real information a caller may want to surface to a
# user during a wait that can run into minutes. Rather than this module
# deciding how (or whether) to display that, `on_progress` - an optional
# callback invoked once per poll with `(wait_time_seconds, queue_position)`
# - lets the caller decide. This module itself never logs or prints
# anything.
#
# Width/height (Stable Horde's SDXL-based models want multiples of 64,
# stricter than Pollinations' multiples of 8) are derived from
# `aspect_ratio` the same way the Pollinations section above derives its own
# - but from a separate table, since the two APIs prefer different absolute
# numbers even where today's values happen to agree.

_STABLEHORDE_BASE_URL = "https://aihorde.net/api/v2"

# The community's own documented anonymous key - ported verbatim from the
# legacy config default (`api_keys.stablehorde = '0000000000'`).
_ANONYMOUS_KEY = "0000000000"

_SUBMIT_TIMEOUT_SECONDS = 60.0
_POLL_TIMEOUT_SECONDS = 30.0
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
_CANCEL_TIMEOUT_SECONDS = 15.0

# Matches the legacy poll cadence: start at 4s, +1s per poll, cap at 8s.
_POLL_START_SECONDS = 4.0
_POLL_MAX_SECONDS = 8.0
_POLL_STEP_SECONDS = 1.0

# Matches the legacy `images.image_timeout_seconds` config default.
_DEFAULT_MAX_WAIT_SECONDS = 180.0

# Stable Horde / SDXL wants multiples of 64 - a different bucket set than
# Pollinations' multiples of 8 (see the section comment above).
_SIZE_BY_RATIO = {"1:1": (1024, 1024), "9:16": (768, 1344), "16:9": (1344, 768)}

# Matches the legacy config defaults (images.stablehorde_sampler/_cfg/_steps).
_DEFAULT_SAMPLER = "k_euler_a"
_DEFAULT_CFG_SCALE = 7
_DEFAULT_STEPS = 30


class StableHordeImageProvider:
    name = "stablehorde"

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_wait_seconds: float = _DEFAULT_MAX_WAIT_SECONDS,
    ) -> None:
        self._transport = transport if transport is not None else Transport()
        self._sleep = sleep
        self._max_wait_seconds = max_wait_seconds

    def supports(self, aspect_ratio: str) -> bool:
        return aspect_ratio in _SIZE_BY_RATIO

    def generate(
        self,
        prompt: str,
        *,
        aspect_ratio: str,
        model: str,
        cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bytes:
        size = _SIZE_BY_RATIO.get(aspect_ratio)
        if size is None:
            raise AvgError(ErrorKind.bug, f"Stable Horde: unsupported aspect_ratio {aspect_ratio!r}")
        width, height = size

        key = get_key(self.name) or _ANONYMOUS_KEY
        headers = {
            "apikey": key,
            "Content-Type": "application/json",
            "Client-Agent": "AVG-Studio:1.0:local",
        }

        job_id = self._submit(prompt, model, width, height, headers)
        return self._poll_until_done(job_id, headers, cancel, on_progress)

    # -- submit --------------------------------------------------------

    def _submit(self, prompt: str, model: str, width: int, height: int, headers: dict[str, str]) -> str:
        body = {
            "prompt": prompt,
            "params": {
                "sampler_name": _DEFAULT_SAMPLER,
                "cfg_scale": _DEFAULT_CFG_SCALE,
                "steps": _DEFAULT_STEPS,
                "width": width,
                "height": height,
                "n": 1,
            },
            "models": [model],
            "nsfw": False,
            "censor_nsfw": True,
            "r2": True,  # CDN URL response instead of inline base64 - avoids a huge body.
        }
        response = self._transport.request(
            "POST",
            f"{_STABLEHORDE_BASE_URL}/generate/async",
            headers=headers,
            json=body,
            timeout=_SUBMIT_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)
        job_id = response.json().get("id")
        if not job_id:
            raise AvgError(ErrorKind.bug, "Stable Horde: no job ID returned")
        return str(job_id)

    # -- poll ------------------------------------------------------------

    def _poll_until_done(
        self,
        job_id: str,
        headers: dict[str, str],
        cancel: Callable[[], bool] | None,
        on_progress: Callable[[int, int], None] | None,
    ) -> bytes:
        elapsed = 0.0
        wait = _POLL_START_SECONDS

        while elapsed < self._max_wait_seconds:
            if cancel is not None and cancel():
                self._cancel_job(job_id, headers)
                raise AvgError(ErrorKind.bug, "Stable Horde generation cancelled")

            self._sleep(wait)
            elapsed += wait
            wait = min(_POLL_MAX_SECONDS, wait + _POLL_STEP_SECONDS)

            check_response = self._transport.request(
                "GET",
                f"{_STABLEHORDE_BASE_URL}/generate/check/{job_id}",
                headers=headers,
                timeout=_POLL_TIMEOUT_SECONDS,
                provider=self.name,
            )
            raise_for_status(check_response, self.name)
            check = check_response.json()

            if on_progress is not None:
                on_progress(int(check.get("wait_time") or 0), int(check.get("queue_position") or 0))

            if check.get("faulted"):
                self._cancel_job(job_id, headers)
                kind = classify(200, "generation faulted: no workers available or content blocked", None, self.name)
                raise AvgError(kind, "Stable Horde: generation faulted (no workers available or content blocked)")

            if check.get("done"):
                return self._fetch_result(job_id, headers)

        self._cancel_job(job_id, headers)
        detail = f"timed out waiting for generation after {self._max_wait_seconds}s"
        kind = classify(None, detail, None, self.name)
        raise AvgError(kind, f"Stable Horde: {detail}")

    def _fetch_result(self, job_id: str, headers: dict[str, str]) -> bytes:
        response = self._transport.request(
            "GET",
            f"{_STABLEHORDE_BASE_URL}/generate/status/{job_id}",
            headers=headers,
            timeout=_POLL_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)
        generations: list[dict[str, Any]] = response.json().get("generations") or []
        if not generations:
            raise AvgError(ErrorKind.bug, "Stable Horde: done but no generation in response")
        gen = generations[0]

        if gen.get("censored"):
            kind = classify(200, "image was censored", None, self.name)
            raise AvgError(kind, "Stable Horde: image was censored")

        img = gen.get("img")
        if not img:
            raise AvgError(ErrorKind.bug, "Stable Horde: generation returned no image data")

        if img.startswith("http://") or img.startswith("https://"):
            img_response = self._transport.request(
                "GET", img, timeout=_DOWNLOAD_TIMEOUT_SECONDS, provider=self.name
            )
            raise_for_status(img_response, self.name)
            return img_response.content

        return base64.b64decode(img)

    def _cancel_job(self, job_id: str, headers: dict[str, str]) -> None:
        try:
            self._transport.request(
                "DELETE",
                f"{_STABLEHORDE_BASE_URL}/generate/status/{job_id}",
                headers=headers,
                timeout=_CANCEL_TIMEOUT_SECONDS,
                provider=self.name,
            )
        except Exception:
            pass
