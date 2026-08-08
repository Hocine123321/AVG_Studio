"""Tests for the model-cache surface (`GET /api/models/cache`,
`POST /api/models/cache/build`, "Test Providers").

The build route calls real providers, so every test here replaces
`avg.api.routes_settings.probe.probe_models` and `.check_reachable` - the
suite must never spend money, and a real sweep is a completion per ticked
text model plus a catalogue fetch per image/voice provider with a tick.
"""

from __future__ import annotations

import threading

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


def test_the_cache_route_requires_a_token(client):
    assert client.get("/api/models/cache").status_code == 401


def test_cache_reports_empty_before_any_build(client):
    body = client.get("/api/models/cache", headers=AUTH).json()
    # Never a guessed catalogue - "no sweep has run" has to be visible as
    # itself so the UI can say "build the cache".
    assert body["updated"] is None
    assert body["providers"] == {}
    assert body["building"] is False


def test_a_build_stores_only_what_worked(client, monkeypatch):
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models",
        lambda *a, **k: {"groq": {"good": True, "dead": False}, "gemini": {}},
    )
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)
    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["providers"] == {"groq": ["good"], "gemini": []}
    assert body["updated"] is not None


# ── ActivityEvent (Slice 17 - the status pill's own feed) ─────────────────


def test_a_build_with_a_failed_model_reports_a_sticky_activity_event(client, monkeypatch):
    """Spec §3: a provider probe where NOT everything passed is sticky."""
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models",
        lambda *a, **k: {"groq": {"good": True, "dead": False}},
    )
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)

    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "hello"
        client.post("/api/models/cache/build", headers=AUTH)
        client.app.state.probe_thread.join(timeout=5)

        running = ws.receive_json()
        assert running["type"] == "activity"
        assert running["activity_id"] == "provider-probe"
        assert running["state"] == "running"
        assert running["sticky"] is False

        terminal = ws.receive_json()
        assert terminal["state"] == "done"
        assert terminal["sticky"] is True


def test_a_build_where_everything_passed_reports_a_not_sticky_activity_event(client, monkeypatch):
    """Spec §3: "a provider probe where everything passed" is explicitly
    not sticky."""
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models",
        lambda *a, **k: {"groq": {"good": True}},
    )
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)

    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.receive_json()  # hello
        client.post("/api/models/cache/build", headers=AUTH)
        client.app.state.probe_thread.join(timeout=5)

        ws.receive_json()  # running
        terminal = ws.receive_json()
        assert terminal["state"] == "done"
        assert terminal["sticky"] is False


def test_build_is_refused_while_one_is_running(client, monkeypatch):
    """Probing every model of every keyed provider takes 30-90 seconds in
    the legacy app. Two at once would double the spend for nothing."""
    started = threading.Event()
    release = threading.Event()

    def slow_probe(*_a, **_k):
        started.set()
        release.wait(timeout=5)
        return {}

    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", slow_probe)
    try:
        assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
        assert started.wait(timeout=5)
        assert client.get("/api/models/cache", headers=AUTH).json()["building"] is True
        assert client.post("/api/models/cache/build", headers=AUTH).status_code == 409
    finally:
        release.set()
        client.app.state.probe_thread.join(timeout=5)


def test_a_failed_sweep_leaves_the_previous_cache_alone(client, monkeypatch):
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models", lambda *a, **k: {"groq": {"good": True}}
    )
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)
    client.post("/api/models/cache/build", headers=AUTH)
    client.app.state.probe_thread.join(timeout=5)

    def boom(*_a, **_k):
        raise RuntimeError("every provider unreachable")

    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", boom)
    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    # Keeping the last known-good answer beats replacing it with nothing.
    assert client.get("/api/models/cache", headers=AUTH).json()["providers"] == {
        "groq": ["good"]
    }


def _tick(client, kind: str, entry_id: str) -> None:
    assert client.patch(
        f"/api/live-list/{kind}/{entry_id}", json={"selected": True}, headers=AUTH
    ).status_code == 200


def test_a_sweep_probes_the_ticked_text_models_and_nothing_else(client, monkeypatch):
    """A sweep that tests everything a provider advertises is what the Live
    List exists to replace."""
    asked: dict[str, list[str]] = {}

    def fake_probe_models(selection, **_k):
        asked.update(selection)
        return {p: {m: m != "dead" for m in models} for p, models in selection.items()}

    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", fake_probe_models)
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)

    _tick(client, "text", "groq/good")
    _tick(client, "text", "groq/dead")

    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    assert asked == {"groq": ["dead", "good"]}
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["text"] == {"groq": {"good": True, "dead": False}}


def test_image_and_voice_providers_are_only_reached_never_generated(client, monkeypatch):
    """An image probe is a paid image; an ElevenLabs probe is paid
    characters. Only the free catalogue fetch may run."""
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.probe_models",
        lambda selection, **k: {p: {} for p in selection},
    )
    reached: list[tuple[str, str]] = []

    def fake_reachable(kind, provider, **_k):
        reached.append((kind, provider))
        return True

    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", fake_reachable)

    _tick(client, "image", "pollinations/flux")
    _tick(client, "voice", "edge/en-GB-RyanNeural")

    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    assert sorted(reached) == [("image", "pollinations"), ("voice", "edge")]
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["reachable"] == {"image/pollinations": True, "voice/edge": True}


def test_an_untouched_provider_reports_untested_not_unreachable(client, monkeypatch):
    """Untested is the ABSENCE of a stored verdict. A provider nobody
    shortlisted has not been proved unreachable."""
    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", lambda s, **k: {})
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.check_reachable",
        lambda *a, **k: pytest.fail("a provider with no ticked entry is not worth a call"),
    )

    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["reachable"] == {}
    assert body["text"] == {}


def test_a_sweep_keeps_the_catalogue_entries_the_live_list_shows(client, monkeypatch):
    """The Live List's image, voice and text rows come from the catalogue
    entries in this same document. A sweep that replaced the whole document
    would empty the screen the ticks came from."""
    monkeypatch.setattr(
        "avg.api.routes_settings.catalogues.image_providers", lambda: ["pollinations"]
    )
    monkeypatch.setattr(
        "avg.api.routes_settings.catalogues.list_image_models", lambda p, **k: ["flux"]
    )
    monkeypatch.setattr("avg.api.routes_settings.catalogues.voice_providers", lambda: [])
    monkeypatch.setattr("avg.api.routes_settings.probe.known_providers", lambda: [])
    assert client.post("/api/live-list/refresh", headers=AUTH).status_code == 200
    assert [r["id"] for r in client.get("/api/live-list", headers=AUTH).json()["image"]] == [
        "pollinations/flux"
    ]

    monkeypatch.setattr("avg.api.routes_settings.probe.probe_models", lambda s, **k: {})
    monkeypatch.setattr("avg.api.routes_settings.probe.check_reachable", lambda *a, **k: None)
    client.post("/api/models/cache/build", headers=AUTH)
    client.app.state.probe_thread.join(timeout=5)

    assert [r["id"] for r in client.get("/api/live-list", headers=AUTH).json()["image"]] == [
        "pollinations/flux"
    ]


def test_refresh_lists_advertised_text_models_so_they_can_be_ticked(client, monkeypatch):
    """Listing a text catalogue is a plain GET and costs nothing - the same
    reason images and voices are listed here. Without it a fresh install has
    no text model to tick, and a ticked-only probe would have nothing to
    prove."""
    monkeypatch.setattr("avg.api.routes_settings.probe.known_providers", lambda: ["groq"])
    monkeypatch.setattr(
        "avg.api.routes_settings.probe.list_models", lambda p, t: ["llama-3.1-8b"]
    )
    monkeypatch.setattr("avg.api.routes_settings.catalogues.image_providers", lambda: [])
    monkeypatch.setattr("avg.api.routes_settings.catalogues.voice_providers", lambda: [])

    assert client.post("/api/live-list/refresh", headers=AUTH).status_code == 200

    rows = client.get("/api/live-list", headers=AUTH).json()["text"]
    assert [(r["id"], r["source"]) for r in rows] == [("groq/llama-3.1-8b", "catalogue")]


# ── Reachable is not usable ─────────────────────────────────────────────────

def test_a_keyless_provider_is_reported_as_needing_a_key(client, monkeypatch):
    """Stable Horde's catalogue is public, so it reads reachable while a
    real generation 403s. The card must be able to say so."""
    monkeypatch.setattr(
        "avg.api.routes_settings.secrets_config.get_key",
        lambda provider, path=None: None,
    )
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["needs_key"]["image/stablehorde"] is True


def test_a_keyed_provider_is_not_flagged(client, monkeypatch):
    monkeypatch.setattr(
        "avg.api.routes_settings.secrets_config.get_key",
        lambda provider, path=None: "a-key",
    )
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["needs_key"].get("image/stablehorde") is not True


def test_no_key_value_ever_appears_in_the_response(client, monkeypatch):
    monkeypatch.setattr(
        "avg.api.routes_settings.secrets_config.get_key",
        lambda provider, path=None: "sk-secret-value",
    )
    assert "sk-secret-value" not in client.get("/api/models/cache", headers=AUTH).text


def test_needs_key_honours_the_injected_secrets_path(client, monkeypatch, paths):
    """`GET /api/models/cache` must read the same secrets store `/api/keys`
    reads - the one `create_app(secrets_path=...)` was given, not the real
    `%APPDATA%` store. This is the regression the mismatched `get_key` call
    (missing the `secrets_path` argument every other secrets call in this
    file passes) produced: App Health said a keyed provider had no key.
    """
    from avg.config import secrets as secrets_config

    secrets_config.set_key("stablehorde", "a-key", paths["secrets_path"])
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["needs_key"].get("image/stablehorde") is not True


def test_every_keyed_image_provider_choice_is_in_needs_key():
    """`_NEEDS_KEY` is a fourth hand-kept list of providers alongside
    `image_provider`'s choices, `visuals.py`'s `_PROVIDERS`, and the `keys`
    settings group - nothing ties them together automatically. Adding a new
    keyed image provider to the schema without also adding it here would
    silently reopen the trap this list exists to close: a provider the Keys
    screen can store a key for, that App Health would never say needs one.
    Pollinations is the sole free image provider, so it is the only
    `image_provider` choice this test expects to be absent.
    """
    from avg.api.routes_settings import _NEEDS_KEY
    from avg.core.settings_schema import SETTINGS

    image_provider_spec = next(spec for spec in SETTINGS if spec.id == "image_provider")
    keyed_entries = {entry.split("/", 1)[1] for entry in _NEEDS_KEY if entry.startswith("image/")}

    for provider in image_provider_spec.choices:
        if provider in ("", "pollinations"):
            continue
        assert provider in keyed_entries, f"{provider} is missing from _NEEDS_KEY"
