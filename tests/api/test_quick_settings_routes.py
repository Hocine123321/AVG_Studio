"""tests/api/test_quick_settings_routes.py"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app

TOKEN = "t0k"
AUTH = {"X-AVG-Token": TOKEN}


@pytest.fixture
def paths(tmp_path):
    return {
        "settings_path": tmp_path / "settings.json",
        "channels_dir": tmp_path / "channels",
        "secrets_path": tmp_path / "secrets.dat",
        "output_root": tmp_path / "output",
        "thumb_cache_dir": tmp_path / "thumbs",
        "quick_settings_path": tmp_path / "quick_settings.json",
    }


@pytest.fixture
def client(paths):
    return TestClient(create_app(token=TOKEN, **paths))


def test_a_fresh_install_returns_the_starter_preset(client):
    from avg.config.quick_settings import STARTER_PRESET

    r = client.get("/api/quick-settings", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["ids"] == STARTER_PRESET


def test_put_replaces_wholesale_and_round_trips(client):
    r = client.put("/api/quick-settings", headers=AUTH, json={"ids": ["video_form", "image_provider"]})
    assert r.status_code == 200
    assert r.json()["ids"] == ["video_form", "image_provider"]
    assert client.get("/api/quick-settings", headers=AUTH).json()["ids"] == ["video_form", "image_provider"]


def test_put_rejects_an_id_the_catalogue_does_not_have(client):
    r = client.put("/api/quick-settings", headers=AUTH, json={"ids": ["not_a_real_setting"]})
    assert r.status_code == 400
    # Nothing written - a rejected PUT must not partially land.
    assert client.get("/api/quick-settings", headers=AUTH).json()["ids"] != ["not_a_real_setting"]


def test_get_silently_drops_an_id_no_longer_in_the_catalogue(client, tmp_path):
    from avg.config.quick_settings import save_quick_settings

    save_quick_settings(["video_form", "a_setting_that_was_deleted"], tmp_path / "quick_settings.json")
    r = client.get("/api/quick-settings", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["ids"] == ["video_form"]


def test_get_never_writes_back_the_filtered_result(client, tmp_path):
    """"Dropped on read" - the stored document itself is untouched, so the
    id would reappear if the setting were ever restored to the catalogue."""
    from avg.config.quick_settings import load_quick_settings, save_quick_settings

    save_quick_settings(["video_form", "a_setting_that_was_deleted"], tmp_path / "quick_settings.json")
    client.get("/api/quick-settings", headers=AUTH)
    assert load_quick_settings(tmp_path / "quick_settings.json") == ["video_form", "a_setting_that_was_deleted"]
