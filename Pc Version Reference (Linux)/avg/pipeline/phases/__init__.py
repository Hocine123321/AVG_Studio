"""The real per-phase implementations that `avg.pipeline.registry.REGISTRY`
wires in, one module per phase (`brief.py`, `script.py`, `director1.py`,
and later tasks' `visuals.py` / `voice.py` / `director2.py` / `captions.py`).

`_shared.py` and `_parsing.py` are private helpers used by more than one
phase in this package - resolving a `PhaseContext` into
`avg.pipeline.prompts.CreativeInputs`, the `<project>/script/` file
conventions, and defensive parsing of a text model's reply - kept here
rather than duplicated three times or bolted onto `avg.pipeline.prompts`
(that module builds prompt strings only; it has no opinion on a model's
response).
"""
