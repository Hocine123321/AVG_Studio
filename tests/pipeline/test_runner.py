"""Tests for the phase runner (`avg.pipeline.runner`) and the resume helper
it's designed to be driven with (`avg.pipeline.registry.next_phase_id`).

Uses a local `RecordingPhase` test double rather than `REGISTRY`'s real
stubs for every scenario that needs controlled failure/cancellation/call
tracking - `REGISTRY`'s stubs always succeed and do nothing, so they can't
exercise those paths. A handful of tests still run against the real
`REGISTRY` (the runner's default) as a smoke test that the wiring works
end-to-end with the actual production phase list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from avg.config.channels import Channel
from avg.core.errors import AvgError, ErrorKind
from avg.core.events import PhaseFailed, PhaseFinished, PhaseStarted, PipelineCancelled
from avg.core.models import ProjectDoc
from avg.pipeline import registry as registry_mod
from avg.pipeline.phase import CancellationToken, PhaseContext
from avg.pipeline.registry import REGISTRY
from avg.pipeline.runner import UnknownPhaseError, run

# Drawn from the real REGISTRY rather than restated as a literal list - if
# REGISTRY's order or naming ever changes, these fixtures follow it instead
# of silently testing against a stale copy (see this task's own invariant
# #1: REGISTRY is the only place phase order is declared).
_KEYS = [phase.key for phase in REGISTRY]
_NAMES = [phase.name for phase in REGISTRY]


def _project(**overrides: Any) -> ProjectDoc:
    defaults: dict[str, Any] = dict(
        id="proj1", title="Test Topic", folder=Path("."), created="2026-07-26T00:00:00",
    )
    defaults.update(overrides)
    return ProjectDoc(**defaults)


def _channel() -> Channel:
    return Channel(id="ch", label="Ch", settings={})


@dataclass
class RecordingPhase:
    """A test double satisfying `avg.pipeline.phase.Phase`: records every
    project id it was called with, optionally raises, optionally emits
    canned events, and optionally cancels a given token mid-run - just
    enough control for the runner's contract to be verified directly."""

    id: int
    key: str
    name: str
    calls: list[str] = field(default_factory=list)
    fail_with: Exception | None = None
    emits: list[object] = field(default_factory=list)
    cancel_after: CancellationToken | None = None

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return True

    def run(self, ctx: PhaseContext) -> None:
        self.calls.append(ctx.project.id)
        for event in self.emits:
            ctx.emit(event)
        if self.cancel_after is not None:
            self.cancel_after.cancel()
        if self.fail_with is not None:
            raise self.fail_with


def _phases(n: int = 8, fail_at: int | None = None, fail_with: Exception | None = None) -> list[RecordingPhase]:
    out = []
    for i in range(n):
        fw = fail_with if (fail_at is not None and i == fail_at) else None
        out.append(RecordingPhase(id=i, key=_KEYS[i], name=_NAMES[i], fail_with=fw))
    return out


# ── Full sequence ────────────────────────────────────────────────────────

def test_full_sequence_runs_every_phase_in_order():
    project = _project()
    phases = _phases()

    events = list(run(project, channel=_channel(), phases=phases))

    assert all(p.calls == ["proj1"] for p in phases)
    finished_keys = [e.phase for e in events if isinstance(e, PhaseFinished)]
    assert finished_keys == _KEYS
    assert all(project.phases[key].status == "done" for key in _KEYS)
    # started -> finished pairs are also in registry order and nothing else
    # (no failure, no cancellation) slipped into the stream.
    started_keys = [e.phase for e in events if isinstance(e, PhaseStarted)]
    assert started_keys == _KEYS
    assert not any(isinstance(e, (PhaseFailed, PipelineCancelled)) for e in events)


def test_default_registry_runs_all_eight_stub_phases_successfully():
    """Smoke test against the real production registry, not the fake."""
    project = _project()

    events = list(run(project, channel=_channel()))

    finished = [e for e in events if isinstance(e, PhaseFinished)]
    assert len(finished) == 8
    assert all(project.phases[phase.key].status == "done" for phase in REGISTRY)


# ── Resume ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("start_from", range(8))
def test_resume_from_each_phase_runs_only_phases_from_there_on(start_from):
    project = _project()
    phases = _phases()

    list(run(project, channel=_channel(), start_from=start_from, phases=phases))

    for phase in phases:
        if phase.id < start_from:
            assert phase.calls == [], f"phase {phase.key} should not have run"
            assert phase.key not in project.phases
        else:
            assert phase.calls == ["proj1"]
            assert project.phases[phase.key].status == "done"


def test_next_phase_id_drives_a_true_resume_after_an_only_run():
    """`only=` runs one phase; `next_phase_id` (reading the real REGISTRY's
    order) says where a full resume should pick up next; `start_from` there
    runs the rest. Ties the three pieces of this task together the way a
    real caller (a later API route) will."""
    project = _project()
    phases = _phases()

    list(run(project, channel=_channel(), only="brief", phases=phases))
    assert registry_mod.next_phase_id(project) == 1

    list(run(project, channel=_channel(), start_from=1, phases=phases))
    assert all(p.calls == ["proj1"] for p in phases)
    assert registry_mod.next_phase_id(project) == 8


def test_next_phase_id_is_zero_for_a_brand_new_project():
    assert registry_mod.next_phase_id(_project()) == 0


def test_next_phase_id_does_not_advance_past_a_failed_phase():
    project = _project()
    phases = _phases(fail_at=2, fail_with=AvgError(ErrorKind.bug, "boom"))

    list(run(project, channel=_channel(), phases=phases))

    assert registry_mod.next_phase_id(project) == 2


# ── only= ────────────────────────────────────────────────────────────────

def test_only_runs_exactly_one_phase():
    project = _project()
    phases = _phases()

    events = list(run(project, channel=_channel(), only="voice", phases=phases))

    ran = [p.key for p in phases if p.calls]
    assert ran == ["voice"]
    assert set(project.phases.keys()) == {"voice"}
    assert project.phases["voice"].status == "done"
    assert isinstance(events[-1], PhaseFinished) and events[-1].phase == "voice"


def test_only_ignores_start_from():
    project = _project()
    phases = _phases()

    list(run(project, channel=_channel(), start_from=5, only="brief", phases=phases))

    assert phases[0].calls == ["proj1"]
    assert set(project.phases.keys()) == {"brief"}


def test_only_with_unknown_key_raises_and_touches_nothing():
    project = _project()
    phases = _phases()

    with pytest.raises(UnknownPhaseError):
        list(run(project, channel=_channel(), only="does-not-exist", phases=phases))

    assert project.phases == {}
    assert all(p.calls == [] for p in phases)


# ── Failure halts the run without corrupting the document ─────────────────

def test_failing_phase_halts_run_records_error_and_leaves_earlier_output_intact():
    project = _project()
    err = AvgError(ErrorKind.quota, "out of credits", detail="daily cap")
    phases = _phases(fail_at=3, fail_with=err)  # "visuals" fails

    events = list(run(project, channel=_channel(), phases=phases))

    for phase in phases[:3]:
        assert project.phases[phase.key].status == "done"
    assert project.phases["visuals"].status == "failed"
    assert project.phases["visuals"].error == "out of credits"
    for phase in phases[4:]:
        assert phase.calls == []
        assert phase.key not in project.phases

    failed = [e for e in events if isinstance(e, PhaseFailed)]
    assert len(failed) == 1
    assert failed[0].phase == "visuals"
    assert failed[0].kind == ErrorKind.quota
    assert failed[0].detail == "daily cap"
    assert not any(isinstance(e, PhaseFinished) and e.phase == "visuals" for e in events)


def test_a_plain_exception_is_classified_as_bug_and_still_halts_cleanly():
    project = _project()
    phases = _phases(fail_at=0, fail_with=RuntimeError("boom"))

    events = list(run(project, channel=_channel(), phases=phases))

    assert project.phases["brief"].status == "failed"
    assert project.phases["brief"].error == "boom"
    failed = [e for e in events if isinstance(e, PhaseFailed)]
    assert len(failed) == 1 and failed[0].kind == ErrorKind.bug
    assert all(p.calls == [] for p in phases[1:])


def test_retrying_a_failed_phase_clears_its_error_on_success():
    project = _project()
    phases = _phases(fail_at=0, fail_with=RuntimeError("boom"))
    list(run(project, channel=_channel(), phases=phases))
    assert project.phases["brief"].status == "failed"

    phases[0].fail_with = None  # simulate the underlying problem being fixed
    list(run(project, channel=_channel(), only="brief", phases=phases))

    assert project.phases["brief"].status == "done"
    assert project.phases["brief"].error is None


# ── Cancellation between phases ────────────────────────────────────────────

def test_cancellation_between_phases_stops_before_the_next_one():
    project = _project()
    phases = _phases()
    token = CancellationToken()
    phases[0].cancel_after = token  # cancel once "brief" has finished running

    events = list(run(project, channel=_channel(), phases=phases, cancel_token=token))

    assert phases[0].calls == ["proj1"]
    assert project.phases["brief"].status == "done"
    for phase in phases[1:]:
        assert phase.calls == []
        assert phase.key not in project.phases

    cancelled = [e for e in events if isinstance(e, PipelineCancelled)]
    assert len(cancelled) == 1
    assert cancelled[0].phase == "script"  # the phase that would have run next
    assert not any(isinstance(e, PhaseFailed) for e in events)


def test_cancellation_set_before_the_run_starts_runs_nothing():
    project = _project()
    phases = _phases()
    token = CancellationToken()
    token.cancel()

    events = list(run(project, channel=_channel(), phases=phases, cancel_token=token))

    assert all(p.calls == [] for p in phases)
    assert project.phases == {}
    cancelled = [e for e in events if isinstance(e, PipelineCancelled)]
    assert len(cancelled) == 1
    assert cancelled[0].phase == "brief"


# ── PhaseContext / emit wiring ─────────────────────────────────────────────

def test_ctx_emit_events_surface_before_phase_finished():
    project = _project()
    phases = _phases(n=1)
    marker = object()
    phases[0].emits = [marker]

    events = list(run(project, channel=_channel(), phases=phases))

    assert isinstance(events[0], PhaseStarted)
    assert events[1] is marker
    assert isinstance(events[2], PhaseFinished)


def test_phase_receives_the_given_channel_and_router():
    project = _project()
    seen: dict[str, Any] = {}

    class Probe:
        id, key, name = 0, "brief", "Brief"

        def inputs_ready(self, ctx: PhaseContext) -> bool:
            return True

        def run(self, ctx: PhaseContext) -> None:
            seen["channel"] = ctx.channel
            seen["router"] = ctx.router

    channel = _channel()
    router = object()
    list(run(project, channel=channel, router=router, phases=[Probe()]))

    assert seen["channel"] is channel
    assert seen["router"] is router
