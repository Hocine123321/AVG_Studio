"""Tests for Slice 5 Task 3.5: persisting `project.json` write-through.

Covers `avg.pipeline.create.save_project` (the one atomic writer),
`avg.pipeline.runner.run`'s `on_phase_change` hook (write-through per-phase
persistence), and `avg.config.legacy_import.import_project`'s round trip of
AVG Studio's own project.json shape (takes, `current_take_id`) alongside its
existing legacy-shape handling.

Uses a local `RecordingPhase` (mirroring `tests/pipeline/test_runner.py`'s
own test double) rather than the real `REGISTRY` - this suite is about
persistence, not the real phases, and must never make a network call.
Everything here runs against `tmp_path`; nothing touches `D:\\AI_Video_Gen`
(read-only, real user projects) - the legacy-shape test below reuses the
same synthetic `tests/fixtures/legacy_project` fixture the existing
`tests/config/test_legacy_import.py` suite already uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from avg.config.channels import Channel
from avg.config.legacy_import import import_project
from avg.core.errors import AvgError, ErrorKind
from avg.core.models import PhaseRecord, ProjectDoc, Take
from avg.core.settings_schema import resolve_channel_settings
from avg.pipeline.create import save_project
from avg.pipeline.phase import CancellationToken, PhaseContext
from avg.pipeline.runner import run

FIXTURE_LEGACY = Path(__file__).parent.parent / "fixtures" / "legacy_project"


def _channel() -> Channel:
    return Channel(id="ch", label="Ch", settings={})


# `_channel()` always returns empty settings, so every scope="channel"
# setting resolves to the SettingSpec's own default (Slice 19 §2.3).
_CHANNEL_VALUES = resolve_channel_settings({}, {})


def _project(tmp_path: Path, **overrides) -> ProjectDoc:
    folder = tmp_path / "proj"
    folder.mkdir(parents=True, exist_ok=True)
    defaults: dict = dict(
        id="proj1",
        title="Test Topic",
        folder=folder,
        created=datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return ProjectDoc(**defaults)


@dataclass
class RecordingPhase:
    """Same test double `tests/pipeline/test_runner.py` uses: records calls,
    optionally raises, so both the success and failure persistence paths can
    be exercised without any real phase or provider."""

    id: int
    key: str
    name: str
    description: str = "A recording stub."
    calls: list[str] = field(default_factory=list)
    fail_with: Exception | None = None

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return True

    def run(self, ctx: PhaseContext) -> None:
        self.calls.append(ctx.project.id)
        if self.fail_with is not None:
            raise self.fail_with


def _phases(n: int, fail_at: int | None = None, fail_with: Exception | None = None) -> list[RecordingPhase]:
    out = []
    for i in range(n):
        fw = fail_with if (fail_at is not None and i == fail_at) else None
        out.append(RecordingPhase(id=i, key=f"phase{i}", name=f"Phase {i}", fail_with=fw))
    return out


# ── save_project: atomic writer ─────────────────────────────────────────────


def test_save_project_writes_a_readable_project_json(tmp_path):
    project = _project(tmp_path)
    save_project(project)

    raw = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    assert raw["id"] == "proj1"
    assert raw["title"] == "Test Topic"


def test_save_project_overwrites_cleanly_on_a_second_call(tmp_path):
    project = _project(tmp_path)
    save_project(project)

    project.title = "Renamed"
    save_project(project)

    raw = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    assert raw["title"] == "Renamed"


def test_save_project_leaves_no_temp_file_behind(tmp_path):
    project = _project(tmp_path)
    save_project(project)

    leftovers = list(project.folder.glob(".project-*.tmp"))
    assert leftovers == []


def test_an_interrupted_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    """Simulates a crash between the temp-file write and `os.replace`: the
    real `project.json` on disk must still hold its old, complete contents
    and remain readable - never a half-written file."""
    project = _project(tmp_path)
    save_project(project)  # first, successful write
    before = (project.folder / "project.json").read_text(encoding="utf-8")

    import avg.pipeline.create as create_mod

    def _boom(*args, **kwargs):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(create_mod.os, "replace", _boom)

    project.title = "Should never land"
    with pytest.raises(OSError):
        save_project(project)

    after = (project.folder / "project.json").read_text(encoding="utf-8")
    assert after == before
    # The document is still valid JSON reflecting the OLD state, not the
    # attempted new one.
    raw = json.loads(after)
    assert raw["title"] == "Test Topic"
    # And the crash doesn't litter the folder with a leftover temp file.
    assert list(project.folder.glob(".project-*.tmp")) == []


# ── runner's on_phase_change: write-through persistence ─────────────────────


def test_a_completing_phase_writes_the_file(tmp_path):
    project = _project(tmp_path)
    phases = _phases(1)

    list(run(project, channel=_channel(), channel_values=_CHANNEL_VALUES, phases=phases, on_phase_change=lambda: save_project(project)))

    raw = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    assert raw["phases"]["phase0"]["status"] == "done"


def test_a_failing_phase_still_writes_the_file(tmp_path):
    project = _project(tmp_path)
    err = AvgError(ErrorKind.bug, "boom")
    phases = _phases(1, fail_at=0, fail_with=err)

    list(run(project, channel=_channel(), channel_values=_CHANNEL_VALUES, phases=phases, on_phase_change=lambda: save_project(project)))

    raw = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    assert raw["phases"]["phase0"]["status"] == "failed"
    assert raw["phases"]["phase0"]["error"] == "boom"


def test_killing_mid_run_keeps_the_completed_phases_on_disk(tmp_path):
    """Drives the runner generator by hand, stopping after the first phase's
    events but before ever asking for the second phase - simulating a
    process kill between phase 1 finishing and phase 2 starting. Only
    events already pulled out of a generator have run; nothing after the
    point consumption stops ever executes, exactly like a killed process
    never reaches the next iteration of the runner's own `for` loop."""
    project = _project(tmp_path)
    phases = _phases(2)

    gen = run(project, channel=_channel(), channel_values=_CHANNEL_VALUES, phases=phases, on_phase_change=lambda: save_project(project))
    # Pull events only through phase0's PhaseFinished, then simply stop
    # consuming - the "kill". Driven by event TYPE rather than a fixed pull
    # count: Slice 17 interleaves ActivityEvents into this same stream, and
    # `on_phase_change` (the write-through this test checks) always fires
    # before PhaseFinished is yielded regardless of how many other events
    # precede it, so stopping right after PhaseFinished is still exactly
    # "the kill lands the instant phase0's write-through has happened".
    from avg.core.events import PhaseFinished as _PhaseFinished

    for event in gen:
        if isinstance(event, _PhaseFinished):
            break
    # gen is now abandoned, never advanced into phase1's on_phase_change.

    raw = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    assert raw["phases"]["phase0"]["status"] == "done"
    assert "phase1" not in raw["phases"]
    assert phases[1].calls == []  # phase1 never actually ran


def test_no_persistence_happens_without_the_hook(tmp_path):
    """Default behaviour (no `on_phase_change` passed) must not write
    anything - every existing runner test relies on this staying silent."""
    project = _project(tmp_path)
    phases = _phases(1)

    list(run(project, channel=_channel(), channel_values=_CHANNEL_VALUES, phases=phases))

    assert not (project.folder / "project.json").exists()


# ── Take round trip through save_project + import_project ──────────────────


def test_a_take_survives_a_write_and_reread_round_trip(tmp_path):
    project = _project(tmp_path)
    take = Take(
        id=2,
        label="Take 2 · brighter grade",
        created=datetime(2026, 7, 25, 9, 0, 0, tzinfo=timezone.utc),
        settings_snapshot={"aspect_ratio": "16:9"},
        changed_from=1,
        changed_ids=["color_grade"],
        phases={"brief": PhaseRecord(name="brief", status="done", duration_ms=1200)},
        master_output=None,
    )
    project.takes = [take]
    project.current_take_id = 2

    save_project(project)
    reloaded = import_project(project.folder)

    assert reloaded.current_take_id == 2
    assert len(reloaded.takes) == 1
    reloaded_take = reloaded.takes[0]
    assert reloaded_take.id == 2
    assert reloaded_take.label == "Take 2 · brighter grade"
    assert reloaded_take.settings_snapshot == {"aspect_ratio": "16:9"}
    assert reloaded_take.changed_from == 1
    assert reloaded_take.changed_ids == ["color_grade"]
    assert reloaded_take.phases["brief"].status == "done"
    assert reloaded_take.phases["brief"].duration_ms == 1200


def test_reread_after_restart_reflects_a_simulated_kill_mid_run(tmp_path):
    """Ties the write-through hook and the importer together: persist
    through a partial run (as `test_killing_mid_run_keeps_the_completed_
    phases_on_disk` does), then read the folder back exactly as
    `avg.api.app.create_app` would on the next launch - completed phases
    must still be there, and nothing past the "kill" point should exist."""
    project = _project(tmp_path)
    phases = _phases(2)

    gen = run(project, channel=_channel(), channel_values=_CHANNEL_VALUES, phases=phases, on_phase_change=lambda: save_project(project))
    # Same "kill right after phase0's write-through" simulation as
    # test_killing_mid_run_keeps_the_completed_phases_on_disk above - see
    # that test's comment for why this is driven by event type rather than
    # a fixed pull count.
    from avg.core.events import PhaseFinished as _PhaseFinished

    for event in gen:
        if isinstance(event, _PhaseFinished):
            break

    reloaded = import_project(project.folder)
    assert reloaded.phases["phase0"].status == "done"
    assert "phase1" not in reloaded.phases


def test_native_project_json_round_trips_without_a_takes_key(tmp_path):
    """A project saved before Slice 5's takes existed - `takes` defaults to
    `[]` - must still import cleanly through the SAME native path (not the
    legacy meta-derivation path), preserving its real phase timing."""
    project = _project(tmp_path)
    project.phases["brief"] = PhaseRecord(name="brief", status="done", duration_ms=500)
    save_project(project)

    raw = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    assert "takes" not in raw or raw["takes"] == []  # sanity: no take ever added

    reloaded = import_project(project.folder)
    assert reloaded.takes == []
    assert reloaded.current_take_id is None
    assert reloaded.phases["brief"].duration_ms == 500
    assert reloaded.title == "Test Topic"


def test_folder_is_overridden_to_the_actual_argument_on_native_reload(tmp_path):
    """If the project directory was moved (or the whole output root
    relocated) since the document was last saved, the folder the caller
    actually handed in must win over whatever stale path was recorded."""
    project = _project(tmp_path)
    save_project(project)

    moved = tmp_path / "moved_elsewhere"
    project.folder.rename(moved)

    reloaded = import_project(moved)
    assert reloaded.folder == moved


# ── Legacy project.json (no takes key) still imports as before ─────────────


def test_a_legacy_project_with_no_takes_key_still_imports():
    doc = import_project(FIXTURE_LEGACY)
    assert doc.takes == []
    assert doc.current_take_id is None
    assert doc.title == "Test topic"
    assert doc.phases["brief"].status == "done"
