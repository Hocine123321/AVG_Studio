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

class RenderFinished(BaseModel):
    type: str = "render_finished"
    project_id: str
    output: Path
    size_bytes: int
