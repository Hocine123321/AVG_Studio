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
`%APPDATA%` or `%LOCALAPPDATA%` at call time - `avg.config.settings`,
`avg.config.channels`, `avg.config.channel_defaults`,
`avg.config.secrets`, `avg.core.model_prefs`, `avg.providers.models`
and `avg.media.thumbnails` all read the environment variable
themselves. `create_app`'s path overrides are how a test *should* stay
isolated, but they are opt-in, and a test that simply omits one gets
the developer's real `%APPDATA%/AVG Studio` with no complaint - it
reads their actual settings, and it can overwrite them. That has to be
impossible rather than merely discouraged, so the environment the whole
session resolves against is redirected here, once. Overriding the two
variables rather than patching seven `default_*_path()` functions is
deliberate: it covers every future document automatically, including
ones nobody has written yet.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="session")
def _replay_fixtures_everywhere() -> None:
    # `setdefault`, not assignment: a deliberate `AVG_HTTP_RECORD=1` run
    # (how fixtures get recorded in the first place) must still win, and
    # record mode is checked before replay in Transport.__init__.
    os.environ.setdefault("AVG_REPLAY_FIXTURES", "1")


@pytest.fixture(autouse=True, scope="session")
def _appdata_points_somewhere_disposable() -> Iterator[Path]:
    # Assignment, not `setdefault`: the whole point is to win over the
    # real value. Anything the suite resolves from these two variables
    # lands under a temporary directory that goes away with the session.
    with tempfile.TemporaryDirectory(prefix="avg-tests-appdata-") as tmp:
        root = Path(tmp)
        roaming = root / "Roaming"
        local = root / "Local"
        roaming.mkdir()
        local.mkdir()
        previous = {
            "APPDATA": os.environ.get("APPDATA"),
            "LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
        }
        os.environ["APPDATA"] = str(roaming)
        os.environ["LOCALAPPDATA"] = str(local)
        try:
            yield root
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
