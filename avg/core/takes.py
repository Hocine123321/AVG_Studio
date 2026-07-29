"""The take model - Slice 5's unit of "generate, tweak, generate again, compare."

A project used to be one video. It is now a project that owns zero or more
`Take`s, each a full attempt with its own folder
(`<project.folder>/takes/<id>/`) mirroring the top-level layout
(`script/`, `images/`, `audio/`, `render/`, `master_output.mp4`) that
`avg.pipeline.create.new_project` already gives a fresh project. Task 1's
`avg.core.invalidation.first_invalid_phase`/`phases_to_rerun` decide WHICH
phases a settings change invalidates; this module is what a re-run acts on -
`clone_take` builds a new take's folder holding only the phases that survive
the change, so the phases that follow can be (re)run into it without
touching the take it came from.

**Current vs selected.** `ProjectDoc.current_take_id` is canonical - Home's
thumbnail, `master_output`, the export default - and only this module's own
functions move it (`delete_take` when the current take is removed,
`migrate_to_takes` onto the one take it creates). Advancing it when a take
*finishes* is a pipeline-runner concern (Task 3, which can see phase
completion; this module cannot), not something exposed here - merely
*viewing* a take in the UI never touches this field, and nothing in this
module provides a way to set it to an arbitrary take on a whim.

**A new take starts with no `master_output`.** `clone_take` never copies
`master_output.mp4` from its source take, regardless of `keep_phase_keys` -
a failed render must not inherit the previous take's video and look
successful, the same invariant `avg.media.engine.MediaEngine`/
`avg.media.environment.render_workspace` already carry for the same reason.

**Register only after the copy succeeds.** If copying a kept phase's
artifacts fails part-way (disk full, a permissions error, anything), the
partially-written take folder is removed and the exception re-raised -
before the new `Take` is ever appended to `project.takes`. A caller that
persists `project.takes` to `project.json` after a successful `clone_take`
call therefore never has to worry about a half-created take reaching disk.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from avg.core.models import PhaseRecord, ProjectDoc, Take  # noqa: F401 - re-exported, see Take's docstring

# Which phase writes what, verified by reading every phase module in
# `avg/pipeline/phases/` rather than trusted from either of this task's two
# hypothesis tables (both of which turned out to have a wrong row - see
# this task's report for specifics). Paths are relative to a take's own
# folder (`take_folder(project, take_id)`), matching how a fresh project's
# own top-level folder is laid out (`avg.pipeline.create.new_project`'s
# `_PROJECT_DIRS`).
#
# `"render"` is deliberately absent: its one artifact, `master_output.mp4`,
# must never be copied into a new take by `clone_take` - see this module's
# docstring - so it simply has no entry to look up, rather than being an
# entry `clone_take` has to remember to skip.
#
# Two corrections against the brief's own hypothesis tables, confirmed by
# reading the phase code itself:
#   - `voice` does NOT write `script/narration.txt` - that file is written
#     by `script` (`avg.pipeline.phases.writing.ScriptPhase.run`, Pass 1's
#     raw text); `voice` only ever reads it
#     (`avg.pipeline.phases.voice._narration_path`). Attributing it to
#     `voice` would make a take that reran only `voice` claim ownership of
#     a file `script` actually produced.
#   - `director1` writes `script/video_map.json`, not `render/index.json` -
#     no phase in this codebase writes an `index.json` at all yet (the
#     `render/` docstring in `avg.pipeline.phases._common.render_dir`
#     mentions it only as a FUTURE Render-phase artifact).
# One row from the task brief's own manual investigation held up exactly as
# reported: `script/scenes.json` is written only by `script` - `director1`,
# `visuals`, `director2`, and `captions` all read it but never write it.
#
# One more thing neither hypothesis table mentions: `captions` REWRITES
# `render/card_plan.json` in place (`ScreenTextPhase.run` reads Director II's
# `card_plan.json`, resolves each card's `start`/`end`, and writes it back
# enriched). That file's ownership still belongs to `director2` here, not
# `captions` - `card_plan.json` exists and is meaningful even when captions
# has not run yet (the enrichment is additive), so a take that keeps
# `director2` but reruns `captions` still needs `card_plan.json` copied
# forward, and a take that keeps both needs it copied only once (harmless -
# `card_plan.json` is a single file, not a merge of two owners' output).
PHASE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "brief": ("script/brief.json",),
    "script": ("script/narration.txt", "script/scenes.json"),
    "director1": ("script/video_map.json",),
    # `render/index.json` is Visuals' output, not Render's: it is the list
    # the render engine reads, and a cloned take that kept Visuals but not
    # Render must carry it or the clone cannot be rendered at all.
    "visuals": ("images", "render/index.json", "render/scene_safezones.json"),
    "voice": ("audio/raw_voice.mp3", "audio/optimized_voice.mp3", "audio/scene_audio_map.json"),
    "director2": ("render/direction_plan.json", "render/card_plan.json"),
    # `card_plan.json` appears here as well as under director2 because
    # Captions REWRITES it in place, adding each card's start/end times
    # (`avg/pipeline/phases/captions.py`). Those timings are captions'
    # own output, so a take that keeps captions has to carry them - it
    # only worked before because kept phases are in practice a prefix,
    # and keeping captions therefore kept director2 too.
    "screen_text": ("render/captions.ass", "render/card_plan.json"),
}

# What a legacy (pre-takes) project's artifacts look like at the TOP of its
# own folder - `avg.pipeline.create.new_project`'s `_PROJECT_DIRS` - copied
# wholesale into take 1's folder by `migrate_to_takes` rather than picked
# apart phase-by-phase (a legacy project has no notion of "kept phases";
# every phase it ever finished is simply part of take 1).
_LEGACY_ARTIFACT_DIRS: tuple[str, ...] = ("script", "images", "audio", "render")


def take_root(project: ProjectDoc, take_id: int) -> Path:
    """Where `take_id`'s artifacts actually are.

    The project folder for a `storage="root"` take, its own folder
    otherwise. Every caller that wants to READ a take's files goes through
    this rather than `take_folder`, which only ever names the `takes/<id>/`
    path and is still what a take that owns a folder is created at.
    """
    take = next((t for t in project.takes if t.id == take_id), None)
    if take is not None and take.storage == "root":
        return Path(project.folder)
    return take_folder(project, take_id)


def alias_root_take_phases(project: ProjectDoc) -> None:
    """Make a `storage="root"` take's phase records the SAME OBJECT as
    `project.phases`.

    A root-storage take keeps its artifacts in the project folder itself,
    so its records and `project.phases` describe the same files. Two dicts
    holding the same facts is how the phase rail froze: the runner
    advanced one of them and Studio displayed the other.

    Aliasing rather than copying is deliberate, and it has to be done as
    an assignment: pydantic validates (and therefore COPIES) a dict passed
    to a model's constructor, so handing the same object to `ProjectDoc`
    and `Take` produces two dicts, not one. `validate_assignment` is off,
    so assigning afterwards keeps the reference.

    Keyed on the declared `storage`, never on whether a folder happens to
    exist: a take whose folder is merely absent would otherwise adopt the
    project's records and lose its own. Idempotent, and a no-op for a
    project whose current take owns its folder.
    """
    take = next((t for t in project.takes if t.id == project.current_take_id), None)
    if take is None or take.storage != "root":
        return
    take.phases = project.phases


def take_folder(project: ProjectDoc, take_id: int) -> Path:
    """`<project.folder>/takes/<take_id>/` - where that take's own copy of
    every artifact it owns lives, mirroring a fresh project's own
    top-level layout."""
    return Path(project.folder) / "takes" / str(take_id)


def _next_take_id(project: ProjectDoc) -> int:
    """One past the highest existing take id, or `1` for the first take.
    Ids are never reused (even after `delete_take`), so a take id also
    doubles as a "how recent is this" ordering - `estimate_seconds` and
    `delete_take` both rely on higher id meaning more recent."""
    return max((t.id for t in project.takes), default=0) + 1


def _copy_artifact(source_root: Path, rel_path: str, dest_root: Path) -> None:
    """Copy one `PHASE_ARTIFACTS` entry from `source_root` to `dest_root`,
    preserving its relative path. A missing source is not an error - a
    phase whose artifact was never written (it hadn't run yet, or failed)
    simply contributes nothing, the same "no history" tolerance
    `estimate_seconds` applies to phase timing."""
    source = source_root / rel_path
    dest = dest_root / rel_path
    if source.is_dir():
        shutil.copytree(source, dest)
    elif source.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def clone_take(
    project: ProjectDoc,
    source_take_id: int,
    keep_phase_keys: Sequence[str],
    *,
    label: str | None = None,
    changed_ids: Sequence[str] = (),
    rerolled_from: str | None = None,
    settings_snapshot: dict | None = None,
) -> Take:
    """Create a new take that keeps `keep_phase_keys`' artifacts from
    `source_take_id` and nothing else.

    Only the `PHASE_ARTIFACTS` belonging to `keep_phase_keys` are copied
    (from the source take's OWN folder, `take_folder(project,
    source_take_id)` - not the project's legacy top-level folder); phases
    not being kept simply have nothing copied for them and are absent from
    the new take's `phases`, exactly like an un-run phase is absent from a
    fresh `ProjectDoc.phases`. `master_output` is never copied, regardless
    of `keep_phase_keys` (see module docstring).

    The new take's folder is created and populated BEFORE it is appended to
    `project.takes` - if any copy fails partway (disk full, permissions,
    anything), the partial folder is removed and the exception re-raised,
    leaving `project.takes` exactly as it was before this call. Raises
    `ValueError` if `source_take_id` names no take on `project`.
    """
    source_take = next((t for t in project.takes if t.id == source_take_id), None)
    if source_take is None:
        raise ValueError(f"no take {source_take_id} on this project")

    new_id = _next_take_id(project)
    dest_folder = take_folder(project, new_id)
    # `take_root`, not `take_folder`: a `storage="root"` take (take 1 of
    # anything created after Slice 8) keeps its artifacts in the project
    # folder itself, and copying from an empty `takes/1/` would make every
    # "new take" off a first run start with nothing - the failure mode
    # `migrate_to_takes`' deliberate double-storage exists to avoid for
    # legacy projects.
    source_folder = take_root(project, source_take_id)

    try:
        dest_folder.mkdir(parents=True, exist_ok=False)
        for phase_key in keep_phase_keys:
            for rel_path in PHASE_ARTIFACTS.get(phase_key, ()):
                _copy_artifact(source_folder, rel_path, dest_folder)
    except Exception:
        shutil.rmtree(dest_folder, ignore_errors=True)
        raise

    kept_phases = {
        key: source_take.phases[key]
        for key in keep_phase_keys
        if key in source_take.phases
    }

    new_take = Take(
        id=new_id,
        label=label or f"Take {new_id}",
        created=datetime.now(timezone.utc),
        settings_snapshot=dict(settings_snapshot) if settings_snapshot is not None else dict(source_take.settings_snapshot),
        changed_from=source_take_id,
        changed_ids=list(changed_ids),
        rerolled_from=rerolled_from,
        phases=kept_phases,
        master_output=None,
    )
    # Registration - the moment this take becomes real - happens only here,
    # after every copy above already succeeded.
    project.takes.append(new_take)
    return new_take


def delete_take(project: ProjectDoc, take_id: int) -> None:
    """Remove `take_id` and its folder from `project`.

    Refuses (`ValueError`) to delete the last remaining take - a project
    with takes must always have at least one. If the deleted take was
    `current_take_id`, the pointer moves to the remaining take with the
    highest id (the most recently created one still standing) rather than
    being left dangling on a take that no longer exists.
    """
    if len(project.takes) <= 1:
        raise ValueError("cannot delete the last remaining take")

    index = next((i for i, t in enumerate(project.takes) if t.id == take_id), None)
    if index is None:
        raise ValueError(f"no take {take_id} on this project")

    shutil.rmtree(take_folder(project, take_id), ignore_errors=True)
    del project.takes[index]

    if project.current_take_id == take_id:
        project.current_take_id = max(t.id for t in project.takes)


def migrate_to_takes(project: ProjectDoc) -> None:
    """Turn a legacy (pre-Slice-5) single-output project into take 1,
    marked current.

    A no-op if `project` already has takes (idempotent - safe to call on
    every load rather than requiring a caller to check first). Copies
    (never moves) the project's existing top-level artifact folders and
    `master_output.mp4`, if any, into `take_folder(project, 1)` - the
    originals are left in place at the project's top level, since other
    code in this codebase (e.g. `avg.pipeline.phases.render.RenderPhase`,
    which still renders "in place" into `ctx.project.folder`) has not yet
    been migrated to read from a take's own folder; that migration is a
    later task's job, not this one's. Take 1's `phases` is a copy of
    whatever `project.phases` already recorded (a legacy import already
    reconstructs per-phase done/pending status - see
    `avg.config.legacy_import._phase_statuses`), and its `master_output`
    points at take 1's own copy, not the original path.

    Like `clone_take`, the destination folder is populated before
    `project.takes`/`project.current_take_id` are touched - a failure
    partway removes the partial folder and re-raises, leaving `project`
    exactly as it was.
    """
    if project.takes:
        return

    project_folder = Path(project.folder)
    dest_folder = take_folder(project, 1)

    try:
        dest_folder.mkdir(parents=True, exist_ok=False)
        for name in _LEGACY_ARTIFACT_DIRS:
            source = project_folder / name
            if source.is_dir():
                shutil.copytree(source, dest_folder / name)

        new_master_output: Path | None = None
        if project.master_output is not None and Path(project.master_output).is_file():
            new_master_output = dest_folder / "master_output.mp4"
            shutil.copy2(project.master_output, new_master_output)
    except Exception:
        shutil.rmtree(dest_folder, ignore_errors=True)
        raise

    take = Take(
        id=1,
        label="Take 1",
        created=project.created,
        settings_snapshot={},
        changed_from=None,
        changed_ids=[],
        rerolled_from=None,
        phases=dict(project.phases),
        master_output=new_master_output,
    )
    project.takes = [take]
    project.current_take_id = 1


def estimate_seconds(project: ProjectDoc, phase_keys: Sequence[str]) -> tuple[int | None, list[str]]:
    """`(seconds, unknown_phase_keys)` - a duration estimate for running
    `phase_keys` again, built from this project's own take history rather
    than a fixed guess.

    For each key, takes `duration_ms` from the most recent take (highest
    id) in which that phase actually completed (`status == "done"` with a
    recorded `duration_ms`) - a take where the phase was kept-but-not-rerun
    still counts, since its `PhaseRecord` (copied forward by `clone_take`)
    is the same completed record. A key with no such history anywhere is
    named in `unknown_phase_keys` and contributes nothing to the total.
    If NOT ONE key has any history, returns `(None, list(phase_keys))` - a
    fabricated estimate (e.g. `0`) would be worse than admitting there is
    no basis for one yet.
    """
    unknown: list[str] = []
    total_ms = 0
    found_any = False

    ordered_takes = sorted(project.takes, key=lambda t: t.id, reverse=True)
    for key in phase_keys:
        record = next(
            (
                rec
                for take in ordered_takes
                if (rec := take.phases.get(key)) is not None and rec.status == "done" and rec.duration_ms is not None
            ),
            None,
        )
        if record is None:
            unknown.append(key)
            continue
        total_ms += record.duration_ms  # type: ignore[operator]  # duration_ms is not None, checked above
        found_any = True

    if not found_any:
        return None, list(phase_keys)
    return round(total_ms / 1000), unknown
