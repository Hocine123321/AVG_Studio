"""Preview routes. The important tests here are the REFUSALS - nothing in
this file may perform a real synthesis or generation, both of which cost
real money.
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


def test_preview_routes_require_a_token(client):
    assert client.post("/api/preview/caption", json={}).status_code == 401


def test_a_caption_preview_is_free_and_needs_no_confirmation(client):
    res = client.post(
        "/api/preview/caption",
        json={"width": 640, "height": 360, "font_name": "Arial", "font_size": 40, "position": "bottom"},
        headers=AUTH,
    )
    # 200 with a PNG, or 503 when ffmpeg is unavailable here - never a 400
    # demanding a confirmation, because nothing is being spent.
    assert res.status_code in (200, 503)


def test_an_elevenlabs_voice_preview_refuses_without_confirmation(client, monkeypatch):
    """The rule that keeps a keystroke from costing money. Enforced on the
    BACKEND, so it holds regardless of what the frontend does."""
    from avg.media import preview

    monkeypatch.setattr(
        preview,
        "voice_sample",
        lambda *a, **k: pytest.fail("a paid preview must not run without confirmation"),
    )
    res = client.post(
        "/api/preview/voice",
        json={"provider": "elevenlabs", "voice_id": "abc"},
        headers=AUTH,
    )
    assert res.status_code == 400
    assert "confirm" in res.json()["detail"].lower()


def test_an_edge_voice_preview_needs_no_confirmation(client, monkeypatch):
    """Edge is free, so it is allowed to preview live."""
    monkeypatch.setattr("avg.api.routes_preview.voice_sample", lambda *a, **k: b"ID3audio")
    res = client.post(
        "/api/preview/voice",
        json={"provider": "edge", "voice_id": "en-GB-RyanNeural"},
        headers=AUTH,
    )
    assert res.status_code == 200
    assert res.content == b"ID3audio"


def test_an_image_preview_always_refuses_without_confirmation(client, monkeypatch):
    """`image_sample` lives in `avg.api.routes_preview`, not `avg.media.preview`
    - it needs `avg.pipeline.phases.visuals._build_provider`, and `avg/media/`
    may not import from `avg/pipeline/` (Global Constraint). The route module
    may import from both layers, so the function moved here instead of
    staying beside `voice_sample`."""
    monkeypatch.setattr(
        "avg.api.routes_preview.image_sample",
        lambda *a, **k: pytest.fail("an image preview costs money and must not run unasked"),
    )
    res = client.post(
        "/api/preview/image",
        json={"provider": "pollinations", "model": "flux", "aspect_ratio": "9:16"},
        headers=AUTH,
    )
    assert res.status_code == 400


def test_a_second_preview_while_one_runs_is_refused(client, monkeypatch):
    """One preview at a time, in its own slot - never the render slot."""
    import threading

    started = threading.Event()
    release = threading.Event()

    def slow(*_a, **_k):
        started.set()
        release.wait(timeout=5)
        return b"ID3audio"

    monkeypatch.setattr("avg.api.routes_preview.voice_sample", slow)
    try:
        worker = threading.Thread(
            target=lambda: client.post(
                "/api/preview/voice",
                json={"provider": "edge", "voice_id": "x"},
                headers=AUTH,
            )
        )
        worker.start()
        assert started.wait(timeout=5)
        second = client.post(
            "/api/preview/voice", json={"provider": "edge", "voice_id": "y"}, headers=AUTH
        )
        assert second.status_code == 409
    finally:
        release.set()
        worker.join(timeout=5)

    # The render slot must be untouched by any preview.
    assert client.app.state.render_thread is None
