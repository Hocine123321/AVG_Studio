from pathlib import Path

from avg.config.settings import SettingsDoc, load_settings, save_settings


def test_round_trips_a_saved_document(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(SettingsDoc(output_root=r"D:\Videos\AVG Studio", onboarded=True), path)

    loaded = load_settings(path)
    assert loaded.schema_version == 1
    assert loaded.output_root == r"D:\Videos\AVG Studio"
    assert loaded.onboarded is True


def test_absent_file_yields_defaults_not_onboarded(tmp_path):
    doc = load_settings(tmp_path / "does_not_exist.json")
    assert doc.onboarded is False
    assert doc.output_root is None


def test_corrupt_file_yields_defaults_rather_than_raising(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not valid json", encoding="utf-8")
    doc = load_settings(path)
    assert doc.onboarded is False
    assert doc.output_root is None


def test_save_is_atomic_and_leaves_no_temp_files_behind(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(SettingsDoc(output_root="C:\\lib", onboarded=True), path)

    leftovers = [p for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []
    assert path.exists()


def test_save_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    save_settings(SettingsDoc(output_root="C:\\lib", onboarded=True), path)
    assert path.exists()
    assert load_settings(path).output_root == "C:\\lib"


def test_active_channel_id_and_values_default_empty(tmp_path):
    doc = load_settings(tmp_path / "does_not_exist.json")
    assert doc.active_channel_id is None
    assert doc.values == {}


def test_active_channel_id_and_values_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(SettingsDoc(active_channel_id="news_report", values={"theme": "dark"}), path)

    loaded = load_settings(path)
    assert loaded.active_channel_id == "news_report"
    assert loaded.values == {"theme": "dark"}


class TestLegacyActiveChannelMigration:
    """Task 2 gave the active-channel pointer its own `active_channel.json`
    sibling of settings.json. Task 3 folds it onto SettingsDoc instead -
    these prove an existing value survives the move and the legacy file
    stops being consulted afterward (so a later explicit `None` sticks)."""

    def test_existing_legacy_value_is_migrated_onto_the_document(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        legacy_path = tmp_path / "active_channel.json"
        legacy_path.write_text('{"active_channel_id": "documentary"}', encoding="utf-8")

        doc = load_settings(settings_path)

        assert doc.active_channel_id == "documentary"

    def test_migration_persists_immediately_to_disk(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        legacy_path = tmp_path / "active_channel.json"
        legacy_path.write_text('{"active_channel_id": "documentary"}', encoding="utf-8")

        load_settings(settings_path)

        assert settings_path.exists()
        assert load_settings(settings_path).active_channel_id == "documentary"

    def test_legacy_file_is_removed_after_migration(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        legacy_path = tmp_path / "active_channel.json"
        legacy_path.write_text('{"active_channel_id": "documentary"}', encoding="utf-8")

        load_settings(settings_path)

        assert not legacy_path.exists()

    def test_a_later_explicit_clear_is_not_overridden_by_a_stale_legacy_file(self, tmp_path):
        # Regression case: without deleting the legacy file on migration, a
        # user who explicitly clears the active channel after upgrading
        # would see it silently come back on the next load.
        settings_path = tmp_path / "settings.json"
        legacy_path = tmp_path / "active_channel.json"
        legacy_path.write_text('{"active_channel_id": "documentary"}', encoding="utf-8")

        doc = load_settings(settings_path)  # migrates + deletes legacy file
        assert doc.active_channel_id == "documentary"
        doc.active_channel_id = None
        save_settings(doc, settings_path)

        assert load_settings(settings_path).active_channel_id is None

    def test_corrupt_legacy_file_is_ignored_and_removed(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        legacy_path = tmp_path / "active_channel.json"
        legacy_path.write_text("{ not valid json", encoding="utf-8")

        doc = load_settings(settings_path)

        assert doc.active_channel_id is None
        assert not legacy_path.exists()

    def test_absent_legacy_file_is_a_plain_noop(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        doc = load_settings(settings_path)
        assert doc.active_channel_id is None
        assert not settings_path.exists()  # nothing to migrate, nothing written
