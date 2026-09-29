"""Tests for the render workspace.

A render runs against a disposable copy of the project so it can never
overwrite the finished video the user already has. Two properties of that
copy are load-bearing and neither was true before Slice 8: it has to be
cheap, because the render sat silent for as long as the copy took, and it
has to be temporary, because it was created with `keep=True` and every
render ever run left a full copy of the project in %TEMP% permanently.
"""

from __future__ import annotations

import os

from avg.media.environment import render_workspace


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / "images").mkdir(parents=True)
    (project / "images" / "a.png").write_bytes(b"x" * 32)
    (project / "audio").mkdir(parents=True)
    (project / "audio" / "raw_voice.mp3").write_bytes(b"y" * 32)
    return project


def test_the_workspace_carries_the_inputs(tmp_path):
    project = _project(tmp_path)
    with render_workspace(project) as workspace:
        assert (workspace / "images" / "a.png").read_bytes() == b"x" * 32
        assert (workspace / "audio" / "raw_voice.mp3").is_file()


def test_the_workspace_is_removed_by_default(tmp_path):
    """Every render used to leave a full copy of the project in %TEMP%,
    forever - `render_workspace` was called with keep=True."""
    project = _project(tmp_path)
    with render_workspace(project) as workspace:
        held = workspace
    assert not held.exists()


def test_keep_is_still_available_for_debugging(tmp_path):
    project = _project(tmp_path)
    with render_workspace(project, keep=True) as workspace:
        held = workspace
    assert held.exists()
    # Not left behind by this test either.
    import shutil

    shutil.rmtree(held, ignore_errors=True)


def test_inputs_are_hardlinked_when_the_filesystem_allows_it(tmp_path):
    """A 'disposable copy' that copies gigabytes is why a render is silent
    for minutes before anything happens. A hardlink is the same bytes under
    a second name, which keeps the property the copy exists for - the
    workspace starts with no master_output.mp4 - at no copy cost."""
    project = _project(tmp_path)
    source = project / "images" / "a.png"

    with render_workspace(project) as workspace:
        linked = workspace / "images" / "a.png"
        same_inode = linked.stat().st_ino == source.stat().st_ino
        counted = linked.stat().st_nlink >= 2
        assert same_inode or counted


def test_master_output_is_never_carried_in(tmp_path):
    """A file appearing in the workspace has to be unambiguously THIS
    render's output - otherwise a failed render looks successful."""
    project = _project(tmp_path)
    (project / "master_output.mp4").write_bytes(b"old video")

    with render_workspace(project) as workspace:
        assert not (workspace / "master_output.mp4").exists()


def test_filters_dir_dev_checkout_points_at_bundled_copy():
    from avg.media import environment

    d = environment.filters_dir()
    assert (d / "Filters.metadata").is_file()
    assert (d / "film_grain.mov").is_file()


def test_ensure_filters_dir_env_sets_the_variable(monkeypatch):
    from avg.media import environment

    monkeypatch.delenv("AVG_FILTERS_DIR", raising=False)
    environment.ensure_filters_dir_env()
    assert os.environ["AVG_FILTERS_DIR"] == str(environment.filters_dir())


def test_ensure_filters_dir_env_never_clobbers_an_explicit_value(monkeypatch):
    from avg.media import environment

    monkeypatch.setenv("AVG_FILTERS_DIR", "/some/test/override")
    environment.ensure_filters_dir_env()
    assert os.environ["AVG_FILTERS_DIR"] == "/some/test/override"
