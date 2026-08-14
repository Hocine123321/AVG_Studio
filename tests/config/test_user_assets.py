from pathlib import Path

from avg.config.user_assets import user_assets_root


def test_default_root_is_under_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert user_assets_root() == tmp_path / "Documents" / "AVG Studio" / "UserAssets"


def test_falls_back_to_home_when_userprofile_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert user_assets_root() == tmp_path / "Documents" / "AVG Studio" / "UserAssets"
