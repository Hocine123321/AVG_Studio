"""Startup must not pay for what it does not use.

A structural test, not a benchmark: it asserts that importing the app does
not drag in the heavy optional libraries. Timing assertions would be flaky
on a loaded machine; "was this module imported at all" is exact.

The measurement that prompted it: `python -X importtime -c "import
avg.shell"` totalled ~4.33s, of which `edge_tts` (and the `aiohttp` it
pulls in) was ~2.0s - imported at module scope by the voice provider, which
the phase registry imports transitively from the API app. Every launch paid
for a TTS library nothing touches until the voice phase runs.
"""

from __future__ import annotations

import subprocess
import sys

_CODE = "import sys; import avg.shell; print('edge_tts' in sys.modules, 'aiohttp' in sys.modules)"


def test_importing_the_app_does_not_import_edge_tts():
    result = subprocess.run(
        [sys.executable, "-c", _CODE], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False False", (
        "importing avg.shell dragged in edge_tts/aiohttp again - that is ~2s "
        f"of startup for a library the voice phase imports on demand: {result.stdout!r}"
    )
