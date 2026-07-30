"""Tests for `POST /api/health/install`.

Nothing here runs pip. The route's whole risk is that it shells a package
name, so what is asserted is what it REFUSES and what exact argv it builds.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.core import health

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


class _Result:
    returncode = 0
    stdout = "Successfully installed pydub-0.25.1\n"
    stderr = ""


def test_install_requires_a_token(client):
    assert client.post("/api/health/install", json={"package": "pydub"}).status_code == 401


def test_it_shells_pip_for_exactly_one_named_package(client, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        seen.append(list(argv))
        return _Result()

    monkeypatch.setattr("avg.api.routes_settings.subprocess.run", fake_run)

    res = client.post("/api/health/install", json={"package": "pydub"}, headers=AUTH)

    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert seen == [[sys.executable, "-m", "pip", "install", "pydub"]]


def test_it_refuses_a_package_that_is_not_one_of_ours(client, monkeypatch):
    """The route shells a name. The name comes from `_PACKAGES` or it does
    not run at all - there is no path from a request body to an arbitrary
    install."""
    monkeypatch.setattr(
        "avg.api.routes_settings.subprocess.run",
        lambda *a, **k: pytest.fail("an unknown package must never reach pip"),
    )
    res = client.post(
        "/api/health/install", json={"package": "pydub; rm -rf /"}, headers=AUTH
    )
    assert res.status_code == 400
    assert "not one of this app" in res.json()["detail"]


def test_it_refuses_itself_in_a_frozen_build(client, monkeypatch):
    """A required dependency missing from a frozen build is a packaging bug
    in the build, not something a user should paper over at runtime."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "avg.api.routes_settings.subprocess.run",
        lambda *a, **k: pytest.fail("a packaged build must not install at runtime"),
    )
    res = client.post("/api/health/install", json={"package": "pydub"}, headers=AUTH)
    assert res.status_code == 400
    assert "packaged build" in res.json()["detail"]


def test_health_says_whether_installing_is_offered_at_all(client, monkeypatch):
    body = client.get("/api/health", headers=AUTH).json()
    assert body["can_install"] is True
    # Every row names the distribution it would install, or names none -
    # FFmpeg is not a pip package and its row has to explain rather than act.
    by_name = {c["name"]: c for c in body["components"]}
    assert by_name["pydub"]["package"] == "pydub"
    assert by_name["Pillow"]["package"] == "Pillow"
    assert by_name["FFmpeg"]["package"] == ""

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert client.get("/api/health", headers=AUTH).json()["can_install"] is False


def test_the_allowlist_is_the_package_table_itself():
    """Derived, never restated: a package added to `_PACKAGES` is installable
    with no second edit, and nothing else ever is."""
    assert health.installable_packages() == {
        dist for _label, _module, dist, _required in health._PACKAGES
    }
