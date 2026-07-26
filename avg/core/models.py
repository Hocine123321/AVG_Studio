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

class Take(BaseModel):
    """One attempt at a project: Slice 5's unit of "generate, tweak,
    generate again, compare." Lives here (rather than in
    `avg.core.takes`, which owns the behavior around this shape) purely to
    avoid a circular import - `ProjectDoc.takes` below is a `list[Take]`,
    and `avg.core.takes` itself needs to import `ProjectDoc`, so the model
    that both sides share has to sit below both of them. `avg.core.takes`
    re-exports this name, so `from avg.core.takes import Take` still works
    for every caller that thinks of it as living there.

    `changed_from` is the take id this one was cloned from (`None` for a
    take with no predecessor - take 1, or a migrated legacy project).
    `changed_ids` is the settings-catalogue ids that changed relative to
    that predecessor - what `avg.core.invalidation.phases_to_rerun` was
    given to decide which phases this take actually redoes.
    `rerolled_from` names a single phase key when this take exists only to
    reroll one phase's output (not a settings change) - `changed_from`/
    `changed_ids` and `rerolled_from` are not mutually exclusive at the
    model level, but a caller only sets the one that applies.

    `phases` mirrors `ProjectDoc.phases`'s shape but scoped to this take
    alone: only phases whose output this take actually holds appear here
    (a freshly cloned take carries over only its kept phases' records - see
    `avg.core.takes.clone_take` - and phases still to (re)run are simply
    absent, exactly like `ProjectDoc.phases` treats a phase that hasn't run
    yet). `master_output` starts `None` on every new take - see
    `avg.core.takes.clone_take`'s docstring for why that must never be
    inherited from the take it was cloned from.
    """

    schema_version: int = 1
    id: int
    label: str
    created: datetime
    settings_snapshot: dict = Field(default_factory=dict)
    changed_from: int | None = None
    changed_ids: list[str] = Field(default_factory=list)
    rerolled_from: str | None = None
    phases: dict[str, PhaseRecord] = Field(default_factory=dict)
    master_output: Path | None = None

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
    # Slice 5: a project is really "one or more takes" now. `takes` starts
    # empty for anything that predates this slice - `avg.core.takes.
    # migrate_to_takes` is what turns that legacy shape into exactly one
    # current take rather than this field defaulting to some fabricated
    # take 1 on every read. `current_take_id` is canonical (Home's
    # thumbnail, `master_output`, the export default) and only ever
    # advances to a take once IT finishes successfully, or moves because a
    # take was deleted - never because the UI merely viewed a different
    # take. See `avg.core.takes` module docstring for the full rule.
    takes: list[Take] = Field(default_factory=list)
    current_take_id: int | None = None

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
