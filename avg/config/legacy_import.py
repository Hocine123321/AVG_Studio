"""Project-folder reader: maps a folder's `project.json` onto a `ProjectDoc`.

Despite the name (kept for history - this module started as a one-way
importer for legacy PowerShell-app project folders, and still is one), this
is also what `avg.api.app.create_app` calls on EVERY launch
(`import_all(output_root)`) to reload every project already living under
the user's chosen output root - AVG Studio's own projects included, not just
ones inherited from the old app. So `import_project` has to handle two
completely different `project.json` shapes from the same folder layout:

  - **AVG Studio's own** (`avg.pipeline.create.save_project`'s output): a
    direct `ProjectDoc.model_dump_json()` dump - `id`/`title`/`folder`/
    `created` at the top level, `phases` already a `dict[str, PhaseRecord]`,
    `takes`/`current_take_id` present since Slice 5. `ProjectDoc.
    model_validate(raw)` on this is not a re-derivation, it's just parsing
    the model back out of its own serialization - takes, labels,
    `current_take_id`, real per-phase timing, all round-trip for free
    because pydantic already knows this shape.
  - **The old PowerShell app's** (`D:\\AI_Video_Gen`'s `project.json`): a
    `meta`-nested shape with no `id`/`title`(top-level)/`folder`/`created`/
    `phases` fields at all, and no notion of takes. `ProjectDoc.
    model_validate` raises `ValidationError` on it (those fields are
    required), which is the signal to fall through to the derivation logic
    below - `_phase_statuses` re-derives done/pending from `meta.phase`
    since the old app never recorded real per-phase timing at all, and a
    legacy project's `takes` stays `[]` (`ProjectDoc`'s own default) until
    `avg.core.takes.migrate_to_takes` is asked to create take 1 for it - the
    ten real legacy projects on this machine take this path.

Never writes to the folder it reads, either way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from avg.core.models import PhaseRecord, ProjectDoc
from avg.core.takes import alias_root_take_phases
from avg.pipeline.registry import REGISTRY

# New pipeline's phase order (resume position, not the legacy phase names).
# Derived from `avg.pipeline.registry.REGISTRY` - the single source of truth
# for phase order (see that module's docstring) - rather than restated here.
PHASE_ORDER: list[str] = [phase.key for phase in REGISTRY]

# Legacy phase names that don't match the new order 1:1.
_PHASE_ALIASES: dict[str, str] = {
    "prompts": "script",
    "images": "visuals",
    "visuals": "visuals",
    "audio": "voice",
    "scene_timings": "director2",
    "smart_editing": "director2",
}

_FORM_TO_ASPECT_RATIO: dict[str, str] = {
    "shortform": "9:16",
    "longform": "16:9",
}


@dataclass
class ImportReport:
    imported: list[ProjectDoc] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)


def _derive_aspect_ratio(meta: dict) -> str:
    return _FORM_TO_ASPECT_RATIO.get(meta.get("form"), "9:16")


def _phase_statuses(raw_phase: str | None) -> dict[str, PhaseRecord]:
    """Map a legacy resume-phase value onto per-phase done/pending records.

    `raw_phase` names the phase the legacy app would resume *into*, so every
    phase before it is done and it (and everything after) is pending.
    `"complete"` is a sentinel, not a phase name, and means all done.
    An unrecognised value degrades to all-pending rather than raising.
    """
    if raw_phase == "complete":
        return {name: PhaseRecord(name=name, status="done") for name in PHASE_ORDER}

    resolved = _PHASE_ALIASES.get(raw_phase, raw_phase)
    if resolved not in PHASE_ORDER:
        return {name: PhaseRecord(name=name, status="pending") for name in PHASE_ORDER}

    reached_index = PHASE_ORDER.index(resolved)
    return {
        name: PhaseRecord(name=name, status="done" if i < reached_index else "pending")
        for i, name in enumerate(PHASE_ORDER)
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def import_project(folder: Path) -> ProjectDoc:
    """Read one project folder and build a ProjectDoc from it - AVG Studio's
    own, or a legacy PowerShell-app one (see this module's docstring for how
    the two are told apart).

    Raises whatever exception reading/parsing/validation produces; callers
    that want failures collected instead of raised should use `import_all`.
    """
    folder = Path(folder)
    project_data = _read_json(folder / "project.json")

    # AVG Studio's own project.json validates directly as a ProjectDoc -
    # takes, current_take_id, and real per-phase timing all come along for
    # free, with no re-derivation. `folder` is overridden with the actual
    # argument rather than trusting whatever path was recorded at creation
    # time, in case the project directory was ever moved or the output root
    # renamed - the same "the folder I was handed wins" rule the legacy
    # path below already follows.
    try:
        native_doc = ProjectDoc.model_validate(project_data)
    except ValidationError:
        native_doc = None
    if native_doc is not None:
        if Path(native_doc.folder) != folder:
            native_doc = native_doc.model_copy(update={"folder": folder})
        # Re-establish the aliasing that `new_project` set up and that a
        # round trip through JSON necessarily broke: a take storing its
        # artifacts at the project root shares one records dict with the
        # project, or a run advances one copy while the screen reads the
        # other. See `avg.core.takes.alias_root_take_phases`.
        alias_root_take_phases(native_doc)
        return native_doc

    meta = project_data.get("meta", {})

    pipeline_state_path = folder / "pipeline_state.json"
    pipeline_state = _read_json(pipeline_state_path) if pipeline_state_path.exists() else {}

    title = meta.get("topic") or pipeline_state.get("topic") or folder.name
    raw_phase = meta.get("phase", pipeline_state.get("phase"))
    created = meta.get("created", pipeline_state.get("created"))

    master_output_path = folder / "master_output.mp4"
    master_output = master_output_path if master_output_path.exists() else None

    return ProjectDoc(
        id=folder.name,
        title=title,
        folder=folder,
        aspect_ratio=_derive_aspect_ratio(meta),
        short_edge=1080,
        created=created,
        phases=_phase_statuses(raw_phase),
        master_output=master_output,
    )


def import_all(output_root: Path) -> ImportReport:
    """Import every project folder under `output_root`.

    Folders whose name starts with `_` are scratch (e.g. `_filters_preview`)
    and are skipped entirely - absent from both `imported` and `failed`.
    Any other folder that raises during import is recorded in `failed`
    rather than propagating.
    """
    output_root = Path(output_root)
    report = ImportReport()
    if not output_root.exists():
        return report

    for entry in sorted(output_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        try:
            report.imported.append(import_project(entry))
        except Exception as exc:  # noqa: BLE001 - collected, not swallowed
            report.failed.append((entry, str(exc)))

    return report
