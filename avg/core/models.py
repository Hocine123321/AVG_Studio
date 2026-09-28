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
    #: Where this take's artifacts actually live.
    #:
    #: `"own"` - `<project.folder>/takes/<id>/`, which is what a cloned
    #: take and a migrated legacy take both get.
    #:
    #: `"root"` - the project folder itself. Take 1 of anything created
    #: after Slice 8 is this: every phase writes in place
    #: (`avg.pipeline.phases._common.script_dir`), so the project root IS
    #: that take's storage and copying it into `takes/1/` would store the
    #: same bytes twice.
    #:
    #: Declared rather than inferred from whether the folder exists. That
    #: inference looked equivalent and was not: a take whose folder is
    #: merely absent - one built in a test, one whose folder was removed -
    #: would read as root storage and adopt the project's phase records.
    storage: Literal["root", "own"] = "own"
    #: Copied by hand from `shadow_project.visual_seed` after a take's run
    #: finishes (`avg/api/routes_takes.py`) - the same treatment
    #: `master_output` above already needs and for the same reason: phases
    #: mutate the shadow project passed to the runner, not this object.
    visual_seed: int | None = None

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
    # Slice 6C: hidden from Home's default view, nothing more. Archiving
    # never touches a file, so it is reversible and cheap - `False` for
    # every project that predates it, and for every legacy import, without
    # a migration. Deliberately NOT a deletion or a move: the folder is the
    # user's work and the app does not relocate it behind their back.
    archived: bool = False
    # Slice 32 sub-slice 2: assigned once, on Visuals' first run for a
    # take, when seed-lock is on - reused for every scene and every
    # re-run of that take, locking the video's look rather than each
    # image's. `None` (every project and take before this field existed,
    # and every one after it until Visuals actually runs) means "not yet
    # assigned". See `avg.pipeline.phases.visuals` for assignment,
    # `avg.core.models.Take.visual_seed` for why a take needs its own copy.
    visual_seed: int | None = None

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


class CompositeRequest(BaseModel):
    """Part 2 of a render: what to composite onto a finished base video.

    Separate from `RenderRequest` because the two share nothing but the
    folder. This pass encodes no frames of its own, so it takes no fps,
    codec, thread count or Ken Burns setting; it cares instead about caption
    styling and about which transcription provider seeded the vendored cache.

    `provider` must match what the Screen Text phase used - it is part of
    that cache's key (`avg/media/vendor/avg.py:3557`), and a mismatch is a
    silent cache miss that re-transcribes and re-pays. The phase records it
    in `render/_transcript_source.json` for exactly this reason.
    """

    project_folder: Path
    provider: str = "estimated"
    font: str = ""
    font_size: int = 56
    font_color: str = "&HFFFFFF"
    position: str = "bottom"
    max_words: int = 7
    animation: str = "pop"
    animation_speed: str = "normal"
    render_engine: str = "auto"
    captions_enabled: bool = True
