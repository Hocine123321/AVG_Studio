# Slice 25 — Ken Zoom intensity: Implementation Plan

Implements `docs/superpowers/specs/2026-08-14-slice-25-kenzoom-design.md`.
Read that first.

Verify: `.venv-build\Scripts\python.exe -m pytest tests -q` and
`.venv-build\Scripts\python.exe -m mypy avg`.

## Files touched

| File | Change |
|---|---|
| `avg/core/settings_schema.py` | New `SettingSpec(id="kb_intensity", ...)` |
| `avg/pipeline/phases/render.py` | `_render_kwargs` gains a `kb_intensity` branch |
| `tests/pipeline/test_phase_render.py` | +1 test, matching `_thread_kwargs`'s existing direct-unit-test style |

One task - the whole slice is a single, small, well-precedented
extension of an existing function.

---

## Task 1 — Wire `kb_intensity` through

**Files:** `avg/core/settings_schema.py`, `avg/pipeline/phases/render.py`,
`tests/pipeline/test_phase_render.py`

### Step 1.1 — failing test

Append to `tests/pipeline/test_phase_render.py`, directly after
`test_render_threads_setting_reaches_the_request` (same section, same
style - `_render_kwargs`'s other branches don't have direct unit tests
yet either, but `_thread_kwargs` right above this one does, and this is
the same shape of function):

```python
def test_kb_intensity_setting_reaches_the_request():
    """`RenderSettings`/`config.json` used to be the only source for this -
    a real channel setting today, same pattern `fps` already has.
    """
    from avg.pipeline.phases.render import _render_kwargs

    assert _render_kwargs({"kb_intensity": 60})["kb_intensity"] == 60
    assert _render_kwargs({"kb_intensity": 150})["kb_intensity"] == 150
    # Unset, wrong type, or a bool (which `isinstance(x, int)` would
    # otherwise accept - `bool` is an `int` subclass in Python) all defer
    # to `RenderRequest`'s own default, matching `fps`'s existing guard.
    assert "kb_intensity" not in _render_kwargs({})
    assert "kb_intensity" not in _render_kwargs({"kb_intensity": "a lot"})
    assert "kb_intensity" not in _render_kwargs({"kb_intensity": True})
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/pipeline/test_phase_render.py -q`
— fails, `_render_kwargs` doesn't look at `kb_intensity` yet (every
assertion against a present value fails; the three `not in` assertions
already pass, which is fine - they're there for symmetry with `fps`'s own
test shape once the branch exists, not because they'd currently fail).

### Step 1.2 — settings schema

In `avg/core/settings_schema.py`, replace:

```python
    SettingSpec(
        id="ken_burns",
        group="video",
        label="Ken Burns pan/zoom",
        help="Slowly pan and zoom on still images instead of holding them static.",
        type="bool",
        default=True,
        scope="channel",
        invalidates="director2",
    ),
    # --- per-phase text models (the mockup's Models table) ---
```

with:

```python
    SettingSpec(
        id="ken_burns",
        group="video",
        label="Ken Burns pan/zoom",
        help="Slowly pan and zoom on still images instead of holding them static.",
        type="bool",
        default=True,
        scope="channel",
        invalidates="director2",
    ),
    SettingSpec(
        id="kb_intensity",
        group="video",
        label="Ken Zoom intensity",
        help="How far the pan/zoom drifts from each scene's own designed motion (0 = none, 100 = default, up to 150 = stronger). Applied at render time, on top of whatever motion Director already chose per scene.",
        type="int",
        default=100,
        scope="channel",
        invalidates="render",
    ),
    # --- per-phase text models (the mockup's Models table) ---
```

(`invalidates="render"`, not `ken_burns`'s own `"director2"` - traced in
the design spec: intensity scales an already-decided motion at render
time, it never needs scenes re-planned.)

### Step 1.3 — `_render_kwargs`

In `avg/pipeline/phases/render.py`, replace:

```python
    if isinstance(settings.get("ken_burns"), bool):
        kwargs["ken_burns"] = settings["ken_burns"]
    codec = settings.get("render_codec")
```

with:

```python
    if isinstance(settings.get("ken_burns"), bool):
        kwargs["ken_burns"] = settings["ken_burns"]
    kb_intensity = settings.get("kb_intensity")
    if isinstance(kb_intensity, int) and not isinstance(kb_intensity, bool):
        kwargs["kb_intensity"] = kb_intensity
    codec = settings.get("render_codec")
```

Also update the function's own docstring, which currently lists the
settings this function exists to NOT let become dead switches - add this
one to that list. Replace:

```python
    Until this existed, `fps`, `ken_burns`, `render_codec` and
    `render_engine` were dead switches in the catalogue: the settings screen
    rendered them, saved them, and no render ever read them - the values
    actually used came from the LEGACY `config.json`, which the user has no
    way to edit from this app.
```

with:

```python
    Until this existed, `fps`, `ken_burns`, `render_codec` and
    `render_engine` were dead switches in the catalogue: the settings screen
    rendered them, saved them, and no render ever read them - the values
    actually used came from the LEGACY `config.json`, which the user has no
    way to edit from this app. `kb_intensity` (Slice 25) was the same
    story, added to this function rather than given a second one.
```

### Step 1.4 — verify

```
.venv-build\Scripts\python.exe -m pytest tests/pipeline/test_phase_render.py -q
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
```

### Step 1.5 — commit

`feat: wire kb_intensity through to the render request — Slice 25`

---

## Task 2 — Update progress ledger

### Step 2.1

Update `.superpowers/sdd/progress.md`: Slice 25 complete, the dead-switch
root cause and its one-function fix noted.

### Step 2.2 — commit

`docs: Slice 25 complete — Ken Zoom intensity`
