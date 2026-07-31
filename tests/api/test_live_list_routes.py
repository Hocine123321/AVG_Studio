"""Tests for the Live List surface.

The refresh route talks to providers, so every test here replaces
`catalogues`. Nothing in this file may generate an image or synthesise
speech - that is the whole reason images and voices are LISTED rather than
probed.
"""

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
        "errors_path": tmp_path / "errors.json",
        "model_cache_path": tmp_path / "working_models.json",
        "model_prefs_path": tmp_path / "model_prefs.json",
    }


@pytest.fixture
def client(paths):
    return TestClient(create_app(token=TOKEN, **paths))


def _activate_channel(client: TestClient, label: str = "Shorts") -> str:
    created = client.post("/api/channels", headers=AUTH, json={"label": label}).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
    return created["id"]


def test_every_route_requires_a_token(client):
    assert client.get("/api/live-list").status_code == 401
    assert client.post("/api/live-list/refresh").status_code == 401
    assert client.patch("/api/live-list/text/a%2Fb", json={}).status_code == 401


def test_an_empty_install_lists_nothing_rather_than_guessing(client):
    body = client.get("/api/live-list", headers=AUTH).json()
    assert body == {"scope": "global", "text": [], "image": [], "voice": [], "voice_meta": {}}


def test_probed_text_models_appear_with_their_source(client):
    client.app.state.model_cache.save({"groq": {"llama-3.3": True, "dead": False}})
    rows = client.get("/api/live-list", headers=AUTH).json()["text"]
    assert [(r["id"], r["source"]) for r in rows] == [("groq/llama-3.3", "probed")]


def test_a_note_and_a_tick_round_trip(client):
    client.app.state.model_cache.save({"groq": {"llama-3.3": True}})
    patched = client.patch(
        "/api/live-list/text/groq%2Fllama-3.3",
        headers=AUTH,
        json={"selected": True, "note": "best for briefs"},
    )
    assert patched.status_code == 200

    # A second patch touching one field must not erase the other.
    client.patch("/api/live-list/text/groq%2Fllama-3.3", headers=AUTH, json={"favourite": True})

    row = client.get("/api/live-list", headers=AUTH).json()["text"][0]
    assert (row["selected"], row["favourite"], row["note"]) == (True, True, "best for briefs")


def test_favourites_sort_first(client):
    client.app.state.model_cache.save({"groq": {"aaa": True, "zzz": True}})
    client.patch("/api/live-list/text/groq%2Fzzz", headers=AUTH, json={"favourite": True})
    rows = client.get("/api/live-list", headers=AUTH).json()["text"]
    assert [r["id"] for r in rows] == ["groq/zzz", "groq/aaa"]


def test_a_manual_entry_can_be_added_and_removed(client):
    added = client.post("/api/live-list/image", headers=AUTH, json={"entry_id": "openai/gpt-image-1"})
    assert added.status_code == 201

    rows = client.get("/api/live-list", headers=AUTH).json()["image"]
    assert [(r["id"], r["source"], r["selected"]) for r in rows] == [
        ("openai/gpt-image-1", "manual", True)
    ]

    removed = client.delete("/api/live-list/image/openai%2Fgpt-image-1", headers=AUTH)
    assert removed.status_code == 200
    assert client.get("/api/live-list", headers=AUTH).json()["image"] == []


def test_a_manual_entry_must_name_a_provider(client):
    r = client.post("/api/live-list/image", headers=AUTH, json={"entry_id": "gpt-image-1"})
    assert r.status_code == 400
    assert "provider/model" in r.json()["detail"]


def test_a_catalogued_entry_cannot_be_deleted(client):
    """It would reappear on the next refresh anyway - untick it instead,
    which is what the tick is for."""
    client.app.state.model_cache.save({"__image__pollinations": {"flux": True}})
    r = client.delete("/api/live-list/image/pollinations%2Fflux", headers=AUTH)
    assert r.status_code == 400
    assert "untick" in r.json()["detail"]


def test_an_unknown_kind_is_refused(client):
    assert client.post("/api/live-list/sounds", headers=AUTH, json={"entry_id": "a/b"}).status_code == 400
    assert client.patch("/api/live-list/sounds/a%2Fb", headers=AUTH, json={}).status_code == 400


def test_refresh_lists_and_never_generates(client, monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "avg.providers.catalogues.list_image_models",
        lambda provider, **_kw: calls.append(f"image:{provider}") or ["flux"],
    )
    monkeypatch.setattr(
        "avg.providers.catalogues.list_voices",
        lambda provider, **_kw: calls.append(f"voice:{provider}")
        or [{"voice_id": "en-US-AriaNeural", "name": "Aria", "locale": "en-US", "gender": "Female"}],
    )
    # Listing text catalogues is fine here - it costs nothing, same as the
    # image and voice lists above - but nothing may reach for a real
    # completion. That is Test Providers' job, on its own button.
    monkeypatch.setattr("avg.api.routes_settings.probe.known_providers", lambda: [])
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models",
        lambda *a, **k: pytest.fail("refresh must not probe"),
    )

    assert client.post("/api/live-list/refresh", headers=AUTH).json() == {"refreshed": True}

    body = client.get("/api/live-list", headers=AUTH).json()
    assert [r["id"] for r in body["image"]] == ["pollinations/flux", "stablehorde/flux"]
    assert any(r["id"] == "edge/en-US-AriaNeural" for r in body["voice"])
    assert body["voice_meta"]["edge/en-US-AriaNeural"]["locale"] == "en-US"
    assert calls


def test_refresh_keeps_the_text_probe_results(client, monkeypatch):
    """`save` replaces the whole cache document, so a voice refresh must
    not silently un-verify every text model."""
    client.app.state.model_cache.save({"groq": {"llama-3.3": True}})
    monkeypatch.setattr("avg.providers.catalogues.list_image_models", lambda *a, **k: [])
    monkeypatch.setattr("avg.providers.catalogues.list_voices", lambda *a, **k: [])
    monkeypatch.setattr("avg.api.routes_settings.probe.known_providers", lambda: [])

    client.post("/api/live-list/refresh", headers=AUTH)

    assert [r["id"] for r in client.get("/api/live-list", headers=AUTH).json()["text"]] == [
        "groq/llama-3.3"
    ]


def test_a_channel_on_its_own_scope_does_not_read_the_global_list(client):
    """No merge layer, exactly like every other channel-scoped setting: the
    channel uses its own document or nothing, never a blend."""
    client.post("/api/live-list/text", headers=AUTH, json={"entry_id": "groq/global-only"})
    _activate_channel(client)
    client.post("/api/settings/values", headers=AUTH, json={"values": {"live_list_source": "channel"}})

    body = client.get("/api/live-list", headers=AUTH).json()
    assert body["scope"] == "channel"
    assert all(r["id"] != "groq/global-only" for r in body["text"])

    # And what it saves stays its own.
    client.post("/api/live-list/text", headers=AUTH, json={"entry_id": "groq/channel-only"})
    client.post("/api/settings/values", headers=AUTH, json={"values": {"live_list_source": "global"}})
    global_rows = client.get("/api/live-list", headers=AUTH).json()["text"]
    assert [r["id"] for r in global_rows] == ["groq/global-only"]


# ── Slice 11 Task 7: the Live List states its age ───────────────────────────

def test_the_live_list_says_when_it_was_last_listed(client):
    """A stale catalogue that says nothing is why 33 Gemini models looked
    like 1."""
    body = client.get("/api/live-list", headers=AUTH).json()
    assert "updated" in body
    assert body["updated"] is None  # nothing has ever been listed here
