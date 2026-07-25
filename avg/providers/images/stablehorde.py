"""Stable Horde image backend - the one provider in this package that is
asynchronous: submit a job, then poll a shared community queue until it
completes.

Mirrors the legacy `Invoke-StableHordeImageGeneration` (`D:\\AI_Video_Gen\\
modules\\visuals.ps1`, line 977): `POST /api/v2/generate/async` to submit,
then `GET /api/v2/generate/check/<id>` on a growing interval (starts at 4s,
+1s per poll, capped at 8s - ported verbatim) until the job is `done` or
`faulted`, then `GET /api/v2/generate/status/<id>` for the final result.
`r2=true` asks the API to return a CDN URL instead of inline base64
(avoids a huge response body); this backend downloads that URL when
present, falling back to inline base64 otherwise - both paths ported since
the legacy function handles both.

Free and keyless-capable (`'0000000000'` is the community's anonymous key,
ported here as this provider's default when no key is configured), but
with failure modes none of the synchronous providers in this package have:

  - **faulted** - no worker picked the job up, or (the API's own genuinely
    ambiguous flag) a content decision. `avg/providers/errors.py`'s module
    comment documents in detail why this deliberately does NOT get guessed
    at a more specific kind: it is run through `classify()` from generic
    text and falls through to `ErrorKind.bug`, the safe default - guessing
    wrong in either direction (retrying a real content block, or failing a
    transient capacity gap) is worse.
  - **censored** - an explicit, unambiguous flag on a completed generation,
    checked separately from `faulted` (matching the legacy code's own
    separate `if ($gen.censored)` branch) - classifies as
    `ErrorKind.content_filter`.
  - **timed out** - this provider's own poll budget (`max_wait_seconds`,
    default matching the legacy `images.image_timeout_seconds` config
    default of 180) is exceeded before the job finishes - classifies as
    `ErrorKind.timeout`. The stalled job is cancelled (a best-effort
    DELETE, swallowing any failure - matching the legacy function's own
    `-EA SilentlyContinue`) before raising.

The poll loop cannot spin forever: it is bounded by `max_wait_seconds`
regardless of what the API itself reports, and accepts an optional
`cancel` callback - checked once per iteration - so a caller can abort a
long wait early, cancelling the remote job the same way a timeout does
rather than abandoning it silently server-side. `sleep` is injectable
(mirroring `avg.providers.transport.Transport`'s own constructor) so tests
can drive the poll loop with an instant fake instead of real wall-clock
delays.

The API also reports an ETA (`wait_time`) and a queue position on every
`check` response - real information a caller may want to surface to a
user during a wait that can run into minutes. Rather than this module
deciding how (or whether) to display that, `on_progress` - an optional
callback invoked once per poll with `(wait_time_seconds, queue_position)`
- lets the caller decide. This module itself never logs or prints
anything.

Width/height (Stable Horde's SDXL-based models want multiples of 64,
stricter than Pollinations' multiples of 8) are derived from
`aspect_ratio` the same way `pollinations.py` derives its own - but with a
different table, since the two APIs prefer different absolute numbers.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Callable

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify
from avg.providers.images.base import raise_for_status
from avg.providers.transport import Transport

_BASE_URL = "https://aihorde.net/api/v2"

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
# Pollinations' multiples of 8 (see module docstring).
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
            f"{_BASE_URL}/generate/async",
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
                f"{_BASE_URL}/generate/check/{job_id}",
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
            f"{_BASE_URL}/generate/status/{job_id}",
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
                f"{_BASE_URL}/generate/status/{job_id}",
                headers=headers,
                timeout=_CANCEL_TIMEOUT_SECONDS,
                provider=self.name,
            )
        except Exception:
            pass
