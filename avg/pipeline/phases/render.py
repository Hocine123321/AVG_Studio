"""Phase 7 - render the finished video.

The last phase, and the only one that generates no new creative content: by
the time it runs, every input already exists on disk. It composites the
images, narration, direction plan, cards and captions into
`master_output.mp4`.

**This phase owns no rendering logic.** `avg.media.engine.MediaEngine` was
built in Slice 1 and already drives the vendored render engine in a child
process - which is what keeps a moviepy out-of-memory kill from taking the
whole app down with it on an 8 GB machine. This phase is the adapter that
lets the pipeline reach that existing capability, nothing more.

It renders **in place**, unlike the `/api/projects/{id}/render` route. That
route re-renders a project the user already has, so it works on a disposable
copy to avoid destroying the finished video they are comparing against
(`avg.media.environment`). Here the project is being made for the first time -
there is no earlier output to protect, and `master_output.mp4` belongs in the
project folder, which is exactly where the engine writes it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from avg.core.errors import AvgError, ErrorKind, report_degradation
from avg.core.events import (
    CompositeFinished,
    PhaseFailed,
    ProgressEvent,
    RenderCancelled,
    RenderFinished,
)
from avg.core.models import CompositeRequest, RenderRequest
from avg.media.engine import MediaEngine
from avg.pipeline.phase import PhaseContext
from avg.pipeline.phases._common import read_json, render_dir, resolve_font_family, write_json


def _render_kwargs(settings: dict) -> dict[str, object]:
    """The channel's render settings, as `RenderRequest` keyword arguments.

    Until this existed, `fps`, `ken_burns`, `render_codec` and
    `render_engine` were dead switches in the catalogue: the settings screen
    rendered them, saved them, and no render ever read them - the values
    actually used came from the LEGACY `config.json`, which the user has no
    way to edit from this app.

    Only keys the channel actually carries are returned, so an unset setting
    still falls through to `RenderRequest`'s own default rather than being
    overwritten with a guess.
    """
    kwargs: dict[str, object] = {}
    fps = settings.get("fps")
    if isinstance(fps, int) and not isinstance(fps, bool) and fps > 0:
        kwargs["fps"] = fps
    if isinstance(settings.get("ken_burns"), bool):
        kwargs["ken_burns"] = settings["ken_burns"]
    codec = settings.get("render_codec")
    if isinstance(codec, str) and codec:
        kwargs["codec"] = codec
    engine = settings.get("render_engine")
    if isinstance(engine, str) and engine:
        kwargs["render_engine"] = engine
    kwargs.update(_thread_kwargs(settings.get("render_threads")))
    return kwargs


def _thread_kwargs(raw: object) -> dict[str, int]:
    """Translate the `render_threads` setting into a `RenderRequest` kwarg.

    `0` (the default) means "auto", which is `RenderRequest`'s own
    `os.cpu_count()` default - so auto is expressed by passing NOTHING and
    letting that default stand, never by passing a number this module
    guessed. Anything unset, unparseable or below 1 is treated as auto for
    the same reason: a bad value must not silently pin the encoder to one
    thread.
    """
    try:
        threads = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}
    return {"threads": threads} if threads >= 1 else {}


#: Anything smaller than this was never a real video, however cleanly the
#: encoder returned - the same guard `avg.media.child` applies to its own
#: output.
_MIN_OUTPUT_BYTES = 1024

#: Part 1 (the base encode) is the long pole, so it owns most of the bar.
_BASE_PERCENT_SPAN = 70


def render_substate(project_folder: Path) -> Literal["none", "base", "done"]:
    """How far the two-part render got, read off disk.

    Derived rather than stored, exactly as `script_substate` is: a stored flag
    and a project folder can disagree, and when they do the user is shown a
    state their files do not support.

    `render/_composite.json` is this app's own file. The vendored caption pass
    replaces `master_output.mp4` in place, so nothing about the video itself
    says whether overlays and captions were baked into it - and "why has my
    video no captions" deserves an answer that does not require reading a log.
    """
    folder = Path(project_folder)
    output = folder / "master_output.mp4"
    if not output.is_file() or output.stat().st_size < _MIN_OUTPUT_BYTES:
        return "none"
    return "done" if (folder / "render" / "_composite.json").is_file() else "base"


def _composite_request(ctx: PhaseContext) -> CompositeRequest:
    """Part 2's inputs, from the channel's settings and Screen Text's record.

    The provider name comes off disk rather than being recomputed here:
    whether AssemblyAI actually answered is knowledge Screen Text has and
    this phase does not, and it is part of the vendored transcription
    cache's key - guessing it means a silent cache miss that re-transcribes
    and re-pays.
    """
    settings = getattr(ctx.channel, "settings", {}) or {}
    source = read_json(render_dir(ctx) / "_transcript_source.json")
    provider = str((source or {}).get("provider") or "estimated") if isinstance(source, dict) else "estimated"
    # `resolve_font_family` reaches into the channel and this phase's own
    # tests build a context without one (see `_render_kwargs`' identical
    # guard). An empty font leaves the drawing library's default standing,
    # which is the same thing an unset setting would do.
    font = resolve_font_family(ctx) if ctx.channel is not None else ""
    return CompositeRequest(
        project_folder=ctx.project.folder,
        provider=provider,
        font=font,
        font_size=int(settings.get("captions_font_size") or 0) or 56,
        position=str(settings.get("captions_position") or "bottom"),
        animation=str(settings.get("captions_animation") or "pop"),
        captions_enabled=settings.get("captions_enabled") is not False,
        render_engine=str(settings.get("render_engine") or "auto"),
    )


class RenderPhase:
    """Composite the finished assets into `master_output.mp4`.

    Two parts, as the old app had (`D:\\AI_Video_Gen\\info.txt:326-334`):
    part 1 encodes the base video from the images, motion and grade; part 2
    composites the overlays, takeover cards and captions onto it. Part 2 ran
    nowhere in this app until Slice 9, which is why finished videos carried
    neither cards nor captions.

    Each part is its own child process (`avg.media.engine`), so an
    out-of-memory kill in the base encode cannot poison the compositing pass,
    and a part-2 retry reuses the finished base instead of re-encoding it.
    """

    id: int = 7
    key: str = "render"
    name: str = "Render"

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        """The engine needs images and narration; everything else degrades."""
        folder = ctx.project.folder
        has_images = (folder / "images").is_dir() and any((folder / "images").glob("*.png"))
        has_audio = (folder / "audio").is_dir() and any((folder / "audio").glob("*.mp3"))
        return has_images and has_audio

    def run(self, ctx: PhaseContext) -> None:
        request = RenderRequest(
            project_folder=ctx.project.folder,
            aspect_ratio=ctx.project.aspect_ratio,
            short_edge=ctx.project.short_edge,
            # `ctx.channel` is typed as a Channel but is None in the render
            # engine's own unit tests, which construct a context without one.
            **_render_kwargs(getattr(ctx.channel, "settings", {}) or {}),
        )

        engine = MediaEngine()
        # Cancelling mid-render has to reach the child process, not just stop
        # the loop below - otherwise ffmpeg keeps burning CPU after the user
        # asked it to stop. The token is checked per event because a render is
        # the one phase long enough that waiting for a phase boundary would
        # feel broken.
        # ── Part 1: the base video ────────────────────────────────────────
        # The vendored engine's stderr goes to the take's log. It is where
        # every real render diagnosis lives, and a windowed build has no
        # console for it to fall out of.
        def _stderr(text: str) -> None:
            if ctx.log is not None:
                ctx.log.error("render stderr", detail=text)

        for event in engine.render(request, ctx.project.id, on_stderr=_stderr):
            if ctx.cancel_token.cancelled:
                engine.cancel()

            if isinstance(event, ProgressEvent):
                # Scaled into 0-70: part 2 owns the rest, and two passes each
                # reporting 0-100 read as the render having started over.
                ctx.emit(event.model_copy(update={"percent": int(event.percent * _BASE_PERCENT_SPAN / 100)}))
            elif isinstance(event, RenderFinished):
                ctx.project.master_output = event.output
                ctx.emit(event)
            elif isinstance(event, RenderCancelled):
                ctx.emit(event)
                raise AvgError(ErrorKind.bug, "render cancelled")
            elif isinstance(event, PhaseFailed):
                ctx.emit(event)
                raise AvgError(event.kind, event.message, event.detail)

        # ── Part 2: overlays, cards and captions ──────────────────────────
        # Whatever this manages is recorded in render/_composite.json, which
        # is also what `render_substate` reads: the vendored caption pass
        # replaces master_output.mp4 in place, so the video itself cannot say
        # whether anything was baked into it.
        for event in engine.composite(_composite_request(ctx), ctx.project.id, on_stderr=_stderr):
            if ctx.cancel_token.cancelled:
                engine.cancel()

            if isinstance(event, ProgressEvent):
                ctx.emit(
                    event.model_copy(
                        update={
                            "percent": _BASE_PERCENT_SPAN
                            + int(event.percent * (100 - _BASE_PERCENT_SPAN) / 100)
                        }
                    )
                )
            elif isinstance(event, CompositeFinished):
                if not event.captions.startswith("burned"):
                    # A finished video with no captions on it. The reason is
                    # already in _composite.json; this puts it on the screen
                    # the user checks when a video looks wrong.
                    report_degradation(ctx, f"Render: captions were {event.captions}.")
                rdir = render_dir(ctx)
                rdir.mkdir(parents=True, exist_ok=True)
                write_json(
                    rdir / "_composite.json",
                    {
                        "overlays": event.overlays,
                        "captions": event.captions,
                        "at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                ctx.emit(event)
            elif isinstance(event, PhaseFailed):
                ctx.emit(event)
                raise AvgError(event.kind, event.message, event.detail)
