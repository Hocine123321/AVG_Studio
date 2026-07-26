"""Tests for the take model: `avg.core.takes`.

Exercises `clone_take`/`delete_take`/`migrate_to_takes`/`estimate_seconds`
against real folders under `tmp_path` - never against
`D:\\AI_Video_Gen\\output` (read-only, real user projects).
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from avg.core.models import PhaseRecord, ProjectDoc
from avg.core.takes import (
    PHASE_ARTIFACTS,
    Take,
    clone_take,
    delete_take,
    estimate_seconds,
    migrate_to_takes,
    take_folder,
)


def _done(name: str, ms: int = 1000) -> PhaseRecord:
    return PhaseRecord(name=name, status="done", duration_ms=ms)


def _project(tmp_path: Path, **kwargs) -> ProjectDoc:
    folder = tmp_path / "proj"
    folder.mkdir(exist_ok=True)
    defaults = dict(
        id="proj",
        title="A Topic",
        folder=folder,
        created=datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return ProjectDoc(**defaults)


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_take1_artifacts(project: ProjectDoc) -> Path:
    """Populate take 1's own folder with one real file per phase in
    `PHASE_ARTIFACTS`, plus a fake `master_output.mp4` - the fixture every
    clone_take test starts from."""
    t1 = take_folder(project, 1)
    for phase_key, rel_paths in PHASE_ARTIFACTS.items():
        for rel in rel_paths:
            target = t1 / rel
            if phase_key == "visuals":
                _write(target / "001.png", "fake-png-bytes")
                _write(target / "manifest.json", "{}")
            else:
                _write(target, f"content for {phase_key}")
    _write(t1 / "master_output.mp4", "fake-mp4-bytes")
    return t1


def _take1(project: ProjectDoc, phases: dict[str, PhaseRecord] | None = None) -> Take:
    take = Take(
        id=1,
        label="Take 1",
        created=project.created,
        phases=phases if phases is not None else {key: _done(key) for key in PHASE_ARTIFACTS},
        master_output=take_folder(project, 1) / "master_output.mp4",
    )
    project.takes = [take]
    project.current_take_id = 1
    return take


# ── clone_take: copies only kept artifacts ───────────────────────────────


def test_clone_take_copies_only_kept_phase_artifacts(tmp_path):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)

    new_take = clone_take(project, source_take_id=1, keep_phase_keys=["brief", "script"])

    t2 = take_folder(project, new_take.id)
    assert (t2 / "script" / "brief.json").exists()
    assert (t2 / "script" / "narration.txt").exists()
    assert (t2 / "script" / "scenes.json").exists()
    # Not kept - must not have been copied.
    assert not (t2 / "script" / "video_map.json").exists()
    assert not (t2 / "images").exists()
    assert not (t2 / "audio").exists()
    assert not (t2 / "render").exists()


def test_clone_take_keeping_everything_still_excludes_render(tmp_path):
    """Even if every phase key that HAS an artifact map entry is kept,
    master_output is never copied - "render" has no PHASE_ARTIFACTS entry
    at all, and clone_take hardcodes master_output=None regardless."""
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)

    new_take = clone_take(project, source_take_id=1, keep_phase_keys=list(PHASE_ARTIFACTS))

    t2 = take_folder(project, new_take.id)
    for phase_key, rel_paths in PHASE_ARTIFACTS.items():
        for rel in rel_paths:
            assert (t2 / rel).exists(), f"{phase_key}'s {rel} should have been copied"
    assert not (t2 / "master_output.mp4").exists()


# ── clone_take: no master_output on the new take ─────────────────────────


def test_cloned_take_has_no_master_output(tmp_path):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)

    new_take = clone_take(project, source_take_id=1, keep_phase_keys=["brief"])

    assert new_take.master_output is None


def test_cloned_take_kept_phases_carry_over_phase_records(tmp_path):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)

    new_take = clone_take(project, source_take_id=1, keep_phase_keys=["brief", "script"])

    assert set(new_take.phases) == {"brief", "script"}
    assert new_take.phases["brief"].status == "done"
    assert new_take.changed_from == 1


# ── clone_take: mid-copy failure leaves no trace ─────────────────────────


def test_clone_take_mid_copy_failure_leaves_no_folder_and_no_registration(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)

    original_copy2 = shutil.copy2
    call_count = {"n": 0}

    def _flaky_copy2(src, dst, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("disk full (simulated)")
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", _flaky_copy2)

    takes_before = list(project.takes)
    with pytest.raises(OSError):
        clone_take(project, source_take_id=1, keep_phase_keys=["brief", "script", "director1"])

    # No folder left behind for the take that would have been id 2.
    assert not take_folder(project, 2).exists()
    # project.takes is untouched - registration never happened.
    assert project.takes == takes_before
    assert len(project.takes) == 1


def test_clone_take_unknown_source_id_raises(tmp_path):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)

    with pytest.raises(ValueError):
        clone_take(project, source_take_id=99, keep_phase_keys=["brief"])


# ── delete_take ───────────────────────────────────────────────────────────


def test_delete_take_refuses_the_last_remaining_take(tmp_path):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)

    with pytest.raises(ValueError):
        delete_take(project, 1)
    assert len(project.takes) == 1


def test_delete_take_moves_current_take_id_when_current_is_deleted(tmp_path):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)
    t2 = clone_take(project, source_take_id=1, keep_phase_keys=["brief"])
    project.current_take_id = t2.id  # user pinned take 2

    delete_take(project, t2.id)

    assert project.current_take_id == 1
    assert [t.id for t in project.takes] == [1]
    assert not take_folder(project, t2.id).exists()


def test_delete_take_removes_folder_and_leaves_current_alone_when_not_current(tmp_path):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)
    t2 = clone_take(project, source_take_id=1, keep_phase_keys=["brief"])
    # current_take_id is still 1; deleting take 2 (not current) must not move it.

    delete_take(project, t2.id)

    assert project.current_take_id == 1
    assert not take_folder(project, t2.id).exists()


def test_delete_take_unknown_id_raises(tmp_path):
    project = _project(tmp_path)
    _seed_take1_artifacts(project)
    _take1(project)
    clone_take(project, source_take_id=1, keep_phase_keys=["brief"])  # so we're not deleting "the last one"

    with pytest.raises(ValueError):
        delete_take(project, 999)


# ── migrate_to_takes ──────────────────────────────────────────────────────


def test_migrate_to_takes_yields_exactly_one_current_take(tmp_path):
    folder = tmp_path / "legacy_proj"
    folder.mkdir()
    _write(folder / "script" / "brief.json", "{}")
    _write(folder / "images" / "001.png", "fake-png")
    _write(folder / "master_output.mp4", "fake-mp4-bytes")

    project = ProjectDoc(
        id="legacy_proj",
        title="Legacy Topic",
        folder=folder,
        created=datetime(2026, 1, 1, tzinfo=timezone.utc),
        phases={"brief": _done("brief"), "script": _done("script")},
        master_output=folder / "master_output.mp4",
    )

    migrate_to_takes(project)

    assert len(project.takes) == 1
    assert project.current_take_id == 1
    take = project.takes[0]
    assert take.id == 1
    assert take.phases == project.phases
    assert take.master_output == take_folder(project, 1) / "master_output.mp4"
    assert take.master_output.exists()
    assert (take_folder(project, 1) / "script" / "brief.json").exists()
    assert (take_folder(project, 1) / "images" / "001.png").exists()
    # Non-destructive: the legacy top-level files are left in place too.
    assert (folder / "master_output.mp4").exists()


def test_migrate_to_takes_is_idempotent(tmp_path):
    folder = tmp_path / "legacy_proj2"
    folder.mkdir()
    project = ProjectDoc(
        id="legacy_proj2",
        title="Legacy Topic",
        folder=folder,
        created=datetime(2026, 1, 1, tzinfo=timezone.utc),
        phases={"brief": _done("brief")},
    )

    migrate_to_takes(project)
    first_takes = project.takes
    migrate_to_takes(project)  # no-op the second time

    assert project.takes is first_takes


def test_migrate_to_takes_with_no_master_output(tmp_path):
    folder = tmp_path / "legacy_proj3"
    folder.mkdir()
    project = ProjectDoc(
        id="legacy_proj3",
        title="Legacy Topic",
        folder=folder,
        created=datetime(2026, 1, 1, tzinfo=timezone.utc),
        phases={},
    )

    migrate_to_takes(project)

    assert project.takes[0].master_output is None


# ── estimate_seconds ──────────────────────────────────────────────────────


def test_estimate_seconds_sums_most_recent_completed_run_per_phase(tmp_path):
    project = _project(tmp_path)
    project.takes = [
        Take(id=1, label="Take 1", created=project.created,
             phases={"script": _done("script", ms=2000), "voice": _done("voice", ms=3000)}),
        Take(id=2, label="Take 2", created=project.created,
             phases={"script": _done("script", ms=5000)}),  # more recent script run
    ]

    seconds, unknown = estimate_seconds(project, ["script", "voice"])

    # script: most recent completed is take 2's 5000ms; voice: only take 1's 3000ms.
    assert seconds == 8
    assert unknown == []


def test_estimate_seconds_names_phases_with_no_history_as_unknown(tmp_path):
    project = _project(tmp_path)
    project.takes = [
        Take(id=1, label="Take 1", created=project.created, phases={"script": _done("script", ms=1000)}),
    ]

    seconds, unknown = estimate_seconds(project, ["script", "voice", "render"])

    assert seconds == 1
    assert unknown == ["voice", "render"]


def test_estimate_seconds_returns_none_when_nothing_has_ever_run(tmp_path):
    project = _project(tmp_path)
    project.takes = [Take(id=1, label="Take 1", created=project.created, phases={})]

    seconds, unknown = estimate_seconds(project, ["script", "voice"])

    assert seconds is None
    assert unknown == ["script", "voice"]


def test_estimate_seconds_ignores_a_phase_that_only_ever_failed(tmp_path):
    project = _project(tmp_path)
    project.takes = [
        Take(id=1, label="Take 1", created=project.created,
             phases={"voice": PhaseRecord(name="voice", status="failed", duration_ms=500)}),
    ]

    seconds, unknown = estimate_seconds(project, ["voice"])

    assert seconds is None
    assert unknown == ["voice"]


def test_estimate_seconds_no_takes_at_all_returns_none(tmp_path):
    project = _project(tmp_path)
    assert project.takes == []

    seconds, unknown = estimate_seconds(project, ["script"])

    assert seconds is None
    assert unknown == ["script"]
