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
matching `probe_thread`, and deliberately NOT the render slot, so a preview
can never block or be blocked by a real render.

**`image_sample` lives here, not in `avg/media/preview.py`.** It needs
`avg.pipeline.phases.visuals._build_provider`, and the Global Constraint is
that `avg/media/` may not import from `avg/pipeline/`. This route module may
import from both layers, so the one function that needed the pipeline layer
moved here instead of staying beside `voice_sample`.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from avg.media.preview import (
    PAID_VOICE_PROVIDERS,
    SAMPLE_IMAGE_PROMPT,
    caption_png,
    overlay_png,
    takeover_png,
    voice_sample,
)

from .deps import require_token

router = APIRouter(dependencies=[Depends(require_token)])


class CaptionPreview(BaseModel):
    width: int = 1080
    height: int = 1920
    font_name: str = "Figtree"
    font_size: int = 64
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
    """One image from `SAMPLE_IMAGE_PROMPT`. Image bytes, or `b""`.

    **Costs money on every provider that works.** Whether this is allowed to
    run is `preview_image`'s decision below, not this function's.

    Lives in the route module rather than `avg/media/preview.py`: it needs
    `avg.pipeline.phases.visuals._build_provider`, which the media layer may
    not import.
    """
    from avg.pipeline.phases.visuals import _build_provider  # noqa: PLC0415

    try:
        backend = _build_provider(provider, None)
        return backend.generate(SAMPLE_IMAGE_PROMPT, aspect_ratio=aspect_ratio, model=model)
    except Exception:  # noqa: BLE001 - a preview panel states the failure
        return b""


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
    finally:
        app_state.preview_lock.release()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the voice provider returned nothing - check its key on App health",
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
    finally:
        app_state.preview_lock.release()
    return _png_or_503(data)
