"""Tests for the Live List's own data.

The rule that matters: catalogue and probe results are machine-owned and
get replaced wholesale on every refresh, so a user's ticks and notes must
live somewhere that refresh cannot touch.
"""

from __future__ import annotations

from avg.core.model_prefs import ModelPrefs


def test_a_note_survives_a_reload(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "groq/llama-3.3-70b-versatile", selected=True, note="best for briefs")

    reopened = ModelPrefs(tmp_path / "model_prefs.json")
    entry = reopened.all("text")["groq/llama-3.3-70b-versatile"]
    assert entry.selected is True
    assert entry.note == "best for briefs"


def test_setting_one_field_leaves_the_others_alone(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("voice", "edge/en-US-AriaNeural", selected=True, note="narration default")
    prefs.set("voice", "edge/en-US-AriaNeural", favourite=True)

    entry = prefs.all("voice")["edge/en-US-AriaNeural"]
    assert (entry.selected, entry.favourite, entry.note) == (True, True, "narration default")


def test_kinds_do_not_collide(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "a/b", selected=True)
    assert prefs.all("image") == {}


def test_a_corrupt_file_is_empty_not_an_error(tmp_path):
    path = tmp_path / "model_prefs.json"
    path.write_text("{not json", encoding="utf-8")
    assert ModelPrefs(path).all("text") == {}


def test_a_missing_file_is_empty(tmp_path):
    assert ModelPrefs(tmp_path / "nothing.json").all("text") == {}


def test_remove_deletes_one_entry(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("image", "pollinations/flux", selected=True)
    prefs.set("image", "pollinations/kontext", selected=True)
    prefs.remove("image", "pollinations/flux")
    assert list(prefs.all("image")) == ["pollinations/kontext"]


def test_removing_something_that_is_not_there_is_not_an_error(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.remove("image", "nothing/here")
    assert prefs.all("image") == {}


def test_an_unknown_kind_is_rejected(tmp_path):
    """Three kinds exist. A typo must not silently create a fourth that
    nothing ever reads back."""
    import pytest

    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    with pytest.raises(ValueError):
        prefs.set("txt", "a/b", selected=True)


def test_selected_ids_are_sorted_favourites_first(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "groq/b", selected=True)
    prefs.set("text", "groq/a", selected=True, favourite=True)
    prefs.set("text", "groq/c", selected=False)

    # Favourites lead, then alphabetical. An unticked entry never appears:
    # the ticks are what the pickers offer.
    assert prefs.selected_ids("text") == ["groq/a", "groq/b"]
