"""Suite-wide test configuration.

Two things this file exists for, both of them harness-level guarantees
that an individual test file must not be able to forget.

**The whole suite runs in replay mode.**
`avg.providers.transport.Transport` defaults to LIVE now, because the
shipped app has to make real API calls and a `replay` default meant it
never could (see that module's own comment). That moves responsibility
for the no-live-calls rule here, where it belongs: the harness asserts
it, session-wide and automatically, so a new test file cannot forget to
and quietly start spending real money on image or voice generation. The
same rule extends to `avg.config.secrets`'s keyring-backed encryption key
(see `_secrets_key_is_fixed_not_a_live_keyring` below) - a live D-Bus
Secret Service session is exactly as much an external dependency as a
live HTTP API, and the suite must never depend on one being reachable.

**The whole suite is pointed away from the real user profile.**
Every document this app owns resolves its own default location from
`$XDG_CONFIG_HOME`, `$XDG_CACHE_HOME`, `$XDG_DOCUMENTS_DIR` or
`$XDG_VIDEOS_DIR` (via `avg.config.platform_paths`) at call time.
`avg.config.settings`, `avg.config.channels`,
`avg.config.channel_defaults`, `avg.config.secrets`,
`avg.config.user_assets`, `avg.providers.models` and
`avg.media.thumbnails` all read one of these environment variables
themselves. `create_app`'s path overrides are how a test *should* stay
isolated, but they are opt-in, and a test that simply omits one gets the
developer's real `~/.config/AVG Studio` / `~/Documents/AVG Studio` with
no complaint - it reads their actual settings, and it can overwrite them.
That has to be impossible rather than merely discouraged, so the
environment the whole session resolves against is redirected here, once.
Overriding the variables rather than patching every `default_*_path()`
function individually is deliberate: it covers every future document
automatically, including ones nobody has written yet.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True, scope="session")
def _secrets_key_is_fixed_not_a_live_keyring() -> Iterator[None]:
    """`avg.config.secrets` sources its encryption key from the desktop's
    Secret Service via the `keyring` package (see that module's
    `_linux_encryption_key`) - a live D-Bus session this suite must never
    depend on, matching the "no live API calls in tests" rule this file's
    docstring describes. Patches the key-retrieval seam to a fixed,
    session-scoped in-memory key instead, so `avg.config.secrets`'s
    round-trip tests exercise real Fernet encrypt/decrypt/tamper-detection
    without a reachable GNOME Keyring/KWallet.
    """
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
def _xdg_points_somewhere_disposable() -> Iterator[Path]:
    # Assignment, not `setdefault`: the whole point is to win over the
    # real value. Anything the suite resolves from these variables lands
    # under a temporary directory that goes away with the session.
    with tempfile.TemporaryDirectory(prefix="avg-tests-xdg-") as tmp:
        root = Path(tmp)
        config = root / "config"
        cache = root / "cache"
        home = root / "home"
        documents = home / "Documents"
        videos = home / "Videos"
        config.mkdir()
        cache.mkdir()
        home.mkdir()
        documents.mkdir()
        videos.mkdir()
        var_names = ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DOCUMENTS_DIR", "XDG_VIDEOS_DIR")
        previous = {name: os.environ.get(name) for name in var_names}
        os.environ["XDG_CONFIG_HOME"] = str(config)
        os.environ["XDG_CACHE_HOME"] = str(cache)
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
