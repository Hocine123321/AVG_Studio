"""Tests for the model-cache surface (`GET /api/models/cache`,
`POST /api/models/cache/build`).

The build route probes real providers, so every test here replaces
`probe_all` - the suite must never spend money, and a sweep is one call per
model per keyed provider.
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
        "avg.api.routes_settings.probe_all",
        lambda *a, **k: {"groq": {"good": True, "dead": False}, "gemini": {}},
    )
    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["providers"] == {"groq": ["good"], "gemini": []}
    assert body["updated"] is not None


def test_build_is_refused_while_one_is_running(client, monkeypatch):
    """Probing every model of every keyed provider takes 30-90 seconds in
    the legacy app. Two at once would double the spend for nothing."""
    started = threading.Event()
    release = threading.Event()

    def slow_probe(*_a, **_k):
        started.set()
        release.wait(timeout=5)
        return {}

    monkeypatch.setattr("avg.api.routes_settings.probe_all", slow_probe)
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
        "avg.api.routes_settings.probe_all", lambda *a, **k: {"groq": {"good": True}}
    )
    client.post("/api/models/cache/build", headers=AUTH)
    client.app.state.probe_thread.join(timeout=5)

    def boom(*_a, **_k):
        raise RuntimeError("every provider unreachable")

    monkeypatch.setattr("avg.api.routes_settings.probe_all", boom)
    assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
    client.app.state.probe_thread.join(timeout=5)

    # Keeping the last known-good answer beats replacing it with nothing.
    assert client.get("/api/models/cache", headers=AUTH).json()["providers"] == {
        "groq": ["good"]
    }
