"""Tests for the Live List's own data.

The rule that matters: catalogue and probe results are machine-owned and
get replaced wholesale on every refresh, so a user's ticks and notes must
live somewhere that refresh cannot touch.
"""

from __future__ import annotations

import threading
import time

from avg.core import model_prefs as model_prefs_module
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


def test_suggestions_live_beside_the_ticks_and_replace_wholesale(tmp_path):
    from avg.core.model_prefs import ModelPrefs

    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "groq/a", selected=True, note="mine")

    prefs.set_suggestions([
        {"entry_id": "groq/a", "kind": "text", "tier": "cheap", "reason": "fast"},
        {"entry_id": "groq/b", "kind": "text", "tier": "high", "reason": "better"},
    ])
    assert set(prefs.suggestions()) == {"groq/a", "groq/b"}

    # A second run replaces, never merges - a stale suggestion must be able
    # to disappear, exactly as a stale probe verdict can.
    prefs.set_suggestions([{"entry_id": "groq/c", "kind": "text", "tier": None, "reason": "x"}])
    assert set(prefs.suggestions()) == {"groq/c"}


def test_a_suggestion_run_never_disturbs_what_the_user_wrote(tmp_path):
    """The one thing in this document that cannot be regenerated."""
    from avg.core.model_prefs import ModelPrefs

    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "groq/a", selected=True, favourite=True, note="warm, good for narration")

    prefs.set_suggestions([{"entry_id": "groq/a", "kind": "text", "tier": "cheap", "reason": "fast"}])

    entry = prefs.all("text")["groq/a"]
    assert entry.selected is True
    assert entry.favourite is True
    assert entry.note == "warm, good for narration"


def test_suggestions_are_empty_before_any_run(tmp_path):
    from avg.core.model_prefs import ModelPrefs

    assert ModelPrefs(tmp_path / "model_prefs.json").suggestions() == {}


def test_ticking_after_a_suggestion_run_does_not_erase_the_suggestions(tmp_path):
    """The symmetric case: `_save` (used by `set`) must preserve the
    `suggested` section the same way `set_suggestions` preserves `entries`."""
    from avg.core.model_prefs import ModelPrefs

    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set_suggestions([{"entry_id": "groq/a", "kind": "text", "tier": "cheap", "reason": "fast"}])
    prefs.set("text", "groq/a", selected=True)

    assert set(prefs.suggestions()) == {"groq/a"}


def test_suggesting_one_kind_leaves_another_kinds_suggestions_intact(tmp_path):
    """Important 4 of the slice review: "Suggest voices" must not erase
    what "Suggest models" already found, and vice versa - the two runs
    never overlap in which kinds they cover, so neither should be able to
    delete the other's rows."""
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set_suggestions(
        [{"entry_id": "groq/a", "kind": "text", "tier": "cheap", "reason": "fast"}],
        kinds={"text", "image"},
    )
    prefs.set_suggestions(
        [{"entry_id": "edge/x", "kind": "voice", "reason": "warm"}],
        kinds={"voice"},
    )

    assert set(prefs.suggestions()) == {"groq/a", "edge/x"}

    # A second "Suggest voices" run still only touches "voice" rows.
    prefs.set_suggestions(
        [{"entry_id": "edge/y", "kind": "voice", "reason": "clear"}],
        kinds={"voice"},
    )
    assert set(prefs.suggestions()) == {"groq/a", "edge/y"}


def test_a_tick_during_a_suggest_run_is_not_lost(tmp_path, monkeypatch):
    """Reproduces the race Important 3 fixes. Two different `ModelPrefs`
    instances point at the same file - exactly the shape `_prefs_for_scope`
    produces for a worker thread and a PATCH request. Without a lock keyed
    by path, the worker's read (taken before the tick's write) would still
    be the document its own write is based on, and the write would revert
    the tick. With the lock, the tick's `_save` simply waits its turn and
    neither write is lost.
    """
    path = tmp_path / "model_prefs.json"
    worker_prefs = ModelPrefs(path)
    ui_prefs = ModelPrefs(path)

    worker_reading = threading.Event()
    release_worker = threading.Event()
    original_load_document = model_prefs_module.ModelPrefs._load_document

    def slow_load_document(self):
        data = original_load_document(self)
        if self is worker_prefs:
            worker_reading.set()
            release_worker.wait(timeout=2)
        return data

    monkeypatch.setattr(model_prefs_module.ModelPrefs, "_load_document", slow_load_document)

    worker_thread = threading.Thread(
        target=lambda: worker_prefs.set_suggestions(
            [{"entry_id": "groq/a", "kind": "text", "tier": "cheap", "reason": "fast"}],
            kinds={"text", "image"},
        )
    )
    ui_thread = threading.Thread(target=lambda: ui_prefs.set("text", "groq/a", selected=True))

    worker_thread.start()
    assert worker_reading.wait(timeout=2), "worker never reached its read"
    ui_thread.start()
    # Give the UI thread time to reach (and block on) the same lock before
    # the worker is allowed to finish - proving the two cannot interleave.
    time.sleep(0.05)
    release_worker.set()

    worker_thread.join(timeout=2)
    ui_thread.join(timeout=2)
    assert not worker_thread.is_alive()
    assert not ui_thread.is_alive()

    fresh = ModelPrefs(path)
    entry = fresh.all("text").get("groq/a")
    assert entry is not None
    assert entry.selected is True
    assert set(fresh.suggestions()) == {"groq/a"}


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
