"""Preview routes: what a setting will look and sound like in the video.

Three of these are free and may be called on any settings change. Two spend
real money and are refused without an explicit confirmation:

    POST /api/preview/caption   free    one ffmpeg/libass frame
    POST /api/preview/overlay   free    one PIL draw
    POST /api/preview/takeover  free    one PIL draw
    POST /api/preview/voice     PAID on elevenlabs, free on edge
    POST /api/preview/image     PAID always

The confirmation is enforced HERE rather than only in the UI, so it holds
regardless of what the frontend does. `CLAUDE.md` and Slice 8 record why
images and voices are listed rather than probed: probing an image model
means paying for an image and probing an ElevenLabs voice costs characters.
A preview is user-initiated, which is why it may exist at all - but the same
money is spent, so it may never happen as a side effect of typing.

One preview at a time, in its own slot with a 409 on a second start -
matching `probe_thread`'s "one job, one slot, 409 on a second start" shape,
and deliberately NOT the render slot, so a preview can never block or be
blocked by a real render. That slot is claimed and released around an
`await asyncio.to_thread(...)` call, not around the underlying worker
thread itself: if the request is cancelled (the user navigates away
mid-preview) the `finally` below still runs and releases the lock, but the
`to_thread` worker keeps running to completion in the background - "one at
a time" bounds how many previews can be STARTED at once, not how many
worker threads can briefly be alive. No extra spend results (a cancelled
request already paid for its own press, same as a completed one), but the
guarantee is narrower than "one in flight" and this comment says so rather
than the previous, over-precise wording.

Both money-spending functions - `image_sample` below and `voice_sample`
below - live in THIS module, not in `avg.media.preview`. `image_sample`
needs `avg.pipeline.phases.visuals._build_provider` to build an image
backend; `voice_sample` needs `avg.providers.voice.providers`. `avg/media/`
may not import from `avg/pipeline/` or `avg/providers/` (see that module's
own docstring, and CLAUDE.md's layer rule) - this route module is allowed
to import from every layer, so the join happens here, the same shape
`preview_caption` below already uses for the caption `.ass` text. Keeping
both spenders in one module, rather than splitting them the way Slice 14's
first pass did, is what makes "where can this app spend money" a
one-file question (Slice 14 review, Important 5).

**Every failure from either spender is turned into a specific, honest
detail** rather than one of two hardcoded sentences (Slice 14 review,
Important 2). Both spenders let a provider's `AvgError` propagate rather
than swallowing it into `b""` - every provider under `avg/providers/`
already raises `AvgError` with a real `kind` on failure (see
`avg.providers.errors.classify`, the ONE place a status code or body
string is examined) - and `_preview_failure` below turns that `kind` into
an HTTP status and a message built from `avg.providers.errors.POLICY`
(`on_exhausted`), never from `exc.detail`: `detail` is a provider's raw
response body, which must never reach a client response (CLAUDE.md's key
rule extends past the key itself to anything a provider might echo back).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from avg.core.errors import AvgError, ErrorKind
from avg.media.preview import (
    PAID_VOICE_PROVIDERS,
    SAMPLE_IMAGE_PROMPT,
    SAMPLE_SENTENCE,
    caption_png,
    overlay_png,
    takeover_png,
)
from avg.providers.errors import POLICY

from .deps import require_token

router = APIRouter(dependencies=[Depends(require_token)])


class CaptionPreview(BaseModel):
    width: int = 1080
    height: int = 1920
    #: RAW setting values, matching the schema's own defaults
    #: (`avg.core.settings_schema`: `font_family=""`, `captions_font_size=0`)
    #: - not a guessed-at "Figtree"/`64`. `sample_ass` is the one place that
    #: resolves a blank/zero value into what the render would actually use;
    #: see its docstring and Important 1 of the Slice 14 review.
    font_name: str = ""
    font_size: int = 0
    position: str = "bottom"


class OverlayPreview(BaseModel):
    spec: dict[str, Any]
    width: int = 1080
    height: int = 1920


class TakeoverPreview(BaseModel):
    headline: str = ""
    sub: str = ""
    width: int = 1080
    height: int = 1920


class VoicePreview(BaseModel):
    provider: str
    voice_id: str
    #: Required for a provider in `PAID_VOICE_PROVIDERS`. The UI sets it only
    #: from an explicit press, never from a settings change.
    confirm_paid: bool = False


class ImagePreview(BaseModel):
    provider: str
    model: str
    aspect_ratio: str = "9:16"
    confirm_paid: bool = False


def image_sample(provider: str, model: str, aspect_ratio: str) -> bytes:
    """One image from `SAMPLE_IMAGE_PROMPT`. Image bytes.

    **Costs money on every provider that works.** `preview_image` below
    refuses to call this without `confirm_paid` - that is the only guard;
    this function itself does the spend unconditionally, same as
    `voice_sample` below does for ElevenLabs.

    Raises `AvgError(ErrorKind.model_missing, ...)` for a `provider` not in
    the image catalogue, rather than reaching for `_build_provider`'s own
    fallback to the catalogue default. That fallback exists for a good
    reason elsewhere - a channel document from a future/rolled-back
    settings catalogue must still produce something runnable during a real
    render - but a PRESSED preview is a live spend the user just asked for
    on a SPECIFIC provider; silently spending it on a different one and
    presenting the result as the one asked for is money on a provider the
    user did not choose (Slice 14 review, Minors).

    Otherwise raises whatever `AvgError` the provider itself raises -
    `preview_image` turns that into a specific status and detail.
    """
    from avg.pipeline.phases.visuals import _PROVIDERS, _build_provider  # noqa: PLC0415

    if provider not in _PROVIDERS:
        raise AvgError(ErrorKind.model_missing, f"unknown image provider: {provider}")

    backend = _build_provider(provider, None)
    data = backend.generate(SAMPLE_IMAGE_PROMPT, aspect_ratio=aspect_ratio, model=model)
    return bytes(data)


def _voice_backend(provider: str) -> Any:
    """The voice backend for `provider`, or `None` for an unrecognised name.

    Imported lazily: `avg.providers.voice.providers` pulls in `edge_tts`,
    which costs about two seconds of startup, and Slice 7 moved that import
    off the launch path deliberately - a rule this function preserves even
    after moving here from `avg.media.preview` (Slice 14 review,
    Important 5).
    """
    from avg.providers.voice.providers import (  # noqa: PLC0415
        EdgeVoiceProvider,
        ElevenLabsVoiceProvider,
    )

    backends = {"edge": EdgeVoiceProvider, "elevenlabs": ElevenLabsVoiceProvider}
    backend_cls = backends.get(provider)
    return backend_cls() if backend_cls is not None else None


def voice_sample(provider: str, voice_id: str) -> bytes:
    """`SAMPLE_SENTENCE`, spoken by `voice_id`. Audio bytes.

    **Costs money on ElevenLabs** - see `PAID_VOICE_PROVIDERS`. Free on
    Edge. Whether this is allowed to run at all is `preview_voice`'s
    decision, not this function's - `confirm_paid` is checked before this
    is ever called.

    Raises `AvgError(ErrorKind.model_missing, ...)` for an unrecognised
    `provider`, and otherwise lets whatever `AvgError` the backend itself
    raises propagate - `preview_voice` turns that into a specific status
    and detail rather than one generic sentence (Slice 14 review,
    Important 2). Moved here from `avg.media.preview` (Important 5): this
    is the only place under `avg/api/` that imports
    `avg.providers.voice.providers`, keeping every money-spending call in
    one module.
    """
    backend = _voice_backend(provider)
    if backend is None:
        raise AvgError(ErrorKind.model_missing, f"unknown voice provider: {provider}")
    data = backend.synthesize(SAMPLE_SENTENCE, voice_id=voice_id, model="")
    return bytes(data)


def _claim(app_state: Any) -> None:
    """Take the single preview slot, or 409."""
    lock: threading.Lock = app_state.preview_lock
    if not lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a preview is already running",
        )


def _png_or_503(data: bytes) -> Response:
    if not data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="nothing could be rendered - check that FFmpeg is present on App health",
        )
    return Response(content=data, media_type="image/png")


def _sniff_image_media_type(data: bytes) -> str:
    """The real content type of `data`, from its magic bytes.

    An image PROVIDER (not `caption_png`/`overlay_png`/`takeover_png`,
    which are always PNG by construction) may return JPEG or WebP - Gemini's
    own `outputMimeType` can ask for `image/jpeg` - so `preview_image`
    declaring `image/png` unconditionally was a claim this route could not
    back. Browsers sniff the bytes themselves and render it either way, so
    nothing was visibly broken, but the header asserted something false.
    Falls back to `application/octet-stream` for anything unrecognised
    rather than guessing at a third format (Slice 14 review, Minors).
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


#: What each `POLICY[kind].on_exhausted` outcome means for a preview's user
#: - built from `POLICY` (avg.providers.errors) rather than re-deriving a
#: meaning from `kind` a second time, per that module's own rule that it is
#: the only place a failure gets classified. Never built from `exc.detail`:
#: that field is a provider's raw response body, and echoing it into an
#: HTTP response is exactly the kind of leak CLAUDE.md's key rule warns
#: about - nothing a provider sends back, key or otherwise, may reach a
#: client response.
_ON_EXHAUSTED_DETAIL: dict[str, str] = {
    "switch_key": "the key was rejected - check it on App health",
    "switch_provider": "this provider could not fulfil the request right now",
    "switch_model": "this model is not available on this provider",
    "fail": "the provider returned nothing usable",
}

#: An HTTP status per `ErrorKind` - a presentation-layer decision this
#: module makes once, not a second classification of the failure (that
#: already happened, inside the provider, via `classify()`). `auth` maps to
#: 401 rather than repeating whatever status the provider itself used,
#: because by the time this dict is consulted the meaning is already known
#: precisely and the original status is not this route's to restate.
_STATUS_BY_KIND: dict[ErrorKind, int] = {
    ErrorKind.auth: status.HTTP_401_UNAUTHORIZED,
    ErrorKind.quota: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorKind.billing: status.HTTP_402_PAYMENT_REQUIRED,
    ErrorKind.rate_limit: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorKind.model_missing: status.HTTP_404_NOT_FOUND,
    ErrorKind.content_filter: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorKind.network: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorKind.timeout: status.HTTP_504_GATEWAY_TIMEOUT,
    ErrorKind.dependency: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorKind.bug: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _preview_failure(kind_label: str, provider: str, exc: AvgError) -> HTTPException:
    """One `AvgError` from a preview spender, turned into a specific status
    and a detail sentence built from `POLICY` - never from `exc.detail`
    (see the two dicts above). `kind_label` is `"voice"`/`"image"`, so a
    401 from the image route and a 401 from the voice route still read as
    two different failures rather than the same generic sentence twice.
    """
    rule = POLICY.get(exc.kind)
    on_exhausted = rule.on_exhausted if rule is not None else "fail"
    reason = _ON_EXHAUSTED_DETAIL.get(on_exhausted, _ON_EXHAUSTED_DETAIL["fail"])
    status_code = _STATUS_BY_KIND.get(exc.kind, status.HTTP_503_SERVICE_UNAVAILABLE)
    return HTTPException(
        status_code=status_code,
        detail=f"{provider} {kind_label} preview failed ({exc.kind.value}): {reason}",
    )


@router.post("/api/preview/caption")
async def preview_caption(body: CaptionPreview, request: Request) -> Response:
    """Free: one frame, burned by the same libass the renderer uses."""
    from avg.pipeline.phases.captions import sample_ass  # noqa: PLC0415

    app_state = request.app.state
    _claim(app_state)
    try:
        ass = sample_ass(body.width, body.height, body.font_name, body.font_size, body.position)
        data = await asyncio.to_thread(caption_png, ass, body.width, body.height)
    finally:
        app_state.preview_lock.release()
    return _png_or_503(data)


@router.post("/api/preview/overlay")
async def preview_overlay(body: OverlayPreview, request: Request) -> Response:
    """Free: one PIL draw by the render's own overlay code."""
    app_state = request.app.state
    _claim(app_state)
    try:
        data = await asyncio.to_thread(overlay_png, body.spec, body.width, body.height)
    finally:
        app_state.preview_lock.release()
    return _png_or_503(data)


@router.post("/api/preview/takeover")
async def preview_takeover(body: TakeoverPreview, request: Request) -> Response:
    """Free: one PIL draw by the render's own overlay code."""
    app_state = request.app.state
    _claim(app_state)
    try:
        data = await asyncio.to_thread(
            takeover_png, body.headline, body.sub, body.width, body.height
        )
    finally:
        app_state.preview_lock.release()
    return _png_or_503(data)


@router.post("/api/preview/voice")
async def preview_voice(body: VoicePreview, request: Request) -> Response:
    """Free on Edge. **Spends ElevenLabs characters** and refuses without
    `confirm_paid`, so typing in a voice field can never cost money."""
    if body.provider in PAID_VOICE_PROVIDERS and not body.confirm_paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{body.provider} charges for every sample - confirm before generating one"
            ),
        )
    app_state = request.app.state
    _claim(app_state)
    try:
        data = await asyncio.to_thread(voice_sample, body.provider, body.voice_id)
    except AvgError as exc:
        raise _preview_failure("voice", body.provider, exc) from exc
    finally:
        app_state.preview_lock.release()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the voice provider returned nothing usable",
        )
    return Response(content=data, media_type="audio/mpeg")


@router.post("/api/preview/image")
async def preview_image(body: ImagePreview, request: Request) -> Response:
    """**Always spends money.** Refused without `confirm_paid`."""
    if not body.confirm_paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="generating a sample image costs money - confirm before generating one",
        )
    app_state = request.app.state
    _claim(app_state)
    try:
        data = await asyncio.to_thread(
            image_sample, body.provider, body.model, body.aspect_ratio
        )
    except AvgError as exc:
        raise _preview_failure("image", body.provider, exc) from exc
    finally:
        app_state.preview_lock.release()
    if not data:
        # A provider is expected to raise `AvgError` on any failure (see
        # `image_sample`'s own docstring) - this is the defensive case of a
        # provider returning empty bytes without raising at all. FFmpeg is
        # not involved in an image generation, so this must never repeat
        # `_png_or_503`'s ffmpeg-flavoured wording (Slice 14 review,
        # Important 2).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the image provider returned nothing usable",
        )
    return Response(content=data, media_type=_sniff_image_media_type(data))
