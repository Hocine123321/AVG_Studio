# Slice 31 (sub-slice 2) — Image Recycling: Design (Linux port)

**Goal:** let a scene reuse a hand-picked, verified image instead of
generating one — the legacy app's second-ranked video-result gap. No API
call, no cost, and (per legacy's own reasoning) often a genuinely better
image than a fresh generation for the scenes it fits. Composite multi-scene
grids, the other half of this sub-slice's original ranking, is explicitly
**out of scope** — dropped from the parity effort entirely, not deferred
(decision made 2026-09-02, recorded in `.superpowers/sdd/progress.md`).

**Linux port note (2026-09-06):** ported from the `main`/`development`
(Windows) design of the same name, unchanged in substance. The only
touchpoint that differs at all is §1's storage location — `avg.config.
user_assets.user_assets_root()` (which this design already calls rather
than hardcoding a path) is built from `avg.config.platform_paths.
documents_root()` on this branch instead of a literal `%USERPROFILE%`
join, so this design's own code was already platform-correct without
change; only the *prose* below describing where the folder lives has
been reworded to not overstate a Windows-only path as if it were
universal. `avg/pipeline/`, `avg/core/` — everything else this design
touches — are byte-identical between branches, confirmed by diff.

Spans two phases: Script (Phase 1) decides, per scene, "reuse pool image
#k" or "write a real prompt"; Visuals (Phase 3) resolves that decision —
copy for a reuse, generate for everything else. Ported from
`Invoke-SceneSegmentation`'s B2.19.1 pool-stitching path and the B2.15
recycle-copy block in `D:\AI_Video_Gen\modules\visuals.ps1`, both read in
full for this design (no behaviour here is guessed from the README).

## 1. The pool: storage and reading

A user-curated, GLOBAL (not per-project, not per-channel) library —
matches legacy's `Assets\Re-usable Images\images.json` exactly in spirit:
hand-maintained, no in-app editor either app version has ever had (checked
`settings_menu.ps1` — only `recycle_enabled`/`recycle_ratio` are
menu-editable there; the pool itself is edited by hand). Porting an
in-app pool manager would be scope beyond what legacy ever offered — not
this sub-slice.

Location: `avg.config.user_assets.user_assets_root() / "ReusableImages"`
(new sibling of `Config/`/`Secrets/`/`Logs/`/`Projects/`) — on this
branch, `~/Documents/AVG Studio/UserAssets/ReusableImages/` (or wherever
`$XDG_DOCUMENTS_DIR` points), on Windows,
`%USERPROFILE%\Documents\AVG Studio\UserAssets\ReusableImages\`; either
way, `default_reusable_images_dir()` is the one place that resolves it,
so nothing else in this design ever needs to know or care which. Manifest
at `ReusableImages/images.json`:

```json
{"images": [{"file": "sunset.jpg", "description": "Warm golden-hour sky over a calm ocean horizon."}]}
```

`file` is relative to `ReusableImages/`. New `avg.config.recycle_pool`
module: `read_recyclable_pool(root: Path | None = None) -> list[dict]`,
`[{"file": <absolute path str>, "description": str}]`, in manifest order,
same tolerance as `Get-RecyclableImagePool`: missing manifest/dir → `[]`;
an entry missing `file`/`description`, or whose file doesn't exist on
disk → skipped, never raised.

## 2. Settings

Two, matching legacy's own pair exactly (no per-scene manual override —
legacy's `Settings > Output > AI Graphics > Filters > overrides` equivalent
for recycling doesn't exist even in legacy):

- `recycle_enabled` — `bool`, default `True`, group `"images"`, scope
  `"channel"`, `invalidates="script"`. An empty pool already makes this a
  no-op regardless, matching legacy's own "feature is a no-op" framing for
  a missing pool.
- `recycle_ratio` — `int`, default `70`, group `"images"`, scope
  `"channel"`, `invalidates="script"`. `0` = off, `1`-`99` = the model's
  own judgement under a soft-guided cap, `100` = a hard contract — every
  scene reuses, Visuals generates nothing.

`invalidates="script"`, not `"visuals"`: the setting changes what Script
*writes* (`scenes.json`'s `image_prompt` values); Visuals re-running is a
downstream consequence of Script's output changing, the same relationship
`overlay_*` has with `director2` today.

## 3. Script phase: per-run pool snapshot

At the top of Pass 2 (before building the segmentation prompt),
`ScriptPhase.run` reads the live pool
(`avg.config.recycle_pool.read_recyclable_pool`) and writes
`<project>/script/recycle_pool.json` — `[{"index": 1, "file": ..., 
"description": ...}, ...]`, 1-indexed, same shape and same reason as
legacy's `Write-RecyclePoolSnapshot`: so a `[RECYCLE #k]` index resolves to
the *exact* image Script saw, even if the global pool changes before
Visuals runs later in the same pipeline. Written fresh on every Script run
(first run or a settings-triggered re-run) — never treated as
create-once, matching how the global pool itself can change between runs.

## 4. Script phase: the segmentation prompt becomes pool-aware

`build_scene_segmentation_prompt` gains `recycle_pool: list[dict] | None =
None` (the snapshot's own `{"index","description"}` shape — no file paths
reach the model) and `recycle_ratio: int = 0`. Mode is derived inside the
function, not passed in as a separate flag (simpler than legacy's
3-parameter `RecycleMode`/`Pool`/`Ratio`, same behaviour):
`"off"` when `recycle_pool` is empty/`None` or `recycle_ratio <= 0`,
`"all"` at `>= 100`, else `"partial"`.

`"off"` mode's prompt is **byte-identical** to today's — pool wording only
appears when there's something to stitch, same guarantee Task 3 of
sub-slice 1 gave the filters section. `"all"`/`"partial"` add, verbatim
from `Invoke-SceneSegmentation`'s own three-way `$jobLine`/`$imageRule`
wording (job line, an injected `=== RE-USABLE IMAGE POOL (#k : 
description) ===` section, the per-mode image-line instruction, and — pool
active only — `Only use image numbers (#k) that appear in the pool above`
+ `The same pool image MAY serve more than one scene`), and the schema
example's Image line becomes `<the image prompt, or [RECYCLE #k]>` instead
of `<the image prompt>`.

## 5. Script phase: normalising the model's picks

After `parse_scene_blocks` returns and the existing verbatim-narration
invariant passes (recycling only ever touches `image_prompt`, never
`text` — the invariant is unaffected either way), a new pass over the
parsed scenes, ported from `Invoke-SceneSegmentation`'s own post-parse
block:

- Loose match, strict rewrite: `\[?\s*recycle\s*#?\s*(\d+)\s*\]?`
  (case-insensitive) against `image_prompt`, only when pool mode is
  active. A match with `1 <= k <= len(pool)` rewrites `image_prompt` to
  the canonical `"[RECYCLE #{k}]"` (so a model that wrote "Use [RECYCLE
  #3]" or "[recycle #3]" still resolves cleanly in Visuals, which will
  only ever match the canonical form). An out-of-range `k` is dropped
  (token stripped, remaining prose — if any — kept), same as legacy.
- `"all"` mode: any scene *still* without a valid pick after the above is
  force-filled by cycling the pool round-robin
  (`k = (n % pool_count) + 1`) — ratio 100 is a hard contract, so this
  always succeeds when the pool is non-empty (which "all" mode requires
  to have been entered in the first place).
- `"partial"` mode: `used = count of scenes with a valid pick`,
  `cap = floor(total_scenes * ratio / 100)`. `used > cap` is logged via
  `ctx.emit(ProgressEvent(...))` (informational — nothing is broken, a
  keener-than-guided model is not a degradation) and otherwise left alone
  — legacy's own reasoning applies unchanged: trimming a `[RECYCLE #k]`
  after the fact would leave that scene with no image prompt at all.
- A scene left with neither a valid pick nor any prose needs no new
  handling — `VisualsPhase.run`'s existing `if not prompt: prompt =
  f"{ctx.project.title}, scene {i}"` fallback already covers it.

## 6. Visuals phase: resolving a recycled scene

Before a scene's job is queued for generation, its `image_prompt` is
checked against the canonical token pattern (`^\[RECYCLE #(\d+)\]$` —
strict here; Script already did the loose-match normalisation, Visuals
never needs to). A match resolves against the *project's own*
`recycle_pool.json` snapshot (never the live global pool — Script's
snapshot is the contract), by 1-indexed position:

- File exists and passes a real-image check (`PIL.Image.open(...).verify()`
  — legacy's `Test-ValidImageFile`, same reasoning: a corrupt or
  non-image pool file must fall back to generation, never poison a
  render) → copied verbatim (`shutil.copy2`, matching `Copy-Item -Force`)
  to `images/{i:03d}.png`, no provider call, recorded as a normal
  successful `_SceneResult`. The source's own format is irrelevant — like
  legacy, the file is copied byte-for-byte regardless of extension; the
  render already aspect-fits every image, generated or recycled.
- Snapshot missing/index out of range/file gone/fails validation → falls
  back to generation, using the pool entry's own `description` as the
  prompt when available (richer than a blank scene, same choice legacy
  makes), else the existing generic title fallback. This is a **decode
  step, not a network call** — happens before jobs are queued, so a
  recycle failure costs nothing but degrades to a normal queued job, never
  a skipped scene.

Composite grids' one interaction point with recycling in legacy — forcing
`composite_layout` to `1x1` at ratio 100 — has nothing to force here:
composite grids don't exist in this codebase and are staying that way (see
Goal). No code needed for that seam.

## 7. Data/schema summary

| File | Change |
|---|---|
| `avg/config/user_assets.py` | +`default_reusable_images_dir()` |
| `avg/config/recycle_pool.py` | New — `read_recyclable_pool()` |
| `avg/core/settings_schema.py` | +2 `SettingSpec`s (`recycle_enabled`, `recycle_ratio`) |
| `avg/pipeline/phases/writing.py` | `ScriptPhase.run`: pool snapshot, pick normalisation, contract enforcement |
| `avg/pipeline/prompts.py` | `build_scene_segmentation_prompt` gains `recycle_pool`/`recycle_ratio` |
| `avg/pipeline/phases/visuals.py` | `VisualsPhase.run`: recycled-job resolution before queuing |
| `avg/media/vendor/*` | **Untouched** — recycling never reaches the render engine; it only changes what lands in `images/` before render even starts |

`<project>/script/recycle_pool.json` — new, additive, absent when
`recycle_enabled` is off or the pool is empty (matches every other
optional per-project artifact in this codebase).

## 8. Testing

Backend: `read_recyclable_pool` tolerance (missing dir/manifest, missing
keys, non-existent file, well-formed entries, in-order); settings
defaults/`invalidates`; segmentation prompt at each mode (`off` is
byte-identical to today, `partial`/`all` wording, schema line swap);
pick-normalisation (loose match variants, out-of-range drop, canonical
rewrite); `"all"` force-fill exhausting/cycling a small pool;
`"partial"` soft-cap emits an event but never trims; Visuals resolving a
valid pick (copy, no provider call, successful `_SceneResult`), an
out-of-range/missing/corrupt pick (falls back to generation with the pool
description as the prompt), and a scene with no pool involvement at all
(unaffected, current behaviour). No frontend test runner (`CLAUDE.md`) —
no new settings UI beyond the existing `bool`/`int` field renderers.

## 9. Out of scope

- Composite multi-scene grids — dropped entirely, not this sub-slice or
  any later one (see Goal).
- An in-app pool manager (add/remove/describe images) — legacy never had
  one either; the manifest is hand-edited.
- Per-scene manual recycle overrides — legacy doesn't have this for
  recycling (unlike Filters, which does have `overrides`, already out of
  scope for that sub-slice too).
- Chapters/takeover cards, caption animation styles — later ranked
  sub-slices.
