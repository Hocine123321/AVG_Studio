from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

PhaseStatus = Literal["pending", "running", "done", "failed"]

class PhaseRecord(BaseModel):
    name: str
    status: PhaseStatus = "pending"
    started: datetime | None = None
    finished: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None

class ProjectDoc(BaseModel):
    schema_version: int = 1
    id: str
    title: str
    folder: Path
    aspect_ratio: str = "9:16"
    short_edge: int = 1080
    created: datetime
    phases: dict[str, PhaseRecord] = Field(default_factory=dict)
    master_output: Path | None = None

class RenderRequest(BaseModel):
    project_folder: Path
    fps: int = 24
    codec: str = "libx264"
    threads: int = Field(default_factory=lambda: os.cpu_count() or 4)
    aspect_ratio: str = "9:16"
    ken_burns: bool = False
    scene_count: int | None = None
    brief_path: str = ""
    timeline_path: str = ""
    scene_map_path: str = ""
    short_edge: int = 1080
    kb_intensity: int = 100
    kb_mode: str = "dynamic"
    render_engine: str = "auto"
    render_opts: dict = Field(default_factory=dict)
