"""The maintained cost table — what a video is estimated to cost, per phase.

**These are maintained estimates, not measured spend.** Nothing in this app
meters tokens, images or audio seconds; no provider response is inspected
for a price. Every figure below is a number a human wrote down and has to
keep current as models and provider pricing change. A phase's cost is flat:
`visuals` costs the same whether the script produced 3 scenes or 15.

That is a deliberate choice (user, 2026-07-28) over metering real usage,
taken from the design mockup's own `PHASE_COST` table
(`F:\\Downloads\\organic_night_v2.html`, line 343). It buys a spend figure
today at the price of a figure that drifts. Anything shown to the user from
this module must read as an estimate, never as an invoice.

── Why the keys are not the pipeline's phase keys ──────────

`REGISTRY` has one `script` phase; this table splits it into `draft` and
`segmentation`, matching the mockup and the Studio phase tree that Slice 6A
added (`web/src/phases.ts`). The split exists because those two passes can
run different models at genuinely different prices - which is exactly what
`script_draft` / `script_segmentation` in the settings catalogue configure.

`phase_cost()` is the seam: it takes a *pipeline* phase key and returns
what that whole phase costs, so a caller holding a `PhaseRecord` never has
to know about the split. `LEAF_COST` is for the models table, which shows
the two passes as separate rows.
"""

from __future__ import annotations

from typing import Mapping

CURRENCY = "USD"

# One entry per display leaf, in pipeline order. `voice` and `render` are
# zero because Edge TTS is free and rendering is local CPU - not because
# they are unknown.
LEAF_COST: dict[str, float] = {
    "brief": 0.004,
    "draft": 0.007,
    "segmentation": 0.004,
    "director1": 0.002,
    "visuals": 0.180,
    "voice": 0.0,
    "director2": 0.002,
    "screen_text": 0.014,
    "render": 0.0,
}

# Which leaves make up each pipeline phase key. Every key here is a real
# `avg.pipeline.registry` phase key; a phase absent from this map costs
# nothing (there are none today, and `test_costs.py` enforces that the map
# covers the registry exactly).
PHASE_LEAVES: dict[str, tuple[str, ...]] = {
    "brief": ("brief",),
    "script": ("draft", "segmentation"),
    "director1": ("director1",),
    "visuals": ("visuals",),
    "voice": ("voice",),
    "director2": ("director2",),
    "screen_text": ("screen_text",),
    "render": ("render",),
}

# How a target length scales the whole-video estimate. Sub-linear on
# purpose: a longer video needs more scenes and more narration, but the
# brief and both director passes cost the same at any length. Interpolated
# from the mockup's four presets onto the six `length_preset` offers in
# `avg.core.settings_schema`.
LENGTH_MULTIPLIER: dict[str, float] = {
    "30s": 0.6,
    "60s": 1.0,
    "90s": 1.4,
    "2m": 1.8,
    "4m": 2.9,
    "8m": 4.0,
}


def phase_cost(phase_key: str) -> float:
    """What one pipeline phase costs, summed over its display leaves."""
    return sum(LEAF_COST[leaf] for leaf in PHASE_LEAVES.get(phase_key, ()))


def base_cost() -> float:
    """A whole video at the reference length (the `1.0` multiplier)."""
    return sum(LEAF_COST.values())


def estimate(length_preset: str) -> float:
    """The estimated cost of one video at `length_preset`.

    An unknown preset scales by 1.0 rather than raising - this feeds a
    number on a screen, and a settings value this module has not caught up
    with must not break the Create screen.
    """
    return base_cost() * LENGTH_MULTIPLIER.get(length_preset, 1.0)


def spent(phase_statuses: Mapping[str, str], *, script_substate: str = "pending") -> float:
    """What has been spent on one project so far.

    Counts every phase that is `done` or `running` - work in progress has
    already been paid for, matching the mockup's `projectCost`. `script` is
    the one phase counted per-leaf: a `partial` script (narration written,
    scenes not yet) has paid for the draft only, which is precisely what
    `avg.pipeline.phases.writing.script_substate` reports.
    """
    total = 0.0
    for phase_key, status in phase_statuses.items():
        if status not in ("done", "running"):
            continue
        if phase_key == "script":
            total += LEAF_COST["draft"]
            if not (status == "running" or script_substate == "partial"):
                total += LEAF_COST["segmentation"]
            continue
        total += phase_cost(phase_key)
    return total
