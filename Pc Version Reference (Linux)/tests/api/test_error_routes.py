"""Tests for the error surface (`GET/POST/DELETE /api/errors`).

The point of the store is that a failure the user did not witness is still
recoverable afterwards, so the test that matters most is the one asserting
an unhandled 500 lands in it. That is exactly the situation this slice
exists for: the packaged build returns 500 from a render and nothing in the
app remembers why.
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
    }


@pytest.fixture
def client(paths):
    # raise_server_exceptions=False so TestClient returns the 500 the real
    # server would, instead of re-raising into the test.
    return TestClient(create_app(token=TOKEN, **paths), raise_server_exceptions=False)


def test_errors_route_requires_a_token(client):
    assert client.get("/api/errors").status_code == 401


def test_an_unhandled_server_error_is_recorded(client, monkeypatch):
    """The whole reason this exists.

    Breaks a REAL route rather than registering a throwaway one: routes
    added after `create_app` land behind the static mount that serves
    `web/dist` at "/", so a test-only route would 404 instead of raising
    and would prove nothing about the middleware.
    """

    def _explode(*_args, **_kwargs):
        raise RuntimeError("deliberate explosion")

    monkeypatch.setattr("avg.api.routes_settings.health.check_all", _explode)

    assert client.get("/api/health", headers=AUTH).status_code == 500

    errors = client.get("/api/errors", headers=AUTH).json()["errors"]
    assert len(errors) == 1
    assert errors[0]["severity"] == "critical"
    assert errors[0]["source"] == "api"
    assert "deliberate explosion" in errors[0]["detail"]
    assert errors[0]["context"]["path"] == "/api/health"
    assert errors[0]["context"]["method"] == "GET"


def test_the_frontend_can_report_its_own_failures(client):
    posted = client.post(
        "/api/errors",
        headers=AUTH,
        json={"severity": "low", "source": "ui", "summary": "thumbnail failed to load"},
    )
    assert posted.status_code == 201
    errors = client.get("/api/errors", headers=AUTH).json()["errors"]
    assert errors[0]["summary"] == "thumbnail failed to load"


def test_clear_empties_the_log(client):
    client.post(
        "/api/errors", headers=AUTH, json={"severity": "low", "source": "ui", "summary": "x"}
    )
    assert client.delete("/api/errors", headers=AUTH).status_code == 200
    assert client.get("/api/errors", headers=AUTH).json()["errors"] == []


def test_a_recorded_error_never_carries_a_key(client):
    client.post(
        "/api/errors",
        headers=AUTH,
        json={
            "severity": "critical",
            "source": "ui",
            "summary": "call failed",
            "detail": "Authorization: Bearer gsk_shouldnotappear999",
        },
    )
    body = client.get("/api/errors", headers=AUTH).text
    assert "gsk_shouldnotappear999" not in body
    assert "REDACTED" in body


def test_errors_are_written_to_the_injected_path_not_the_real_profile(client, paths):
    client.post(
        "/api/errors", headers=AUTH, json={"severity": "low", "source": "ui", "summary": "x"}
    )
    assert paths["errors_path"].is_file()
