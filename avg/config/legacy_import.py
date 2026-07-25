"""One-way importer: legacy PowerShell-app project folders -> ProjectDoc.

Reads `project.json` (and, where present, `pipeline_state.json`) from a
project folder produced by the old app and maps it onto the new
`ProjectDoc` / `PhaseRecord` models. Never writes to the folder it reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from avg.core.models import PhaseRecord, ProjectDoc

# New pipeline's phase order (resume position, not the legacy phase names).
PHASE_ORDER: list[str] = [
    "brief",
    "script",
    "director1",
    "visuals",
    "voice",
    "director2",
    "captions",
    "render",
]

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
    """Read one legacy project folder and build a ProjectDoc from it.

    Raises whatever exception reading/parsing/validation produces; callers
    that want failures collected instead of raised should use `import_all`.
    """
    folder = Path(folder)
    project_data = _read_json(folder / "project.json")
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
