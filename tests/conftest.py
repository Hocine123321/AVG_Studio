"""Suite-wide test configuration.

The one thing this file exists for: **the whole suite runs in replay
mode.**

`avg.providers.transport.Transport` defaults to LIVE now, because the
shipped app has to make real API calls and a `replay` default meant it
never could (see that module's own comment). That moves responsibility
for the no-live-calls rule here, where it belongs: the harness asserts
it, session-wide and automatically, so a new test file cannot forget to
and quietly start spending real money on image or voice generation.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _replay_fixtures_everywhere() -> None:
    # `setdefault`, not assignment: a deliberate `AVG_HTTP_RECORD=1` run
    # (how fixtures get recorded in the first place) must still win, and
    # record mode is checked before replay in Transport.__init__.
    os.environ.setdefault("AVG_REPLAY_FIXTURES", "1")
