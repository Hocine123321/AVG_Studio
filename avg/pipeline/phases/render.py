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
(`avg.media.workspace`). Here the project is being made for the first time -
there is no earlier output to protect, and `master_output.mp4` belongs in the
project folder, which is exactly where the engine writes it.
"""

from __future__ import annotations

from avg.core.errors import AvgError, ErrorKind
from avg.core.events import PhaseFailed, ProgressEvent, RenderCancelled, RenderFinished
from avg.core.models import RenderRequest
from avg.media.engine import MediaEngine
from avg.pipeline.phase import PhaseContext


class RenderPhase:
    """Composite the finished assets into `master_output.mp4`."""

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
        )

        engine = MediaEngine()
        # Cancelling mid-render has to reach the child process, not just stop
        # the loop below - otherwise ffmpeg keeps burning CPU after the user
        # asked it to stop. The token is checked per event because a render is
        # the one phase long enough that waiting for a phase boundary would
        # feel broken.
        for event in engine.render(request, ctx.project.id):
            if ctx.cancel_token.cancelled:
                engine.cancel()

            if isinstance(event, ProgressEvent):
                ctx.emit(event)
            elif isinstance(event, RenderFinished):
                ctx.project.master_output = event.output
                ctx.emit(event)
            elif isinstance(event, RenderCancelled):
                ctx.emit(event)
                raise AvgError(ErrorKind.bug, "render cancelled")
            elif isinstance(event, PhaseFailed):
                ctx.emit(event)
                raise AvgError(event.kind, event.message, event.detail)
