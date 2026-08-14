"""The suite cannot reach the developer's real `%APPDATA%/AVG Studio`.

`create_app`'s path overrides are the *intended* isolation mechanism, but
they are opt-in: a test that omits one silently gets the real user profile,
reads the developer's actual settings, and can overwrite them. Slice 12's
review found exactly that hole around `channel_defaults.json`, where seven
test modules build a client without the override and any of them could have
written to the real document the moment one of them touched
`settings_source="global"`.

The fix is in `tests/conftest.py`, which repoints `%APPDATA%` and
`%LOCALAPPDATA%` for the whole session. These tests are what stops that
fixture being quietly deleted or narrowed later: they assert the property
the fixture exists to provide, for every document the app owns, rather than
asserting the fixture's implementation.
"""

from __future__ import annotations

import os
from pathlib import Path

from avg.config import channel_defaults, channels, secrets, settings, user_assets
from avg.core import model_prefs
from avg.media import thumbnails
from avg.providers import models


def _real_app_folders() -> list[Path]:
    """Where this app's own documents would live on the developer's machine.

    Read from `Path.home()` rather than the environment, because the
    environment is precisely what the fixture has redirected. The check is
    against `AVG Studio` specifically rather than against `AppData` as a
    whole, because the redirect target is itself a `TemporaryDirectory`,
    and on Windows that lands under `%LOCALAPPDATA%\\Temp` - inside AppData
    but nowhere near the real documents. What must be unreachable is the
    app's own folder, not the whole profile.
    """
    home = Path.home()
    return [
        home / "AppData" / "Roaming" / "AVG Studio",
        home / "AppData" / "Local" / "AVG Studio",
        home / "Documents" / "AVG Studio",
    ]


def test_every_default_document_path_avoids_the_real_profile() -> None:
    resolved = {
        "settings.json": settings.default_settings_path(),
        "channels/": channels.default_channels_dir(),
        "channel_defaults.json": channel_defaults.default_channel_defaults_path(),
        "secrets.dat": secrets.default_secrets_path(),
        "model_prefs.json": model_prefs.default_prefs_path(),
        "model_cache.json": models.default_cache_path(),
        "thumbs/": thumbnails.default_cache_dir(),
        "UserAssets/": user_assets.user_assets_root(),
    }
    real_folders = _real_app_folders()
    for label, path in resolved.items():
        for folder in real_folders:
            assert folder != path and folder not in path.parents, (
                f"{label} resolves to {path}, inside the developer's real "
                f"AVG Studio folder at {folder}. The session fixture in "
                f"tests/conftest.py that redirects %APPDATA% has stopped "
                f"working, and this suite can now read and overwrite their "
                f"own settings, channels and secrets."
            )


def test_the_redirected_profile_is_writable() -> None:
    """Redirecting the variables is only useful if the target actually works.

    A redirect that points at a directory nothing can create would turn every
    default-path test into a permission error rather than an isolated pass,
    so assert the round trip rather than just the string.
    """
    target = settings.default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"schema_version": 1, "values": {}}', encoding="utf-8")
    assert target.read_text(encoding="utf-8").startswith("{")
    target.unlink()


def test_all_variables_are_set() -> None:
    # Every resolver falls back to `Path.home() / "AppData" / ...` (or, for
    # avg.config.user_assets, `Path.home() / "Documents" / ...`) when its
    # variable is unset, which would defeat the redirect entirely, so the
    # fixture must leave all three populated rather than deleted.
    assert os.environ.get("APPDATA")
    assert os.environ.get("LOCALAPPDATA")
    assert os.environ.get("USERPROFILE")
