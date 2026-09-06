# Slice 31 — Filters + Brand Kit: Design (Linux port)

**Goal:** close the two Director II gaps found by the legacy-parity
comparison (`.superpowers/sdd/progress.md`, 2026-09-02) that most affect
the final video's look: per-scene external filter overlays, and a brand
preset that actually changes the video instead of only labelling a
prompt line. First of four ranked sub-slices toward legacy video-result
parity (chapters/cards and caption animation styles follow later).

**Linux port note (added 2026-09-06):** this design was originally
written against `main`/`development` (Windows) and is ported here
unchanged in substance. `avg/pipeline/`, `avg/core/`, and
`avg/media/overlays.py` — everything this design actually touches —
are byte-identical between branches; confirmed directly (`diff` against
a fresh clone of both), not assumed. The only two things adjusted below
are §1's asset-location reasoning (Linux filesystems are case-sensitive,
so the specific collision risk the Windows version cites doesn't apply
here — the underlying reason to still avoid the vendor's default path
does) and §4's packaging note (`tools/build.py`'s PyInstaller step has
no Linux equivalent; this branch packages via
`packaging/flatpak/io.github.Hocine123321.AVGStudio.yml`'s `pip install
.` instead — which turns out to matter a lot more than a passing
mention, see §4). Everything else applies exactly as written. See
`docs/linux-port/WINDOWS_VS_LINUX.md` for this platform's own general
Windows/Linux translation notes.

## 1. Filters — assets and catalog

Bundle the two legacy clips verbatim at `avg/assets/Filters/` (matching
the existing `avg/assets/` convention):
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
the old repo's flat layout (predates this codebase's `avg/media/`
split entirely), and vendor/ is never edited. That default is simply
the wrong directory regardless of platform — it isn't a sibling of
`avg/assets/` at all, it's a level deeper, under `avg/media/`. (The
Windows version of this design also flags a same-directory-different-
case collision risk between `avg/assets/` and `avg/Assets/`; Linux
filesystems are case-sensitive, so that specific risk doesn't apply
here — the two would simply be different, non-colliding paths. The
underlying reason to still not rely on the vendor's own default path
resolution is unaffected: it resolves to the wrong directory either
way.) `avg/media/environment.py` sets `os.environ["AVG_FILTERS_DIR"]`
to the packaged `avg/assets/Filters` path before the render child
spawns — the same place it already sets `PATH`/`IMAGEIO_FFMPEG_EXE`
(`prepend_to_path()`, lines 99/109 on this branch too — confirmed
identical). One line, no vendor edit.

**Packaging (Linux): a real, pre-existing gap found while writing this
port, not a "confirm" checkbox.** This branch has no PyInstaller step at
all — packaging is `packaging/flatpak/io.github.Hocine123321.AVGStudio.yml`
running a plain `pip3 install --prefix=/app .` (`docs/linux-port/
WINDOWS_VS_LINUX.md`'s "Packaging shape" row). Reproduced directly in this
sandbox: a real, non-editable `pip install .` into a throwaway prefix
does **not** copy `avg/assets/` at all — not the two new filter clips this
design adds, but the *already-existing* `dynamic_options.txt`/
`personality.txt` too. `pyproject.toml`'s `[tool.setuptools.packages.find]`
only tells setuptools which `.py`-package directories exist; it says
nothing about non-Python data files, and no `package_data`/
`include_package_data`+`MANIFEST.in` entry exists to say so. Confirmed by
checking a real install's `site-packages/avg/` tree: `assets/` is absent
entirely.

This is not something this sub-slice introduced and not something it can
responsibly paper over by only fixing its own two files — every caller of
`avg.pipeline.personality.assets_dir()` (brand presets, video tones,
personality presets, and now filters) is already silently broken in any
real Flatpak build made from this branch as it stands today, catalog
reads degrading to empty rather than crashing (per `_load_bundled_catalog`'s
own tolerant `OSError` handling) — which makes it worse to leave
unfixed, not better: a silent empty catalog gives no error to notice.
Fixing `pyproject.toml`'s packaging to include `avg/assets/**` (and,
once this sub-slice lands, its own `Filters/*.mov`) is a prerequisite
for this design's assets to reach a packaged build at all, and belongs
in this sub-slice's plan as its own task, not a footnote.

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
| `pyproject.toml` | Package data fix (§4) — prerequisite, not new to this sub-slice's own feature scope, but blocking for it to reach a packaged build |
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
- Full Flatpak packaging hardening (pinned/vendored sources for a
  Flathub-submittable build, per `docs/linux-port/WINDOWS_VS_LINUX.md`'s
  own "Still open" section) — §4's packaging fix is scoped to the one
  gap that blocks this sub-slice specifically (`avg/assets/` not
  reaching an installed build at all), not a general packaging audit.
