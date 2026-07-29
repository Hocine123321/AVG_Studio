"""Tests for artifact resolution.

Two properties are load-bearing. A key outside the table must never reach
the filesystem - this is the surface that serves files from a user's disk
to a browser. And the table must not drift from `takes.PHASE_ARTIFACTS`,
because that is what `clone_take` copies: an artifact the panel shows but
the clone does not copy is a take that silently loses a file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from avg.core.artifacts import ARTIFACTS, available, resolve, spec_for
from avg.core.models import PhaseRecord, ProjectDoc, Take
from avg.core.takes import PHASE_ARTIFACTS


def _project(folder, *, extra_takes: list[Take] | None = None) -> ProjectDoc:
    now = datetime.now(timezone.utc)
    return ProjectDoc(
        id="p",
        title="P",
        folder=folder,
        created=now,
        phases={"brief": PhaseRecord(name="brief", status="done")},
        takes=[Take(id=1, label="Take 1", created=now, phases={}, storage="root"), *(extra_takes or [])],
        current_take_id=1,
    )


def test_an_unknown_key_never_touches_the_filesystem(tmp_path):
    project = _project(tmp_path)
    assert resolve(project, 1, "../../etc/passwd") is None
    # A path is not a key: the key space is a table, so there is nothing
    # to traverse.
    assert resolve(project, 1, "script/brief.json") is None
    assert resolve(project, 1, "nonsense") is None


def test_a_known_key_resolves_under_the_project_root_for_a_root_take(tmp_path):
    project = _project(tmp_path)
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "brief.json").write_text("{}", encoding="utf-8")

    path = resolve(project, 1, "brief")
    assert path is not None and path.is_file()


def test_a_take_that_owns_a_folder_reads_its_own_copy(tmp_path):
    now = datetime.now(timezone.utc)
    own = Take(id=2, label="Take 2", created=now, phases={})
    project = _project(tmp_path, extra_takes=[own])

    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "brief.json").write_text('{"root": true}', encoding="utf-8")
    take_dir = tmp_path / "takes" / "2" / "script"
    take_dir.mkdir(parents=True)
    (take_dir / "brief.json").write_text('{"take": true}', encoding="utf-8")

    path = resolve(project, 2, "brief")
    assert path is not None and "take" in path.read_text(encoding="utf-8")


def test_an_unknown_take_resolves_to_nothing(tmp_path):
    project = _project(tmp_path)
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "brief.json").write_text("{}", encoding="utf-8")
    assert resolve(project, 99, "brief") is None


def test_available_reports_only_what_is_on_disk(tmp_path):
    project = _project(tmp_path)
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "brief.json").write_text("{}", encoding="utf-8")

    found = available(project, 1)
    assert "brief" in found
    assert "narration" not in found


def test_a_directory_artifact_reports_a_file_count(tmp_path):
    project = _project(tmp_path)
    images = tmp_path / "images"
    images.mkdir()
    for i in range(3):
        (images / f"scene_{i}.png").write_bytes(b"x")

    assert available(project, 1)["images"] == 3


def test_spec_for_exposes_the_media_type(tmp_path):
    spec = spec_for("raw_voice")
    assert spec is not None
    assert spec.media_type == "audio/mpeg"
    assert spec_for("nonsense") is None


def test_every_artifact_path_is_one_clone_take_copies():
    """Drift here is a take that loses a file on clone.

    `clone_take` copies `PHASE_ARTIFACTS`; this table is what the UI
    offers. An entry here with no counterpart there would show a file in
    take 1 that vanishes in take 2.
    """
    for phase, specs in ARTIFACTS.items():
        copied = set(PHASE_ARTIFACTS.get(phase, ()))
        for spec in specs:
            assert spec.rel_path in copied or spec.rel_path.split("/")[0] in copied, (
                f"{phase}/{spec.key} ({spec.rel_path}) is not copied by clone_take"
            )
