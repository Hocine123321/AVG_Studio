"""Tests for `avg.pipeline.create.new_project`."""

from __future__ import annotations

import json
from typing import Any

from avg.config.channels import Channel
from avg.pipeline.create import new_project
from avg.pipeline.registry import next_phase_id


def _channel(settings: dict[str, Any] | None = None) -> Channel:
    return Channel(id="ch", label="Ch", settings=settings or {})


def test_new_project_has_no_phases_recorded(tmp_path):
    project = new_project("How Rome Fell", _channel(), tmp_path)
    assert project.phases == {}


def test_new_project_resumes_at_phase_zero(tmp_path):
    """The concrete form of this slice's "new-video start phase is 0"
    invariant: nothing sets a start-phase field, `phases == {}` alone is
    what makes `next_phase_id` resolve to 0."""
    project = new_project("How Rome Fell", _channel(), tmp_path)
    assert next_phase_id(project) == 0


def test_new_project_creates_the_folder_and_its_input_subfolders(tmp_path):
    project = new_project("How Rome Fell", _channel(), tmp_path)
    assert project.folder.is_dir()
    for sub in ("script", "images", "audio", "render"):
        assert (project.folder / sub).is_dir()


def test_new_project_id_matches_its_folder_name(tmp_path):
    project = new_project("How Rome Fell", _channel(), tmp_path)
    assert project.id == project.folder.name


def test_new_project_writes_a_readable_project_json(tmp_path):
    project = new_project("How Rome Fell", _channel(), tmp_path)
    raw = json.loads((project.folder / "project.json").read_text(encoding="utf-8"))
    assert raw["title"] == "How Rome Fell"
    assert raw["id"] == project.id
    assert raw["phases"] == {}


def test_new_project_uses_the_channels_aspect_ratio_when_set(tmp_path):
    project = new_project("Topic", _channel({"aspect_ratio": "16:9"}), tmp_path)
    assert project.aspect_ratio == "16:9"


def test_new_project_falls_back_to_the_schema_default_aspect_ratio(tmp_path):
    project = new_project("Topic", _channel(), tmp_path)
    assert project.aspect_ratio == "9:16"


def test_two_projects_from_the_same_topic_get_distinct_folders(tmp_path):
    a = new_project("Same Topic", _channel(), tmp_path)
    b = new_project("Same Topic", _channel(), tmp_path)
    assert a.folder != b.folder
    assert a.id != b.id


def test_new_project_creates_output_root_if_missing(tmp_path):
    output_root = tmp_path / "not_yet_created"
    project = new_project("Topic", _channel(), output_root)
    assert project.folder.is_dir()


def test_a_new_project_has_take_1_from_the_start(tmp_path):
    """The take is not created lazily on first read.

    It used to be: `GET /takes` called `migrate_to_takes`, which COPIED
    `project.phases` into the take as a snapshot. The runner then went on
    mutating `project.phases` - a different dict - so a first run's rail
    froze at whatever was true when the screen was opened.
    """
    doc = new_project("A topic", _channel(), tmp_path)

    assert [t.id for t in doc.takes] == [1]
    assert doc.current_take_id == 1


def test_take_1_shares_the_phases_dict_rather_than_copying_it(tmp_path):
    """Same object, not an equal one. This is what makes the runner's
    in-place mutation visible through the take the UI reads."""
    from avg.core.models import PhaseRecord

    doc = new_project("A topic", _channel(), tmp_path)
    doc.phases["brief"] = PhaseRecord(name="brief", status="done", duration_ms=10)

    assert doc.takes[0].phases["brief"].status == "done"
