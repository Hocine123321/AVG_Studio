"""Tests for the working-models cache.

Ported from the legacy app's `working_models_cache.json`, whose shape is
{"updated": iso8601, "models": {provider: {model_id: bool}}}. The rule that
matters is the legacy one: only models a probe PROVED working are returned,
and an empty result stays empty rather than falling back to a hardcoded
catalogue - the old app's built-in list went stale and made it impossible
to tell what had actually been verified.
"""

from __future__ import annotations

from avg.core.model_cache import ModelCache


def test_only_proven_models_come_back(tmp_path):
    cache = ModelCache(tmp_path / "models.json")
    cache.save({"groq": {"llama-3.3-70b-versatile": True, "broken-model": False}})
    assert cache.working("groq") == ["llama-3.3-70b-versatile"]


def test_an_unprobed_provider_is_empty_never_invented(tmp_path):
    cache = ModelCache(tmp_path / "models.json")
    cache.save({"groq": {"a": True}})
    assert cache.working("gemini") == []


def test_a_missing_cache_file_is_empty_not_an_error(tmp_path):
    assert ModelCache(tmp_path / "nothing.json").working("groq") == []


def test_a_corrupt_cache_file_is_empty_not_an_error(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{not json", encoding="utf-8")
    assert ModelCache(path).working("groq") == []
    assert ModelCache(path).updated_at() is None


def test_saving_stamps_the_time(tmp_path):
    cache = ModelCache(tmp_path / "models.json")
    assert cache.updated_at() is None
    cache.save({"groq": {"a": True}})
    assert cache.updated_at() is not None


def test_results_survive_a_reload(tmp_path):
    path = tmp_path / "models.json"
    ModelCache(path).save({"groq": {"a": True, "b": False}})
    assert ModelCache(path).working("groq") == ["a"]


def test_a_save_replaces_rather_than_merges(tmp_path):
    """A model that stopped working has to be able to disappear.

    A merge would keep a stale `true` alive forever, which is exactly the
    failure the legacy hardcoded catalogue had.
    """
    path = tmp_path / "models.json"
    cache = ModelCache(path)
    cache.save({"groq": {"a": True, "b": True}})
    cache.save({"groq": {"a": True}})
    assert cache.working("groq") == ["a"]
    assert cache.providers() == ["groq"]
