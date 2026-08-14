from pathlib import Path

from avg.config.user_assets import user_assets_root


def test_default_root_is_under_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert user_assets_root() == tmp_path / "Documents" / "AVG Studio" / "UserAssets"


def test_falls_back_to_home_when_userprofile_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert user_assets_root() == tmp_path / "Documents" / "AVG Studio" / "UserAssets"


import json
import shutil

from avg.config.user_assets import migrate_legacy_files, user_assets_root


def test_migrates_an_old_only_file(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text('{"a": 1}', encoding="utf-8")

    moved = migrate_legacy_files(old_base, new_root)

    assert moved == [{
        "from": str(old_base / "settings.json"),
        "to": str(new_root / "Config" / "settings.json"),
    }]
    assert not (old_base / "settings.json").exists()
    assert (new_root / "Config" / "settings.json").read_text(encoding="utf-8") == '{"a": 1}'


def test_nothing_old_is_a_silent_no_op(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    assert migrate_legacy_files(old_base, new_root) == []
    assert not (new_root / "migration.json").exists()


def test_both_present_leaves_old_alone_and_keeps_new(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("OLD", encoding="utf-8")
    (new_root / "Config").mkdir(parents=True)
    (new_root / "Config" / "settings.json").write_text("NEW", encoding="utf-8")

    moved = migrate_legacy_files(old_base, new_root)

    assert moved == []
    assert (old_base / "settings.json").read_text(encoding="utf-8") == "OLD"
    assert (new_root / "Config" / "settings.json").read_text(encoding="utf-8") == "NEW"


def test_moves_the_channels_directory_as_one_unit(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    channels_dir = old_base / "channels"
    channels_dir.mkdir(parents=True)
    (channels_dir / "documentary.json").write_text("{}", encoding="utf-8")

    migrate_legacy_files(old_base, new_root)

    assert not channels_dir.exists()
    new_channels = new_root / "Config" / "channels"
    assert (new_channels / "documentary.json").read_text(encoding="utf-8") == "{}"


def test_second_call_after_a_full_migration_is_a_no_op(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("{}", encoding="utf-8")

    first = migrate_legacy_files(old_base, new_root)
    second = migrate_legacy_files(old_base, new_root)

    assert len(first) == 1
    assert second == []


def test_a_failed_item_does_not_block_the_rest(tmp_path, monkeypatch):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("{}", encoding="utf-8")
    (old_base / "secrets.dat").write_text("{}", encoding="utf-8")

    real_move = shutil.move

    def flaky_move(src, dst):
        if "settings.json" in str(src):
            raise OSError("locked")
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", flaky_move)

    moved = migrate_legacy_files(old_base, new_root)

    assert (old_base / "settings.json").exists()  # skipped, left alone
    assert not (old_base / "secrets.dat").exists()  # still moved
    assert {m["to"] for m in moved} == {str(new_root / "Secrets" / "secrets.dat")}


def test_manifest_records_what_moved(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("{}", encoding="utf-8")

    migrate_legacy_files(old_base, new_root)

    manifest = json.loads((new_root / "migration.json").read_text(encoding="utf-8"))
    assert len(manifest["events"]) == 1
    assert manifest["events"][0]["moved"] == [{
        "from": str(old_base / "settings.json"),
        "to": str(new_root / "Config" / "settings.json"),
    }]


def test_manifest_appends_across_two_migration_passes(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("{}", encoding="utf-8")
    migrate_legacy_files(old_base, new_root)

    (old_base / "secrets.dat").write_text("{}", encoding="utf-8")
    migrate_legacy_files(old_base, new_root)

    manifest = json.loads((new_root / "migration.json").read_text(encoding="utf-8"))
    assert len(manifest["events"]) == 2


def test_user_assets_root_triggers_migration_from_appdata(monkeypatch, tmp_path):
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    old_avg = appdata / "AVG Studio"
    old_avg.mkdir(parents=True)
    (old_avg / "settings.json").write_text('{"schema_version": 1}', encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))

    root = user_assets_root()

    assert root == home / "Documents" / "AVG Studio" / "UserAssets"
    assert (root / "Config" / "settings.json").read_text(encoding="utf-8") == '{"schema_version": 1}'
    assert not (old_avg / "settings.json").exists()
