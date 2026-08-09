"""tests/config/test_quick_settings.py"""

from __future__ import annotations

from avg.config.quick_settings import STARTER_PRESET, load_quick_settings, save_quick_settings


def test_a_missing_document_reads_as_the_starter_preset():
    """The one deliberate difference from channel_defaults.py's `{}` -
    spec §3.2: "Ships with a starter preset.\""""
    import pathlib
    assert load_quick_settings(pathlib.Path("/nonexistent/nope.json")) == STARTER_PRESET


def test_a_corrupt_document_reads_as_the_starter_preset(tmp_path):
    path = tmp_path / "quick_settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_quick_settings(path) == STARTER_PRESET


def test_a_malformed_ids_field_reads_as_the_starter_preset(tmp_path):
    path = tmp_path / "quick_settings.json"
    path.write_text('{"ids": "not a list"}', encoding="utf-8")
    assert load_quick_settings(path) == STARTER_PRESET


def test_saving_then_loading_round_trips(tmp_path):
    path = tmp_path / "quick_settings.json"
    save_quick_settings(["video_form", "image_provider"], path)
    assert load_quick_settings(path) == ["video_form", "image_provider"]


def test_saving_replaces_wholesale(tmp_path):
    path = tmp_path / "quick_settings.json"
    save_quick_settings(["a", "b"], path)
    save_quick_settings(["a"], path)
    assert load_quick_settings(path) == ["a"]


def test_saving_an_empty_list_is_allowed_and_round_trips(tmp_path):
    """The user removing everything is a valid state, not an error -
    it reads back as [], not as the starter preset re-appearing, which
    would make "remove the last one" impossible to actually do."""
    path = tmp_path / "quick_settings.json"
    save_quick_settings([], path)
    assert load_quick_settings(path) == []
