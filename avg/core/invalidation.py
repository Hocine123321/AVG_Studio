"""Maps a set of changed setting ids to the phases a re-run must redo.

This is the pure-data heart of Slice 5's "takes" feature: a re-run should
redo only the phases a change actually invalidates, not the whole pipeline
(the legacy PowerShell app's "Quick Redo" had no such notion - it always
re-ran everything). Every `SettingSpec` in `avg.core.settings_schema`
declares `invalidates: str | None`, the first phase key its value affects;
this module reads that declaration and, given the ids that changed, figures
out where a re-run should start and which phase keys it will touch.

Order is never hardcoded here - it always comes from
`avg.pipeline.registry.REGISTRY`, the single source of phase order. This
module does no I/O, calls no model, and does not import `ProjectDoc`: it
maps setting ids to phases and nothing more. `estimate_seconds` (which
needs take history) lives elsewhere, alongside the model it depends on.
"""

from __future__ import annotations

from collections.abc import Iterable

from avg.core.settings_schema import BY_ID
from avg.pipeline.registry import BY_KEY, REGISTRY


def first_invalid_phase(changed_ids: Iterable[str]) -> int | None:
    """The lowest phase id any of `changed_ids` invalidates, or `None`.

    Unknown setting ids are ignored rather than raising - a stale or
    misspelled id in a change set should not crash a re-run. Settings whose
    `invalidates` is `None` (not a creative input) are likewise skipped.
    """
    lowest: int | None = None
    for setting_id in changed_ids:
        spec = BY_ID.get(setting_id)
        if spec is None or spec.invalidates is None:
            continue
        phase = BY_KEY.get(spec.invalidates)
        if phase is None:
            continue
        if lowest is None or phase.id < lowest:
            lowest = phase.id
    return lowest


def phases_to_rerun(changed_ids: Iterable[str]) -> list[str]:
    """Phase keys from `first_invalid_phase(changed_ids)` onward, in `REGISTRY` order.

    Empty if none of `changed_ids` invalidate anything.
    """
    start_id = first_invalid_phase(changed_ids)
    if start_id is None:
        return []
    return [phase.key for phase in REGISTRY if phase.id >= start_id]
