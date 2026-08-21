import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.config.channels import get_channel
from avg.config.settings import load_settings
from avg.core.settings_schema import KEY_PROVIDER_IDS, SETTINGS

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
        "channel_defaults_path": tmp_path / "channel_defaults.json",
    }


@pytest.fixture
def client(paths):
    app = create_app(
        token=TOKEN,
        output_root=paths["output_root"],
        thumb_cache_dir=paths["thumb_cache_dir"],
        settings_path=paths["settings_path"],
        channels_dir=paths["channels_dir"],
        secrets_path=paths["secrets_path"],
        channel_defaults_path=paths["channel_defaults_path"],
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_route_returns_the_whole_catalogue(client):
    r = client.get("/api/settings/schema", headers=AUTH)
    assert r.status_code == 200
    body = r.json()["settings"]
    assert len(body) == len(SETTINGS)
    ids = {s["id"] for s in body}
    assert "video_form" in ids
    assert "content_shape" in ids
    assert "brand_preset" in ids


def test_schema_entries_declare_scope_correctly(client):
    body = {s["id"]: s for s in client.get("/api/settings/schema", headers=AUTH).json()["settings"]}
    assert body["theme"]["scope"] == "app"
    assert body["video_form"]["scope"] == "channel"
    assert body["content_shape"]["scope"] == "channel"
    assert body["visual_tone"]["scope"] == "channel"
    assert body["style_lock"]["scope"] == "channel"
    assert body["brand_preset"]["scope"] == "channel"


def test_schema_requires_token(client):
    assert client.get("/api/settings/schema").status_code == 401


# ---------------------------------------------------------------------------
# Values: reading
# ---------------------------------------------------------------------------


def test_values_route_returns_defaults_with_no_active_channel(client):
    r = client.get("/api/settings/values", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["active_channel_id"] is None
    assert body["app"]["theme"] == "night"
    assert body["channel"]["video_form"] == "shortform"
    assert body["channel"]["brand_preset"] == "modern_dark"


def test_values_requires_token(client):
    assert client.get("/api/settings/values").status_code == 401


# ---------------------------------------------------------------------------
# Values: app scope
# ---------------------------------------------------------------------------


def test_post_app_scope_value_persists_to_settings_json(client, paths):
    r = client.post("/api/settings/values", headers=AUTH, json={"values": {"theme": "day"}})
    assert r.status_code == 200
    assert r.json()["app"]["theme"] == "day"

    on_disk = load_settings(paths["settings_path"])
    assert on_disk.values["theme"] == "day"


def test_post_app_scope_value_is_reflected_on_next_get(client):
    # `theme` is the only non-secret app-scoped setting left after the dead
    # switches were removed.
    client.post("/api/settings/values", headers=AUTH, json={"values": {"theme": "day"}})
    r = client.get("/api/settings/values", headers=AUTH)
    assert r.json()["app"]["theme"] == "day"


def test_post_rejects_unknown_setting_id(client):
    r = client.post("/api/settings/values", headers=AUTH, json={"values": {"not_a_real_setting": 1}})
    assert r.status_code == 400
    assert "not_a_real_setting" in r.json()["detail"]


def test_post_rejects_invalid_choice_value(client):
    # `aspect_ratio` was this test's example setting pre-Slice-19; it's no
    # longer a real setting (derived from `video_form` instead), so
    # `video_form` - another `type="choice"` id - stands in for it.
    r = client.post("/api/settings/values", headers=AUTH, json={"values": {"video_form": "4:3"}})
    assert r.status_code == 400
    assert "4:3" in r.json()["detail"]


def test_post_coerces_numeric_strings_for_int_fields(client):
    # fps is channel-scoped, so this needs an active channel to actually
    # save.
    created = client.post("/api/channels", headers=AUTH, json={"label": "Coerce"}).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)

    r = client.post("/api/settings/values", headers=AUTH, json={"values": {"fps": "24"}})
    assert r.status_code == 200
    assert r.json()["channel"]["fps"] == 24

    r2 = client.post("/api/settings/values", headers=AUTH, json={"values": {"fps": "not-a-number"}})
    assert r2.status_code == 400


# ---------------------------------------------------------------------------
# Values: channel scope
# ---------------------------------------------------------------------------


def test_post_channel_scope_value_fails_cleanly_with_no_active_channel(client, paths):
    r = client.post("/api/settings/values", headers=AUTH, json={"values": {"video_form": "longform"}})
    assert r.status_code == 400
    assert "active channel" in r.json()["detail"].lower()

    # And nothing was written anywhere arbitrary.
    assert not paths["channels_dir"].exists() or all(
        json.loads(p.read_text(encoding="utf-8")).get("settings") == {}
        for p in paths["channels_dir"].glob("*.json")
    )


def test_post_channel_scope_value_does_not_leak_into_app_scope_write(client, paths):
    # A mixed request where the channel half fails must not silently apply
    # the app half either - the whole POST fails together.
    # The app half must be VALID, or this passes for the wrong reason: a
    # bad theme would fail validation before the "is a channel active"
    # check ever runs, and the test would no longer prove anything about
    # the channel half failing.
    client.post("/api/settings/values", headers=AUTH, json={"values": {"theme": "day", "video_form": "longform"}})
    on_disk = load_settings(paths["settings_path"])
    assert "theme" not in on_disk.values


def test_post_channel_scope_value_saves_to_the_active_channel(client, paths):
    created = client.post("/api/channels", headers=AUTH, json={"label": "My Channel"}).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)

    r = client.post(
        "/api/settings/values",
        headers=AUTH,
        json={"values": {"video_form": "longform", "brand_preset": "luxury_gold"}},
    )
    assert r.status_code == 200
    assert r.json()["channel"]["video_form"] == "longform"
    assert r.json()["channel"]["brand_preset"] == "luxury_gold"

    on_disk = get_channel(created["id"], paths["channels_dir"])
    assert on_disk.settings["video_form"] == "longform"
    assert on_disk.settings["brand_preset"] == "luxury_gold"


def test_deleting_the_active_channel_reverts_values_to_defaults(client, paths):
    created = client.post("/api/channels", headers=AUTH, json={"label": "Temp"}).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
    client.post("/api/settings/values", headers=AUTH, json={"values": {"video_form": "longform"}})

    client.delete(f"/api/channels/{created['id']}", headers=AUTH)

    r = client.get("/api/settings/values", headers=AUTH)
    assert r.json()["active_channel_id"] is None
    assert r.json()["channel"]["video_form"] == "shortform"  # back to the schema default


# ---------------------------------------------------------------------------
# Channels CRUD
# ---------------------------------------------------------------------------


def test_channels_route_seeds_and_lists_starter_channels(client):
    r = client.get("/api/channels", headers=AUTH)
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()["channels"]}
    assert ids == {"documentary", "news_report", "zack_d_films"}
    assert r.json()["active_channel_id"] is None


def test_create_activate_delete_channel_round_trip(client):
    created = client.post("/api/channels", headers=AUTH, json={"label": "Podcast Clips", "note": "n"}).json()
    assert created["label"] == "Podcast Clips"
    assert created["settings"] == {}

    activate = client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
    assert activate.status_code == 200
    assert activate.json()["active_channel_id"] == created["id"]

    listed = client.get("/api/channels", headers=AUTH).json()
    assert listed["active_channel_id"] == created["id"]

    deleted = client.delete(f"/api/channels/{created['id']}", headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}

    listed_after = client.get("/api/channels", headers=AUTH).json()
    assert created["id"] not in {c["id"] for c in listed_after["channels"]}
    assert listed_after["active_channel_id"] is None


def test_patch_renames_a_channel_and_keeps_its_id(client, paths):
    created = client.post("/api/channels", headers=AUTH, json={"label": "Original", "note": "n"}).json()

    r = client.patch(f"/api/channels/{created['id']}", headers=AUTH, json={"label": "Renamed"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert body["label"] == "Renamed"
    assert body["note"] == "n"

    on_disk = get_channel(created["id"], paths["channels_dir"])
    assert on_disk.label == "Renamed"


def test_patch_unknown_channel_is_404(client):
    r = client.patch("/api/channels/does-not-exist", headers=AUTH, json={"label": "x"})
    assert r.status_code == 404


def test_activating_an_unknown_channel_is_404(client):
    r = client.post("/api/channels/does-not-exist/activate", headers=AUTH)
    assert r.status_code == 404


def test_deleting_an_unknown_channel_is_a_noop(client):
    r = client.delete("/api/channels/does-not-exist", headers=AUTH)
    assert r.status_code == 200


def test_channels_routes_require_token(client):
    assert client.get("/api/channels").status_code == 401
    assert client.post("/api/channels", json={"label": "x"}).status_code == 401


# ---------------------------------------------------------------------------
# Keys — the critical "never leak a value" surface
# ---------------------------------------------------------------------------

SECRET_VALUE = "sk-super-secret-do-not-leak-1234567890"


def test_keys_route_lists_every_known_provider_as_false_by_default(client):
    r = client.get("/api/keys", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    for provider in KEY_PROVIDER_IDS:
        assert body[provider] is False


def test_set_key_reports_true_never_the_value(client):
    r = client.post("/api/keys/groq", headers=AUTH, json={"key": SECRET_VALUE})
    assert r.status_code == 200
    assert r.json() == {"groq": True}
    assert SECRET_VALUE not in r.text


def test_get_keys_reflects_a_set_key_as_true(client):
    client.post("/api/keys/groq", headers=AUTH, json={"key": SECRET_VALUE})
    r = client.get("/api/keys", headers=AUTH)
    assert r.json()["groq"] is True


def test_delete_key_clears_it_back_to_false(client):
    client.post("/api/keys/groq", headers=AUTH, json={"key": SECRET_VALUE})
    r = client.delete("/api/keys/groq", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"groq": False}
    assert client.get("/api/keys", headers=AUTH).json()["groq"] is False


def test_setting_an_empty_key_is_rejected(client):
    r = client.post("/api/keys/groq", headers=AUTH, json={"key": ""})
    assert r.status_code == 400


def test_keys_routes_require_token(client):
    assert client.get("/api/keys").status_code == 401
    assert client.post("/api/keys/groq", json={"key": "x"}).status_code == 401
    assert client.delete("/api/keys/groq").status_code == 401


def test_secret_settings_never_carry_a_value_through_settings_values(client):
    client.post("/api/keys/groq", headers=AUTH, json={"key": SECRET_VALUE})
    r = client.get("/api/settings/values", headers=AUTH)
    assert r.json()["app"]["groq"] is True  # boolean, not the key
    assert SECRET_VALUE not in r.text


def test_posting_a_secret_setting_through_values_route_is_rejected(client):
    r = client.post("/api/settings/values", headers=AUTH, json={"values": {"groq": SECRET_VALUE}})
    assert r.status_code == 400
    assert SECRET_VALUE not in r.text
    assert "groq" in r.json()["detail"]


def test_secrets_file_on_disk_never_contains_the_plaintext_key(client, paths):
    client.post("/api/keys/groq", headers=AUTH, json={"key": SECRET_VALUE})
    on_disk = paths["secrets_path"].read_bytes()
    assert SECRET_VALUE.encode("utf-8") not in on_disk


class TestNoRouteEverLeaksAKeyValue:
    """The task brief's one mandatory test: assert directly that no route
    in this whole module ever returns a key's value anywhere in its
    response body - success or error paths alike."""

    def test_sweep_every_route_after_setting_a_key(self, client, paths):
        client.post("/api/keys/groq", headers=AUTH, json={"key": SECRET_VALUE})
        client.post("/api/keys/elevenlabs", headers=AUTH, json={"key": SECRET_VALUE + "-2"})

        created = client.post("/api/channels", headers=AUTH, json={"label": "Sweep"}).json()
        client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
        client.post("/api/settings/values", headers=AUTH, json={"values": {"video_form": "longform"}})

        responses = [
            client.get("/api/settings/schema", headers=AUTH),
            client.get("/api/settings/values", headers=AUTH),
            client.post("/api/settings/values", headers=AUTH, json={"values": {"theme": "neon"}}),
            client.post("/api/settings/values", headers=AUTH, json={"values": {"groq": SECRET_VALUE}}),
            client.get("/api/channels", headers=AUTH),
            client.get("/api/keys", headers=AUTH),
            client.post("/api/keys/groq", headers=AUTH, json={"key": SECRET_VALUE}),
            client.post("/api/keys/does-not-exist-provider", headers=AUTH, json={"key": SECRET_VALUE}),
            client.delete("/api/keys/elevenlabs", headers=AUTH),
        ]

        for resp in responses:
            assert SECRET_VALUE not in resp.text
            assert (SECRET_VALUE + "-2") not in resp.text

        # And never on disk in the clear either.
        secrets_bytes = paths["secrets_path"].read_bytes()
        assert SECRET_VALUE.encode("utf-8") not in secrets_bytes
        for channel_file in paths["channels_dir"].glob("*.json"):
            assert SECRET_VALUE.encode("utf-8") not in channel_file.read_bytes()
        if paths["settings_path"].exists():
            assert SECRET_VALUE.encode("utf-8") not in paths["settings_path"].read_bytes()


# ---------------------------------------------------------------------------
# GET /api/costs (Slice 6B)


def test_costs_route_serves_the_table_and_needs_a_token(client):
    assert client.get("/api/costs").status_code == 401

    body = client.get("/api/costs", headers=AUTH).json()
    assert body["currency"] == "USD"
    assert body["base_cost"] == sum(body["leaf_cost"].values())
    # The phase -> leaf map is what lets the UI charge a partial Script for
    # its draft only; without it the frontend would need its own copy.
    assert body["phase_leaves"]["script"] == ["draft", "segmentation"]
    assert set(body["length_multiplier"]) >= {"30s", "60s", "8m"}


# ---------------------------------------------------------------------------
# GET /api/health (Slice 6C)


def test_health_route_reports_every_component_and_needs_a_token(client):
    assert client.get("/api/health").status_code == 401

    body = client.get("/api/health", headers=AUTH).json()
    names = [c["name"] for c in body["components"]]
    assert "FFmpeg" in names and "Pillow" in names
    assert body["ok"] is all(c["ok"] for c in body["components"])


def test_health_route_reports_a_broken_component_rather_than_failing(client, monkeypatch):
    """A broken FFmpeg must render as a row, not as a 500 - the whole point
    of the screen is to tell the user what is wrong."""
    from avg.core import health

    health.clear_cache()
    monkeypatch.setattr(
        "avg.core.health._check_ffmpeg",
        lambda: health.Component(name="FFmpeg", ok=False, detail="not found (ffmpeg)"),
    )

    r = client.get("/api/health?refresh=true", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    ffmpeg = next(c for c in body["components"] if c["name"] == "FFmpeg")
    assert ffmpeg["ok"] is False and "not found" in ffmpeg["detail"]
    health.clear_cache()


def test_health_report_runs_all_three_stages_in_order(client):
    assert client.get("/api/health/report").status_code == 401

    body = client.get("/api/health/report", headers=AUTH).json()
    # Order is meaningful: each stage assumes the one before it passed, so
    # the screen must be able to render them exactly as returned.
    assert [s["key"] for s in body["stages"]] == ["dependencies", "warmup", "safeguards"]
    assert body["ok"] is all(s["ok"] for s in body["stages"])
    for stage in body["stages"]:
        assert stage["checks"], f"{stage['key']} ran no checks"
        # REQUIRED checks alone decide the verdict. A machine without an
        # optional dependency (edge-tts, say) used to read as a failed
        # dependency stage next to a row labelled Optional.
        assert stage["ok"] is all(c["ok"] for c in stage["checks"] if c["required"])


def test_health_report_checks_the_isolated_paths_not_the_real_profile(client, paths):
    """The report must read app.state's paths, never resolve its own.

    Otherwise this test would inspect the developer's real %APPDATA% and
    the report would describe a different installation than the one the
    window is showing.
    """
    body = client.get("/api/health/report", headers=AUTH).json()
    warmup = next(s for s in body["stages"] if s["key"] == "warmup")
    settings_check = next(c for c in warmup["checks"] if c["name"] == "Settings document")
    # A fresh tmp_path settings file has no output root chosen yet - proof
    # it read the injected path rather than the real one.
    assert "not chosen yet" in settings_check["detail"]

    safeguards = next(s for s in body["stages"] if s["key"] == "safeguards")
    writable = next(c for c in safeguards["checks"] if c["name"] == "Library folder writable")
    assert str(paths["output_root"]) in writable["detail"]


def test_open_logs_folder_creates_it_and_needs_a_token(client):
    from avg.config.user_assets import default_errors_path

    assert client.post("/api/health/open-logs").status_code == 401

    r = client.post("/api/health/open-logs", headers=AUTH)
    assert r.status_code == 200
    opened = Path(r.json()["opened"])
    assert opened.is_dir()
    assert opened == default_errors_path().parent


def test_open_logs_folder_does_not_open_explorer_under_pytest(client, monkeypatch):
    """Same guard, same test technique as `capcut.py`'s existing
    `test_export_does_not_open_explorer_under_pytest` - a spy on
    `os.startfile` proves the route never calls it during the suite,
    which just checking for a 200 response cannot: `suppress(Exception)`
    would swallow a broken guard's `AttributeError` on this non-Windows
    sandbox exactly as quietly as a working guard's absence of a call,
    making the two indistinguishable without a spy.
    """
    import os

    calls = []
    # os.startfile doesn't exist on non-Windows Pythons; add it if absent
    # so the spy can be installed uniformly regardless of platform -
    # matching test_capcut.py's own handling of the same situation.
    if not hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(os, "startfile", lambda *a, **k: calls.append(a), raising=False)
    assert "PYTEST_CURRENT_TEST" in os.environ

    r = client.post("/api/health/open-logs", headers=AUTH)

    assert r.status_code == 200
    assert calls == []


# ---------------------------------------------------------------------------
# Phases


def test_phases_route_serves_the_registry_in_order(client):
    body = client.get("/api/phases", headers=AUTH).json()
    keys = [p["key"] for p in body["phases"]]
    assert keys == [
        "brief", "script", "director1", "visuals",
        "voice", "director2", "screen_text", "render",
    ]
    assert [p["id"] for p in body["phases"]] == list(range(8))


def test_every_phase_describes_itself(client):
    """The description is what an un-started phase panel shows instead of an
    empty box. A blank one would put the panel straight back where it was."""
    body = client.get("/api/phases", headers=AUTH).json()
    for phase in body["phases"]:
        assert phase["description"].strip(), phase["key"]


def test_the_phases_route_requires_a_token(client):
    assert client.get("/api/phases").status_code == 401


# ---------------------------------------------------------------------------
# resolve_channel_settings (Slice 19 §2.3): per-field, absence-only
# inheritance through the one resolver - a channel's own document wins,
# else the app-wide defaults document wins, else the SettingSpec default.
# Replaces the old whole-document settings_source switch this block used
# to guard (deleted, Task 2).


def _make_channel(client, label="Docs"):
    created = client.post("/api/channels", json={"label": label}, headers=AUTH).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
    return created["id"]


def test_a_key_present_in_the_channel_wins(client):
    from avg.config.channel_defaults import save_defaults

    _make_channel(client)
    save_defaults({"length_preset": "90s"}, client.app.state.channel_defaults_path)
    client.post("/api/settings/values", json={"values": {"length_preset": "30s"}}, headers=AUTH)

    body = client.get("/api/settings/values", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "30s"


def test_a_key_absent_from_the_channel_resolves_from_defaults(client):
    from avg.config.channel_defaults import save_defaults

    _make_channel(client)
    save_defaults({"length_preset": "90s"}, client.app.state.channel_defaults_path)

    body = client.get("/api/settings/values", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "90s"


def test_a_key_absent_from_both_resolves_to_the_spec_default(client):
    from avg.core.settings_schema import BY_ID

    _make_channel(client)
    body = client.get("/api/settings/values", headers=AUTH).json()
    assert body["channel"]["length_preset"] == BY_ID["length_preset"].default


def test_empty_string_stored_against_an_owned_key_is_never_treated_as_absent(client):
    from avg.config.channel_defaults import save_defaults

    _make_channel(client)
    save_defaults({"text_model_default": "groq/some-model"}, client.app.state.channel_defaults_path)
    response = client.post(
        "/api/settings/values", json={"values": {"text_model_default": ""}}, headers=AUTH
    )
    assert response.status_code == 200

    body = client.get("/api/settings/values", headers=AUTH).json()
    assert body["channel"]["text_model_default"] == ""


def test_release_deletes_the_owned_key_leaving_a_sibling_untouched(client):
    _make_channel(client)
    client.post(
        "/api/settings/values",
        json={"values": {"length_preset": "30s", "fps": 60}},
        headers=AUTH,
    )

    client.delete("/api/settings/values/length_preset", headers=AUTH)

    channel_id = client.get("/api/channels", headers=AUTH).json()["active_channel_id"]
    channel = get_channel(channel_id, client.app.state.channels_dir)
    assert "length_preset" not in channel.settings
    assert channel.settings["fps"] == 60


def test_releasing_an_already_absent_key_is_a_noop_not_a_404(client):
    _make_channel(client)
    response = client.delete("/api/settings/values/length_preset", headers=AUTH)
    assert response.status_code == 200


def test_releasing_an_unknown_or_app_scope_id_is_a_400(client):
    _make_channel(client)
    assert client.delete("/api/settings/values/not-a-real-id", headers=AUTH).status_code == 400
    assert client.delete("/api/settings/values/theme", headers=AUTH).status_code == 400


def test_scope_global_get_reflects_the_defaults_document_not_the_channel(client):
    from avg.config.channel_defaults import save_defaults

    _make_channel(client)
    save_defaults({"length_preset": "90s"}, client.app.state.channel_defaults_path)
    client.post("/api/settings/values", json={"values": {"length_preset": "30s"}}, headers=AUTH)

    body = client.get("/api/settings/values?scope=global", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "90s"


def test_target_global_write_updates_defaults_not_the_active_channel(client):
    _make_channel(client)
    client.post(
        "/api/settings/values",
        json={"values": {"length_preset": "30s"}, "target": "global"},
        headers=AUTH,
    )

    channel_id = client.get("/api/channels", headers=AUTH).json()["active_channel_id"]
    channel = get_channel(channel_id, client.app.state.channels_dir)
    assert "length_preset" not in channel.settings

    body = client.get("/api/settings/values?scope=global", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "30s"


def test_target_global_write_needs_no_active_channel(client):
    response = client.post(
        "/api/settings/values",
        json={"values": {"length_preset": "30s"}, "target": "global"},
        headers=AUTH,
    )
    assert response.status_code == 200

    body = client.get("/api/settings/values?scope=global", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "30s"


def test_target_channel_write_still_400s_with_no_active_channel(client):
    response = client.post(
        "/api/settings/values",
        json={"values": {"length_preset": "30s"}, "target": "channel"},
        headers=AUTH,
    )
    assert response.status_code == 400


def test_porting_copies_channel_values_and_no_secret(client):
    from avg.config.channel_defaults import load_defaults

    _make_channel(client)
    client.post("/api/settings/values", json={"values": {"length_preset": "30s"}}, headers=AUTH)
    client.post("/api/keys/groq", json={"key": "sk-should-never-be-copied"}, headers=AUTH)

    res = client.post("/api/settings/port-to-global", headers=AUTH)
    assert res.status_code == 200

    stored = load_defaults(client.app.state.channel_defaults_path)
    assert stored["length_preset"] == "30s"
    assert "groq" not in stored
    assert "sk-should-never-be-copied" not in str(stored)
    # App-scope settings belong to the app, not to a channel snapshot.
    assert "theme" not in stored


def test_porting_without_an_active_channel_is_refused(client):
    assert client.post("/api/settings/port-to-global", headers=AUTH).status_code == 400


def test_hardware_endpoint_reports_a_positive_cpu_count(client):
    r = client.get("/api/system/hardware", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["cpu_count"] >= 1
