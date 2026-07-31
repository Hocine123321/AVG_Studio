"""The app-wide defaults document a channel can read INSTEAD of its own.

Storage only. The either/or resolution lives in `avg/api/routes_settings.py`
and is tested there.
"""

from __future__ import annotations

from avg.config.channel_defaults import load_defaults, save_defaults


def test_a_missing_document_is_empty_not_an_error(tmp_path):
    """Same tolerance every other document in this app is read with: a
    missing or corrupt file means "nothing set yet", never an exception."""
    assert load_defaults(tmp_path / "nope.json") == {}


def test_a_corrupt_document_is_empty_not_an_error(tmp_path):
    path = tmp_path / "channel_defaults.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_defaults(path) == {}


def test_saving_then_loading_round_trips(tmp_path):
    path = tmp_path / "channel_defaults.json"
    save_defaults({"image_provider": "stablehorde", "length_preset": "60s"}, path)
    assert load_defaults(path) == {"image_provider": "stablehorde", "length_preset": "60s"}


def test_saving_replaces_wholesale(tmp_path):
    """A snapshot, not a merge. A field dropped from the new set is gone -
    a merge here would be the per-field inheritance this whole design
    exists to avoid."""
    path = tmp_path / "channel_defaults.json"
    save_defaults({"a": 1, "b": 2}, path)
    save_defaults({"a": 9}, path)
    assert load_defaults(path) == {"a": 9}
