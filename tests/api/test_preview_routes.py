"""Preview routes. The important tests here are the REFUSALS - nothing in
this file may perform a real synthesis or generation, both of which cost
real money.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.core.errors import AvgError, ErrorKind

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
    BACKEND, so it holds regardless of what the frontend does.

    Patched at `avg.api.routes_preview.voice_sample` - the name the route
    module actually calls - rather than `avg.media.preview.voice_sample`,
    since the route imported its own binding and patching the origin module
    would not touch it. That is what makes this a real guard: if the
    confirmation check were ever removed, this fails loudly instead of
    silently spending money.
    """
    monkeypatch.setattr(
        "avg.api.routes_preview.voice_sample",
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
    """`image_sample` lives in `avg.api.routes_preview` (see that module's
    docstring for why: it needs `avg.pipeline.phases.visuals`, which
    `avg/media/` may not import), so that is what gets patched here."""
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


# ── Important 5: both money-spending functions live in this module ────────


def test_voice_sample_speaks_the_shared_sentence(monkeypatch):
    """Moved here from `avg.media.preview` (Slice 14 review, Important 5) -
    never invents its own text, one constant, so every provider says the
    same thing and the panel can quote it."""
    from avg.api import routes_preview
    from avg.media.preview import SAMPLE_SENTENCE

    spoken: list[str] = []

    class _Fake:
        def synthesize(self, text, *, voice_id, model):
            spoken.append(text)
            return b"ID3fake-audio"

    monkeypatch.setattr(routes_preview, "_voice_backend", lambda provider: _Fake())
    data = routes_preview.voice_sample("edge", "en-GB-RyanNeural")

    assert data == b"ID3fake-audio"
    assert spoken == [SAMPLE_SENTENCE]


def test_voice_sample_raises_for_an_unknown_provider():
    """No longer degrades to `b""` - Important 2 requires a real `AvgError`
    so the route can say what actually happened."""
    from avg.api.routes_preview import voice_sample

    with pytest.raises(AvgError) as excinfo:
        voice_sample("not-a-provider", "x")
    assert excinfo.value.kind == ErrorKind.model_missing


def test_voice_sample_lets_a_backend_failure_propagate(monkeypatch):
    """A provider's own `AvgError` must reach the route, not be swallowed
    into `b""` - that swallowing is exactly what made every voice failure
    look identical (Slice 14 review, Important 2)."""
    from avg.api import routes_preview

    class _Boom:
        def synthesize(self, text, *, voice_id, model):
            raise AvgError(ErrorKind.auth, "ElevenLabs API key is not set")

    monkeypatch.setattr(routes_preview, "_voice_backend", lambda provider: _Boom())
    with pytest.raises(AvgError) as excinfo:
        routes_preview.voice_sample("elevenlabs", "x")
    assert excinfo.value.kind == ErrorKind.auth


def test_image_sample_raises_for_an_unknown_provider_rather_than_falling_back(monkeypatch):
    """Minor: `_build_provider` itself falls back to the catalogue default
    for an unrecognised name (a real render needs something runnable even
    from a stale channel document), but a PRESSED preview must never spend
    on a provider the user did not choose. `_build_provider` must never
    even be reached for an unknown name here."""
    from avg.api.routes_preview import image_sample

    monkeypatch.setattr(
        "avg.pipeline.phases.visuals._build_provider",
        lambda *a, **k: pytest.fail("must not fall back to a different provider"),
    )
    with pytest.raises(AvgError) as excinfo:
        image_sample("not-a-real-provider", "flux", "9:16")
    assert excinfo.value.kind == ErrorKind.model_missing


# ── Important 2: a preview failure reports what actually happened ─────────


def test_a_voice_preview_failure_reports_the_real_reason(client, monkeypatch):
    """A 401 from the provider must reach the panel as a 401 that says the
    key was rejected - not a generic 'check its key' sentence indistinguishable
    from every other failure, and never the provider's raw response body
    (which could carry account-identifying text)."""
    monkeypatch.setattr(
        "avg.api.routes_preview.voice_sample",
        lambda *a, **k: (_ for _ in ()).throw(
            AvgError(ErrorKind.auth, "boom", detail="raw-provider-body-do-not-leak")
        ),
    )
    res = client.post(
        "/api/preview/voice",
        json={"provider": "edge", "voice_id": "x"},
        headers=AUTH,
    )
    assert res.status_code == 401
    detail = res.json()["detail"]
    assert "raw-provider-body-do-not-leak" not in detail
    assert "key" in detail.lower()


def test_an_image_preview_failure_never_blames_ffmpeg(client, monkeypatch):
    """The pre-fix behaviour: every image failure collapsed to `b""` and
    the route named FFmpeg, which is never involved in an image generation.
    A quota failure must say so, at a 429, without FFmpeg anywhere in it."""
    monkeypatch.setattr(
        "avg.api.routes_preview.image_sample",
        lambda *a, **k: (_ for _ in ()).throw(
            AvgError(ErrorKind.quota, "boom", detail="raw-provider-body-do-not-leak")
        ),
    )
    res = client.post(
        "/api/preview/image",
        json={
            "provider": "pollinations",
            "model": "flux",
            "aspect_ratio": "9:16",
            "confirm_paid": True,
        },
        headers=AUTH,
    )
    assert res.status_code == 429
    detail = res.json()["detail"]
    assert "ffmpeg" not in detail.lower()
    assert "raw-provider-body-do-not-leak" not in detail


def test_an_image_preview_refuses_an_unknown_provider_at_the_route(client, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.visuals._build_provider",
        lambda *a, **k: pytest.fail("must not build a fallback provider for an unrecognised name"),
    )
    res = client.post(
        "/api/preview/image",
        json={
            "provider": "not-a-real-provider",
            "model": "x",
            "aspect_ratio": "9:16",
            "confirm_paid": True,
        },
        headers=AUTH,
    )
    assert res.status_code == 404


# ── Minor: the image route's Content-Type must match the actual bytes ─────


def test_an_image_preview_reports_the_content_type_it_actually_sends(client, monkeypatch):
    """A provider can return JPEG or WebP, not only PNG (Gemini's own
    `outputMimeType` can ask for JPEG) - the route must not claim
    `image/png` unconditionally, a claim it could not back."""
    jpeg_bytes = b"\xff\xd8\xff\xe0rest-of-a-jpeg"
    monkeypatch.setattr("avg.api.routes_preview.image_sample", lambda *a, **k: jpeg_bytes)
    res = client.post(
        "/api/preview/image",
        json={"provider": "pollinations", "model": "flux", "aspect_ratio": "9:16", "confirm_paid": True},
        headers=AUTH,
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == jpeg_bytes


def test_an_image_preview_falls_back_to_octet_stream_for_unrecognised_bytes(client, monkeypatch):
    monkeypatch.setattr("avg.api.routes_preview.image_sample", lambda *a, **k: b"not-a-real-image-format")
    res = client.post(
        "/api/preview/image",
        json={"provider": "pollinations", "model": "flux", "aspect_ratio": "9:16", "confirm_paid": True},
        headers=AUTH,
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/octet-stream"


def test_sniff_image_media_type_recognises_png_jpeg_and_webp():
    from avg.api.routes_preview import _sniff_image_media_type

    assert _sniff_image_media_type(b"\x89PNG\r\n\x1a\n\x00\x00") == "image/png"
    assert _sniff_image_media_type(b"\xff\xd8\xff\xe0\x00\x00") == "image/jpeg"
    assert _sniff_image_media_type(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"
    assert _sniff_image_media_type(b"nonsense") == "application/octet-stream"
