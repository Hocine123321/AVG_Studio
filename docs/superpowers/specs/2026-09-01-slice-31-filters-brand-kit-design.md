# Slice 31 — Filters + Brand Kit: Design

**Goal:** close the two Director II gaps found by the legacy-parity
comparison (`.superpowers/sdd/progress.md`, 2026-09-01) that most affect
the final video's look: per-scene external filter overlays, and a brand
preset that actually changes the video instead of only labelling a
prompt line. First of four ranked sub-slices toward legacy video-result
parity (chapters/cards and caption animation styles follow later).

## 1. Filters — assets and catalog

Bundle the two legacy clips verbatim at `avg/assets/Filters/` (matching
the existing `avg/assets/` convention, not the vendor default's
`Assets/` casing — see §4):
`film_grain.mov`, `light_leak.mov` (ProRes 4444, `yuva444p10le`, ~28MB
total, copied byte-for-byte from `D:\AI_Video_Gen\Assets\Filters\`) plus
`Filters.metadata`, its own small format —
`name | description | file.mov` per line, `#`-commented — distinct from
`dynamic_options.txt`'s `## CATEGORY/## OPTION` format, so it needs its
own parser rather than reusing `avg.pipeline.prompts._parse_catalog`.
New `avg.pipeline.filters` module: `load_filter_catalog(path=None) ->
dict[str, FilterOption]` (`{name, description, file}`), lazy + cached
like `_load_bundled_catalog`, reading `avg/assets/Filters/Filters.metadata`
by default.

## 2. Filters — settings

Three new `SettingSpec`s, group `"filters"` (own group — this is a
Director II/AI Graphics concern, distinct from `"overlay"`'s card
colours), scope `"channel"`, `invalidates="director2"`:

- `filters_intensity` — `int`, default `50`, `0`-`100`. `0` disables the
  feature outright (catalog not even loaded into the prompt).
- `filters_allow` — `text`, default `""`. CSV of filter names; empty
  means every cataloged filter is eligible. CSV over a new multi-select
  setting type: no such type exists in the schema yet (`bool | choice |
  float | int | secret | text`) and one free-typed field matches how
  `visual_tone` already takes open text rather than adding UI surface
  for a two-filter catalog.
- `filters_block` — `text`, default `""`. Same shape, subtractive.

No manual per-scene pin (legacy's `Settings > ... > Filters: overrides`)
— YAGNI for a two-filter catalog; add if the catalog grows enough that
per-scene control earns its UI.

## 3. Filters — Director II selection

`build_director2_prompt` (`avg/pipeline/prompts.py`) gains a filters
section, built the same way the style bible already is: skipped
entirely when `filters_intensity == 0` or the catalog (after
allow/block) is empty, otherwise one line per eligible filter
(`name: description`) and a frequency instruction scaled off intensity
— `>=75` "freely", `>=40` "occasionally", else "sparingly" — mirroring
`Get-AiDirectionPlan`'s three-tier phrasing. The per-scene JSON schema
line gains `"filter":"..."` only when the section is present, so a
disabled/empty catalog produces byte-identical output to today's prompt
(no regression for anyone not using this).

`_normalize_direction_entry` (`avg/pipeline/phases/director.py`) gains a
`filter` field, validated against the catalog's own names (not a
hardcoded closed set like `_VALID_MOTION` — the catalog is data, not
vocabulary) with `"none"`/unrecognised degrading to `None`. Post-pass in
`Director2Phase.run`, after normalization: the same anti-clutter caps as
legacy (`Get-AiDirectionPlan`) — at most 25% of scenes filtered, at most
2 scenes per filter name, walked in scene order, excess nulled — so a
model that over-uses one filter doesn't saturate the video.
`direction_plan.json`'s scene shape gains `"filter"` (string or absent);
the render engine already reads exactly this field
(`avg/media/vendor/avg.py:2049` reads `sz.get(...)`-style per-scene
dicts feeding `_apply_filter` at line 1247 — confirmed still live, not
dead code, just unreachable without an upstream writer).

## 4. Filters — render wiring

`avg/media/vendor/avg.py`'s `_filters_root()` defaults to
`os.environ.get("AVG_FILTERS_DIR")` or a path two directories up from
the vendor file itself (`avg/media/Assets/Filters` here) — a relic of
the old repo's flat layout, and vendor/ is never edited. Rather than
placing assets at that default (case-colliding with `avg/assets/` on
Windows' case-insensitive filesystem, and outside the established
`avg/assets/` convention), `avg/media/environment.py` sets
`os.environ["AVG_FILTERS_DIR"]` to the packaged `avg/assets/Filters`
path before the render child spawns — the same place it already sets
`PATH`/`IMAGEIO_FFMPEG_EXE` (lines 99/109). One line, no vendor edit.

Packaging: confirm `avg/assets/Filters/*.mov` is picked up by
`tools/build.py`'s PyInstaller spec the same way `avg/assets/*.txt`
already is (plan-level check, not a design decision).

## 5. Brand Kit — palette actually drives colour

Today: `brand_preset` (dropdown) only feeds
`resolve_brand_preset_label` → a text line in the Director II prompt.
`overlay_accent1`/`accent2`/`highlight` are separate settings that
never read the preset. `dynamic_options.txt`'s `brand_presets` entries
already carry a full payload (`bg`/`panel`/`panel_stroke`/`text`/
`text2`/`accent1`/`accent2`/`highlight`/`font_path`) that nothing
parses beyond squeezing it whole into `CatalogOption.hint`.

Fix: `CatalogOption` gains a fourth field, `payload: dict[str, str]`
(parsed from the same `key=value;key=value` string `_OPTION_RE` already
captures as the third `|` segment — the format was compatible all
along, just unparsed past `hint`). `avg.media.overlays.resolve_brand`
gains a `preset_payload: dict[str, str] | None` parameter; its base
palette becomes the preset's payload (falling back to `od.DEFAULT_BRAND`
per-key when a key is absent) instead of always `od.DEFAULT_BRAND`.
`overlay_accent1`/`accent2`/`highlight` keep their exact current
semantics — explicit values still win — just layered on a real base
instead of a static one. Both call sites already resolve their brand
through this one function (`render_layers` → `_draw_overlay_assets` in
`director.py`; `preview.py`'s settings-preview path) per Slice 14's
"one resolver" rule, so both change together automatically.

Not ported: the settings-menu palette *editor*
(`Edit-BrandPresetPalette`, mutates `dynamic_options.txt` from the UI) —
a separate, later UI concern; palettes are catalog-authored for now,
same as every other catalog category in this codebase today.

## 6. Brand Kit — style bible biases Director II's choices

New `_BRAND_STYLE_DEFAULTS: dict[str, dict[str, str]]` in
`avg/pipeline/prompts.py`, one `{motion, transitions, grade}` entry per
existing preset id (`modern_dark`, `neon_cyber`, `warm_organic`,
`clean_light`, `luxury_gold`, plus fallback entries for
`wireframe_ink`/`tiktok_viral`/`vsauce_curiosity`/`academic_editorial`/
`tactical_terminal`, which legacy never had — new fallback profiles
written to match each preset's existing palette mood, not guessed
blind), ported from `$script:BRAND_STYLE_DEFAULTS`
(`D:\AI_Video_Gen\modules\utilities.ps1:2247`). A preset's catalog
payload may override any of the three keys directly (same
`motion=`/`transitions=`/`grade=` payload keys legacy supports), read
through the same `CatalogOption.payload` from §5 — no second parse.

`build_director_style_bible` (already the opening fragment of both
Director II's prompt and, per its own docstring, reused elsewhere)
replaces the current bare `brand: {label}` line with the full block:
brand name, motion feel, preferred transitions, grade look — same shape
as legacy's `$brandSection`. A preset with no motion/transitions/grade
resolvable (payload silent and id not in `_BRAND_STYLE_DEFAULTS`) falls
back to today's label-only line rather than fabricating a style.

## 7. Data/schema summary

| File | Change |
|---|---|
| `avg/assets/Filters/Filters.metadata`, `film_grain.mov`, `light_leak.mov` | New, bundled |
| `avg/pipeline/filters.py` | New — catalog loader |
| `avg/core/settings_schema.py` | +3 `SettingSpec`s (`filters_intensity`/`allow`/`block`) |
| `avg/pipeline/prompts.py` | `CatalogOption.payload`; filters section; `_BRAND_STYLE_DEFAULTS`; style bible rewrite |
| `avg/pipeline/phases/director.py` | Filter validation + anti-clutter cap in `Director2Phase.run` |
| `avg/media/overlays.py` | `resolve_brand(..., preset_payload=...)` |
| `avg/media/environment.py` | Sets `AVG_FILTERS_DIR` before child spawn |
| `avg/media/vendor/*` | **Untouched** |

`direction_plan.json` scenes gain an optional `"filter"` key —
additive, no migration needed (absent = today's behaviour, matches how
`_normalize_direction_entry` already treats every other optional field).

## 8. Testing

Backend: `avg.pipeline.filters` catalog parsing (comments/blanks
skipped, missing file tolerant, matching `_load_bundled_catalog`'s own
`OSError` handling); filters-section prompt text at each intensity
tier and at `0` (section absent, byte-identical prompt to pre-change);
`_normalize_direction_entry` filter validation + the anti-clutter cap
across a scene count large enough to trigger both the 25%-of-scenes and
2-per-name limits; `CatalogOption.payload` parsing round-trip;
`resolve_brand` precedence (payload key present / absent / explicit
setting override) for all three brand-driven colours; style-bible text
per preset, including a preset with no style-bible entry and no payload
override (falls back to label-only line, not a crash).

No frontend test runner (`CLAUDE.md`) — `tsc -b` + `oxlint` clean; no
new settings UI beyond what the existing `type="int"`/`type="text"`
field renderers already handle, so no new component.

## 9. Out of scope

- The settings-menu brand palette editor (§5).
- Per-scene manual filter pin/override (§2).
- New filter clips beyond the two legacy assets — the catalog format
  supports adding more later without a code change.
- Chapters/parts grouping, chapter/explainer takeover cards, image
  recycling, composite grids, caption animation styles — later ranked
  sub-slices.
