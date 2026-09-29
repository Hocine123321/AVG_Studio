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


def test_the_api_module_does_not_pull_the_pipeline_at_import_time():
    """`avg.pipeline.registry` costs ~190ms measured with `-X importtime`
    because it transitively imports every phase, and `avg.media.engine` and
    `avg.pipeline.runner` sit right next to it in that same chain. All of it
    is work nothing does until a project is actually created, resumed,
    rendered or listed - the library scan (which is what originally made
    `avg.config.legacy_import` heavy) already runs on a background thread,
    so none of this has business being on the startup path either.

    A prior version of this test only asserted `not hasattr(app_module,
    "import_all")` - true by construction (`import_all` was already a local
    import inside a function, never a module attribute) and therefore no
    evidence at all that the pipeline stayed out of `sys.modules`. This
    subprocess-imports `avg.api.app` in a clean interpreter and checks
    `avg.pipeline.registry` directly: `avg.pipeline.runner` imports it
    outright, and every phase module is imported BY it (see `avg.pipeline
    .registry`'s own docstring), so its absence is direct evidence the
    registry/runner/phase side of the chain was not pulled in at import
    time - not merely evidence about one module that happens to be unused.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import avg.api.app; print('avg.pipeline.registry' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", (
        "importing avg.api.app dragged in avg.pipeline.registry - that is "
        "the whole phase/provider/media chain, none of which startup should "
        f"pay for: {result.stdout!r}"
    )
