# Slice 25 — Ken Zoom intensity: Design

**Goal:** a real, working `kb_intensity` setting that actually reaches the
render, replacing the current dead switch.

## 0. The actual mathematical meaning of "100" (traced, not assumed)

The vendored render engine (`avg/media/vendor/avg.py`, protected, read
but never edited) computes each scene's Ken Burns motion in two steps:

1. A per-scene-type base motion is chosen - `(kb_from, kb_to)` pairs like
   `(1.0, 1.08)`, `(1.0, 1.05)`, `(1.04, 1.0)`, or whatever
   `_resolve_motion` derives from Director's own motion spec for that
   scene.
2. `kb_intensity` (normalized from the raw 0-150 integer percent to a
   0.0-1.5 float, clamped) scales how far that base motion deviates from
   1.0 (no zoom at all): `kb_from = 1.0 + (kb_from - 1.0) * kb_intensity`,
   same for `kb_to`.

So `100` is not "zoom to 100% / lose the whole frame" - it's a **neutral
multiplier**: `(kb_from - 1.0) * 1.0` changes nothing, so intensity 100
means "use each scene's base motion exactly as designed, unscaled." `0`
flattens every scene to no motion at all (`kb_from == kb_to == 1.0`,
functionally the same visual result as turning the separate `ken_burns`
toggle off, just via a different path - harmless overlap, not a
conflict). `150` amplifies the base motion by 50%. The base motions
themselves (up to `1.0 → 1.08`, an ~7.4% crop by the end of a scene) are
what the backlog item's "loses too much of the frame" is actually
describing - not the number 100 meaning anything about "100% zoom."

## 1. Why it's a dead switch today, traced to the exact gap

`kb_intensity` and `kb_mode` are `RenderSettings` fields
(`avg/config/settings.py`) - but that class's own docstring says exactly
what it is: "Render settings extracted from **legacy app config**,"
reading `config.json`, the old PowerShell app's file, not the current
per-channel `SettingSpec` catalogue. There is no `SettingSpec(id=
"kb_intensity", ...)` at all today - only `ken_burns` (the on/off
toggle) has one.

There's a direct, exact precedent for the fix already in the codebase:
`avg.pipeline.phases.render._render_kwargs`'s own docstring: *"Until this
existed, `fps`, `ken_burns`, `render_codec` and `render_engine` were dead
switches in the catalogue: the settings screen rendered them, saved
them, and no render ever read them - the values actually used came from
the LEGACY `config.json`."* That function is precisely the fix for this
exact class of bug, already applied to four other settings. `kb_intensity`
was simply never added to it. `RenderRequest`
(`avg.core.models.RenderRequest.kb_intensity: int = 100`) and
`MediaEngine` already forward whatever `_render_kwargs` gives them all
the way to the vendored render call - the plumbing below `_render_kwargs`
is already complete and correct. This is a one-function-extension fix,
not new plumbing.

## 2. The fix

- New `SettingSpec(id="kb_intensity", group="video", type="int",
  default=100, scope="channel", invalidates="render")` -
  `type="int"` and `default=100` both matching `RenderRequest`'s own
  field exactly, not introducing a float↔int conversion `_render_kwargs`
  would otherwise have to do. `invalidates="render"`, not `"director2"`
  (`ken_burns`'s own value) - traced: intensity is applied entirely
  inside the render-time compositing function, after Director II has
  already decided each scene's base motion; changing it never needs
  scenes re-planned, only re-rendered, exactly like `fps`.
- `_render_kwargs` gains one more branch, `settings.get("kb_intensity")`,
  the same shape as its existing `fps` branch (validate it's a real int,
  not a bool, in range, then pass it through) - not a new pattern.

`kb_mode` (`dynamic`/`flat`) stays exactly as it is - not requested by
this item, and there's a pre-existing, unrelated inconsistency between
`RenderSettings`'s legacy-bridge default (`"flat"`) and the vendored
function's own signature default (`"dynamic"`) that this slice doesn't
touch or need to resolve.

## 3. Testing

- `_render_kwargs`: `kb_intensity` present and a valid int → passed
  through; absent, non-int, or a bool → not added (falls through to
  `RenderRequest`'s own default), matching the existing `fps` tests'
  shape exactly.
- Schema: the new `SettingSpec` round-trips through `GET
  /api/settings/schema` like every other entry (covered generically by
  the existing structural schema tests - no bespoke test needed for that
  part).

## 4. Out of scope

- `kb_mode` - not requested, and its pre-existing default inconsistency
  is unrelated to this item.
- A Slider UI treatment - the Settings-selectors slice's concern; the
  existing generic settings UI already renders an `int` setting
  correctly without one.
