from avg.config.channels import (
    Channel,
    active_channel_id,
    create_channel,
    default_channels_dir,
    delete_channel,
    get_channel,
    list_channels,
    save_channel,
    set_active,
    update_channel,
)
from avg.config.settings import load_settings


def test_create_save_load_round_trip(tmp_path):
    channels_dir = tmp_path / "channels"
    ch = create_channel("My Channel", note="a note", channels_dir=channels_dir)

    assert ch.id == "my_channel"
    assert ch.label == "My Channel"
    assert ch.note == "a note"
    assert ch.settings == {}
    assert ch.schema_version == 1

    ch.settings["visual_tone"] = "gritty documentary"
    ch.note = "updated note"
    save_channel(ch, channels_dir=channels_dir)

    loaded = get_channel("my_channel", channels_dir=channels_dir)
    assert loaded is not None
    assert loaded.label == "My Channel"
    assert loaded.note == "updated note"
    assert loaded.settings == {"visual_tone": "gritty documentary"}


def test_get_channel_returns_none_for_missing_id(tmp_path):
    channels_dir = tmp_path / "channels"
    assert get_channel("does-not-exist", channels_dir=channels_dir) is None


def test_save_channel_creates_missing_parent_directories(tmp_path):
    channels_dir = tmp_path / "nested" / "channels"
    ch = Channel(id="test", label="Test", settings={})
    save_channel(ch, channels_dir=channels_dir)

    loaded = get_channel("test", channels_dir=channels_dir)
    assert loaded is not None
    assert loaded.label == "Test"


def test_save_is_atomic_and_leaves_no_temp_files_behind(tmp_path):
    channels_dir = tmp_path / "channels"
    ch = create_channel("Clean", channels_dir=channels_dir)

    leftovers = [p for p in channels_dir.iterdir() if not p.name.endswith(".json")]
    assert leftovers == []
    assert (channels_dir / f"{ch.id}.json").exists()


def test_corrupt_channel_file_is_skipped_not_raised(tmp_path):
    channels_dir = tmp_path / "channels"
    channels_dir.mkdir(parents=True)
    (channels_dir / "seeded_marker.json").write_text("not json at all", encoding="utf-8")

    # An empty-but-existing folder must not be re-seeded (see seeding
    # test below); a corrupt file inside it must just be skipped.
    assert list_channels(channels_dir=channels_dir) == []
    assert get_channel("seeded_marker", channels_dir=channels_dir) is None


class TestUpdateChannel:
    def test_rename_label_leaves_id_and_settings_untouched(self, tmp_path):
        channels_dir = tmp_path / "channels"
        ch = create_channel("My Channel", note="orig", channels_dir=channels_dir)
        ch.settings["visual_tone"] = "gritty"
        save_channel(ch, channels_dir=channels_dir)

        updated = update_channel(ch.id, label="Renamed", channels_dir=channels_dir)

        assert updated is not None
        assert updated.id == ch.id
        assert updated.label == "Renamed"
        assert updated.note == "orig"
        assert updated.settings == {"visual_tone": "gritty"}

        reloaded = get_channel(ch.id, channels_dir=channels_dir)
        assert reloaded.label == "Renamed"

    def test_update_note_only_leaves_label_alone(self, tmp_path):
        channels_dir = tmp_path / "channels"
        ch = create_channel("Keep Label", channels_dir=channels_dir)

        updated = update_channel(ch.id, note="new note", channels_dir=channels_dir)

        assert updated.label == "Keep Label"
        assert updated.note == "new note"

    def test_update_missing_channel_returns_none(self, tmp_path):
        channels_dir = tmp_path / "channels"
        assert update_channel("does-not-exist", label="x", channels_dir=channels_dir) is None


class TestSlugDeduplication:
    """Folders are pre-created (`mkdir` before any `create_channel` call)
    so first-run seeding (covered separately in TestSeeding) never fires
    here and doesn't contaminate the id sequence under test."""

    def test_two_channels_with_the_same_label_get_distinct_ids(self, tmp_path):
        channels_dir = tmp_path / "channels"
        channels_dir.mkdir(parents=True)
        first = create_channel("News", channels_dir=channels_dir)
        second = create_channel("News", channels_dir=channels_dir)

        assert first.id == "news"
        assert second.id == "news_2"
        assert first.id != second.id

    def test_labels_differing_only_by_case_still_collide_and_dedup(self, tmp_path):
        channels_dir = tmp_path / "channels"
        channels_dir.mkdir(parents=True)
        documentary = create_channel("Documentary", channels_dir=channels_dir)
        lowercase = create_channel("documentary", channels_dir=channels_dir)

        assert documentary.id == "documentary"
        assert lowercase.id == "documentary_2"

        ids = {c.id for c in list_channels(channels_dir=channels_dir)}
        assert {"documentary", "documentary_2"} <= ids

    def test_a_third_colliding_label_keeps_incrementing(self, tmp_path):
        channels_dir = tmp_path / "channels"
        channels_dir.mkdir(parents=True)
        a = create_channel("Zack D Films", channels_dir=channels_dir)
        b = create_channel("Zack D Films", channels_dir=channels_dir)
        c = create_channel("Zack D Films", channels_dir=channels_dir)

        assert (a.id, b.id, c.id) == ("zack_d_films", "zack_d_films_2", "zack_d_films_3")

    def test_symbol_only_label_falls_back_to_a_stable_slug(self, tmp_path):
        channels_dir = tmp_path / "channels"
        channels_dir.mkdir(parents=True)
        ch = create_channel("!!!", channels_dir=channels_dir)
        assert ch.id == "channel"


class TestActiveChannel:
    """Storage moved onto avg.config.settings's SettingsDoc (Task 3) - these
    exercise active_channel_id/set_active through a settings_path pointing
    at a throwaway settings.json rather than the old dedicated pointer
    file. See tests/config/test_settings.py for the migration itself."""

    def test_no_active_channel_by_default(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        assert active_channel_id(settings_path=settings_path) is None

    def test_set_active_round_trips(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        set_active("news_report", settings_path=settings_path)
        assert active_channel_id(settings_path=settings_path) == "news_report"

    def test_set_active_none_clears_it(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        set_active("news_report", settings_path=settings_path)
        set_active(None, settings_path=settings_path)
        assert active_channel_id(settings_path=settings_path) is None

    def test_set_active_persists_to_the_settings_document(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        set_active("news_report", settings_path=settings_path)
        assert load_settings(settings_path).active_channel_id == "news_report"

    def test_deleting_the_active_channel_clears_the_pointer(self, tmp_path):
        channels_dir = tmp_path / "channels"
        settings_path = tmp_path / "settings.json"
        ch = create_channel("News", channels_dir=channels_dir)
        set_active(ch.id, settings_path=settings_path)

        delete_channel(ch.id, channels_dir=channels_dir, settings_path=settings_path)

        assert active_channel_id(settings_path=settings_path) is None
        assert get_channel(ch.id, channels_dir=channels_dir) is None

    def test_deleting_a_non_active_channel_leaves_the_pointer_alone(self, tmp_path):
        channels_dir = tmp_path / "channels"
        settings_path = tmp_path / "settings.json"
        keep = create_channel("Keep Me", channels_dir=channels_dir)
        drop = create_channel("Drop Me", channels_dir=channels_dir)
        set_active(keep.id, settings_path=settings_path)

        delete_channel(drop.id, channels_dir=channels_dir, settings_path=settings_path)

        assert active_channel_id(settings_path=settings_path) == keep.id

    def test_deleting_a_missing_channel_is_a_noop(self, tmp_path):
        channels_dir = tmp_path / "channels"
        settings_path = tmp_path / "settings.json"
        # Must not raise even with nothing on disk yet.
        delete_channel("never-existed", channels_dir=channels_dir, settings_path=settings_path)
        assert active_channel_id(settings_path=settings_path) is None


class TestSeeding:
    def test_first_touch_seeds_three_starter_channels(self, tmp_path):
        channels_dir = tmp_path / "channels"
        assert not channels_dir.exists()

        channels = list_channels(channels_dir=channels_dir)

        assert len(channels) == 3
        ids = {c.id for c in channels}
        assert ids == {"documentary", "news_report", "zack_d_films"}
        for c in channels:
            assert c.settings == {}

    def test_seeding_only_happens_once_per_folder(self, tmp_path):
        channels_dir = tmp_path / "channels"
        list_channels(channels_dir=channels_dir)  # first touch: seeds

        # Delete every channel, but the folder itself remains.
        for c in list_channels(channels_dir=channels_dir):
            delete_channel(c.id, channels_dir=channels_dir)
        assert list_channels(channels_dir=channels_dir) == []

        # A later call must not resurrect anything - folder still exists.
        assert list_channels(channels_dir=channels_dir) == []

    def test_a_deleted_seed_channel_does_not_come_back(self, tmp_path):
        channels_dir = tmp_path / "channels"
        list_channels(channels_dir=channels_dir)  # seeds documentary, etc.

        delete_channel("documentary", channels_dir=channels_dir)

        ids = {c.id for c in list_channels(channels_dir=channels_dir)}
        assert "documentary" not in ids
        assert ids == {"news_report", "zack_d_films"}

        # Simulate a fresh process reading the same folder again.
        ids_again = {c.id for c in list_channels(channels_dir=channels_dir)}
        assert "documentary" not in ids_again

    def test_empty_folder_created_by_caller_is_not_reseeded(self, tmp_path):
        # An empty folder that already exists (not merely "never seen")
        # must be treated identically to "user deleted everything" -
        # never seeded, per the "folder absent, not empty" rule.
        channels_dir = tmp_path / "channels"
        channels_dir.mkdir(parents=True)

        assert list_channels(channels_dir=channels_dir) == []

    def test_default_paths_live_under_appdata_avg_studio(self):
        assert default_channels_dir().parts[-2:] == ("AVG Studio", "channels")
