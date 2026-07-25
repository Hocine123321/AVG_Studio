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
