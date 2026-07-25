from pathlib import Path
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
