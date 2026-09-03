"""Suite-wide test configuration.

Two things this file exists for, both of them harness-level guarantees
that an individual test file must not be able to forget.

**The whole suite runs in replay mode.**
`avg.providers.transport.Transport` defaults to LIVE now, because the
shipped app has to make real API calls and a `replay` default meant it
never could (see that module's own comment). That moves responsibility
for the no-live-calls rule here, where it belongs: the harness asserts
it, session-wide and automatically, so a new test file cannot forget to
and quietly start spending real money on image or voice generation.

**The whole suite is pointed away from the real user profile.**
Every document this app owns resolves its own default location from
`%APPDATA%`, `%LOCALAPPDATA%` or `%USERPROFILE%` (Windows) - or
`$XDG_CONFIG_HOME`, `$XDG_CACHE_HOME` or `$XDG_DOCUMENTS_DIR` (Linux,
via `avg.config.platform_paths`) - at call time. `avg.config.settings`,
`avg.config.channels`, `avg.config.channel_defaults`,
`avg.config.secrets`, `avg.config.user_assets`, `avg.providers.models`
and `avg.media.thumbnails` all read one of these environment variables
themselves. `create_app`'s path overrides are how a test
*should* stay isolated, but they are opt-in, and a test that simply
omits one gets the developer's real `%APPDATA%/AVG Studio` (or, on
Linux, `~/.config/AVG Studio` / `~/Documents/AVG Studio`) with no
complaint - it reads their actual settings, and it can overwrite them.
That has to be impossible rather than merely discouraged, so the
environment the whole session resolves against is redirected here,
once, for BOTH platforms' variable names at the same time - a session
only runs on one platform, so only one set is ever actually consulted,
but setting both costs nothing and means this fixture never needs to
know which OS it's running under. Overriding the variables rather than
patching every `default_*_path()` function individually is deliberate:
it covers every future document automatically, including ones nobody
has written yet.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True, scope="session")
def _linux_secrets_key_is_fixed_not_a_live_keyring() -> Iterator[None]:
    """On Linux, `avg.config.secrets` sources its encryption key from the
    desktop's Secret Service via the `keyring` package (see that module's
    `_linux_encryption_key`) - a live D-Bus session this suite must never
    depend on, matching the "no live API calls in tests" rule already
    enforced for HTTP by `_replay_fixtures_everywhere` below. Patches the
    key-retrieval seam to a fixed, session-scoped in-memory key instead,
    so `avg.config.secrets`'s round-trip tests exercise real Fernet
    encrypt/decrypt/tamper-detection without a reachable GNOME
    Keyring/KWallet. A no-op on Windows, where DPAPI needs no session of
    its own beyond the one already running the test process.
    """
    if sys.platform == "win32":
        yield
        return
    from cryptography.fernet import Fernet

    import avg.config.secrets as secrets_module

    fixed_key = Fernet.generate_key()
    with patch.object(secrets_module, "_linux_encryption_key", lambda: fixed_key):
        yield


@pytest.fixture(autouse=True, scope="session")
def _replay_fixtures_everywhere() -> None:
    # `setdefault`, not assignment: a deliberate `AVG_HTTP_RECORD=1` run
    # (how fixtures get recorded in the first place) must still win, and
    # record mode is checked before replay in Transport.__init__.
    os.environ.setdefault("AVG_REPLAY_FIXTURES", "1")


@pytest.fixture(autouse=True, scope="session")
def _appdata_points_somewhere_disposable() -> Iterator[Path]:
    # Assignment, not `setdefault`: the whole point is to win over the
    # real value. Anything the suite resolves from these variables lands
    # under a temporary directory that goes away with the session. Sets
    # both the Windows names and their Linux/XDG equivalents
    # (avg.config.platform_paths) unconditionally - harmless on whichever
    # platform doesn't read the other set, and means this fixture never
    # has to branch on `sys.platform` itself.
    with tempfile.TemporaryDirectory(prefix="avg-tests-appdata-") as tmp:
        root = Path(tmp)
        roaming = root / "Roaming"
        local = root / "Local"
        home = root / "Home"
        documents = home / "Documents"
        videos = home / "Videos"
        roaming.mkdir()
        local.mkdir()
        home.mkdir()
        documents.mkdir()
        videos.mkdir()
        var_names = (
            "APPDATA",
            "LOCALAPPDATA",
            "USERPROFILE",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_DOCUMENTS_DIR",
            "XDG_VIDEOS_DIR",
        )
        previous = {name: os.environ.get(name) for name in var_names}
        os.environ["APPDATA"] = str(roaming)
        os.environ["LOCALAPPDATA"] = str(local)
        os.environ["USERPROFILE"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(roaming)
        os.environ["XDG_CACHE_HOME"] = str(local)
        os.environ["XDG_DOCUMENTS_DIR"] = str(documents)
        os.environ["XDG_VIDEOS_DIR"] = str(videos)
        try:
            yield root
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
