from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from avg.core.errors import ErrorKind

class ProgressEvent(BaseModel):
    type: str = "progress"
    project_id: str
    percent: int
    message: str

class PhaseFailed(BaseModel):
    type: str = "phase_failed"
    project_id: str
    phase: str
    kind: ErrorKind
    message: str
    detail: str = ""

class RenderCancelled(BaseModel):
    """The user stopped the render. Distinct from PhaseFailed on purpose.

    Cancelling terminates the child, which on Windows produces the same
    negative exit code as an out-of-memory kill. Without this event the UI
    would show a red "failed" row saying the render died, when in fact the
    user asked it to stop.
    """

    type: str = "render_cancelled"
    project_id: str


class RenderFinished(BaseModel):
    type: str = "render_finished"
    project_id: str
    output: Path
    size_bytes: int


class PhaseStarted(BaseModel):
    """A pipeline phase (`avg.pipeline.runner`) began running."""

    type: str = "phase_started"
    project_id: str
    phase: str


class PhaseFinished(BaseModel):
    """A pipeline phase completed successfully."""

    type: str = "phase_finished"
    project_id: str
    phase: str
    duration_ms: int


class JobStarted(BaseModel):
    """A background job's thread started, before it has done any work.

    Without this, the first thing a client hears about a render is the
    encoder's first progress line - which on a real project is minutes in,
    because the workspace has to be prepared first. The status pill's "a
    job is running" state begins here.

    `kind` distinguishes a full pipeline run from a direct `/render`,
    which are the two things that can occupy the app's one job slot.
    """

    type: str = "job_started"
    project_id: str
    kind: Literal["render", "pipeline"]


class JobStep(BaseModel):
    """A coarse named step for work that cannot report a percentage.

    Preparing the render workspace is the only one today. Deliberately not
    a `ProgressEvent`: a fabricated percentage on work with no measurable
    denominator is worse than an honest name, and every consumer of
    progress treats the number as real.
    """

    type: str = "job_step"
    project_id: str
    step: str
    detail: str = ""


class PipelineCancelled(BaseModel):
    """The pipeline run stopped because its `CancellationToken` was set.

    Distinct from `PhaseFailed` for the same reason `RenderCancelled` is
    distinct from it: a user-requested stop is not a bug in the phase that
    didn't get to run, and the UI must not paint it as a red failure.
    `phase` is the phase that was about to start when cancellation was
    noticed - it never ran, so no `PhaseRecord` for it is touched.
    """

    type: str = "pipeline_cancelled"
    project_id: str
    phase: str


class PipelineFinished(BaseModel):
    """`avg.pipeline.runner.run` reached the end of its phase list with every
    attempted phase having succeeded.

    Nothing else says this. `RenderFinished` (from the render phase itself)
    fires before the render phase's own composite pass, whose progress events
    then follow it - so a UI that treats `RenderFinished` as "the run is over"
    watches the bar come back to life immediately after. `PhaseFinished` fires
    after every phase, not just the last, so it cannot mean "done" either.
    This is the one event a full pipeline run emits exactly once, only at the
    very end of a successful run - a failed or cancelled run ends on
    `PhaseFailed`/`PipelineCancelled` instead and never reaches this.
    """

    type: str = "pipeline_finished"
    project_id: str


class CompositeFinished(BaseModel):
    """Part 2 of a render finished, carrying what it actually managed to do.

    `captions` is a sentence rather than a boolean because "skipped" has a
    reason worth keeping: a render that produced a perfectly good video with
    no captions is a real outcome - the vendored pass answers 2 for it, not 1
    (`avg/media/vendor/avg.py:3329`) - and the user is owed the reason rather
    than left to guess. `avg.pipeline.phases.render` writes the same string
    into `render/_composite.json`, so the answer survives the session.
    """

    type: str = "composite_finished"
    project_id: str
    overlays: int
    captions: str
