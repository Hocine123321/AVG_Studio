from pathlib import Path
import pytest
from avg.config.legacy_import import import_project, import_all

FIX = Path(__file__).parent.parent / "fixtures" / "legacy_project"

COMPLETE = Path(__file__).parent.parent / "fixtures" / "legacy_complete"

def test_imports_title_and_derives_aspect_from_form():
    doc = import_project(FIX)
    assert doc.title == "Test topic"
    assert doc.aspect_ratio == "9:16"        # meta.form == "shortform"
    assert doc.short_edge == 1080            # never recorded per project
    assert doc.schema_version == 1

def test_longform_maps_to_widescreen():
    assert import_project(COMPLETE).aspect_ratio == "16:9"

def test_marks_reached_phases_done():
    doc = import_project(FIX)
    assert doc.phases["brief"].status == "done"
    assert doc.phases["render"].status == "pending"

def test_complete_marks_every_phase_done():
    doc = import_project(COMPLETE)
    assert {p.status for p in doc.phases.values()} == {"done"}

def test_scratch_folders_are_skipped(tmp_path):
    (tmp_path / "_filters_preview").mkdir()
    report = import_all(tmp_path)
    assert report.imported == [] and report.failed == []   # skipped, not failed

def test_missing_project_json_is_reported_not_raised(tmp_path):
    (tmp_path / "empty_project").mkdir()
    report = import_all(tmp_path)
    assert report.imported == []
    assert len(report.failed) == 1

def test_import_never_writes_to_the_legacy_folder():
    before = {p: p.stat().st_mtime for p in FIX.rglob("*") if p.is_file()}
    import_project(FIX)
    after = {p: p.stat().st_mtime for p in FIX.rglob("*") if p.is_file()}
    assert before == after
