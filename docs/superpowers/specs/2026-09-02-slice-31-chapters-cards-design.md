# Slice 31 (sub-slice 3) — Chapters + Explainer/Focused Cards: Design

**Goal:** the third-ranked video-result gap — chapter/parts grouping with
its own full-screen divider card, plus the AI-selected "Explainer" concept
board that sits alongside the already-ported, deterministic "Focused"
card. Ported from `Get-AiVideoMap`'s chapters path, `Get-AiEditPlan`, and
`Export-CardPlan` (all in `D:\AI_Video_Gen\modules\ai.ps1`), read in full
for this design.

**The rendering side is already complete** — this is the smallest of the
four sub-slices' actual new-code footprint, and the reason is worth
stating plainly: `avg.pipeline.phases.director.build_timeline` already
turns a `video_map.json["parts"]` entry into a takeover node
(`{after_scene, title, sub, duration_sec}` → `{type:"takeover", ...}`);
`avg.media.overlays.render_layers` already loops every takeover node from
`scene_timings` and draws it as a `chapters_takeover_cards` element;
`avg.media.vendor.overlay_design.render_chapters_takeover_cards` and
`render_explainer` are both byte-identical vendored copies of the legacy
drawing functions, already in `FULLSCREEN_CARDS`/`RENDER_FUNCS`. None of
that needs to change. The entire gap is upstream: nothing ever writes a
`parts` entry, and nothing ever asks the AI for an Explainer card.

## 1. Settings

Three, all group `"overlay"` (co-located with the existing card-colour
settings), scope `"channel"`:

- `takeover_cards_enabled` — `bool`, default `True`, `invalidates=
  "director2"`. A master switch for every card type (Focused, Explainer,
  Chapters) — legacy's `edit.takeover_cards`. AVG Studio currently has no
  such switch at all (Focused cards are unconditional); this closes that
  gap too while adding the other two card types in the same settings
  area.
- `chapters_enabled` — `bool`, default `False`, `invalidates="director1"`.
  Legacy's `edit.graphic_preset` (`''`/`'chapters'`) ported as a plain
  boolean — the only other value that string ever took was the removed
  procedural-overlay system (`Get-AiEditPlan`'s own synopsis: "the
  procedural floating-overlay planner was REMOVED"), so a boolean is a
  faithful, simpler port, not a simplification that loses a real mode.
- `explainer_enabled` — `bool`, default `False`, `invalidates="director2"`.
  Legacy's `edit.enabled` (misleadingly named there — it gates only the
  Explainer AI call, not the whole "AI Editing" system, most of which
  was already removed per the same synopsis above).

## 2. Director I: chapters/parts grouping

`build_director1_prompt` gains `chapters_enabled: bool = False`. When
`True`, adds — verbatim from `Get-AiVideoMap`'s `$chaptersSection` —
estimated part count (`max(2, min(4, ceil(scene_count/10)))`), the
grouping rule ("split where the sub-topic genuinely changes... never split
scene 1 away from its part; never put a part break right before the final
scene"), and title/subtitle length limits, plus a `parts` field in the
schema.

The schema AVG Studio asks for is **flatter** than legacy's own (`{part,
title, scenes, takeover:{headline,sub,duration_sec}}`) — legacy's nested
`takeover.headline` and top-level `title` describe the same on-card text
twice for no functional reason once ported; AVG Studio asks for one flat
shape per part: `{"part":<int>,"title":"<max 4 words>","sub":"<max 6
words>","scenes":[<scene numbers>],"duration_sec":1.8}`. Same content, same
rules, no redundant field.

`Director1Phase.run` gains a `_normalize_part_entry` (siblings
`_normalize_scene_entry`/`_normalize_zoom_word` already in the file):
validates `part`/`title`/`sub`/`scenes` (a non-empty list of valid,
in-range scene numbers), computes `after_scene = min(scenes) - 1` (the
divider lands right before the part's first scene — `build_timeline`
already expects exactly this key), and **drops the entry outright if
`after_scene <= 0`** (rule: scene 1 is never split from its part, so
part 1 never gets a divider — there is nothing "before the video" to
insert one into). The normalized list becomes `video_map["parts"]` — the
one new key added to the dict already built and written at
`director.py:321-327`; `build_timeline`, called on the very next lines,
needs no change at all to pick it up.

## 3. Explainer cards: a new AI call in Director II

A new prompt, `build_explainer_prompt(scene_texts) -> str`, ported
verbatim from `Get-AiEditPlan`: pick a *few* scenes (budget
`max(0, min(4, floor(scene_count/6)))`, `0` skips the call entirely —
most videos need none) whose narration states a "deeper concept" worth a
full-screen pause, an 8-word max on-card phrasing per pick. Called from
`Director2Phase.run`, gated on `explainer_enabled` AND a non-zero budget,
using its own router phase key (`"director2_explainer"`, distinct from
`"director2"` — same reasoning `"script_segmentation"` already has its
own key apart from `"script"`: separate model routing/cost visibility per
call). A failed or malformed response degrades to zero explainer cards,
never fails the phase — matches every other AI-response degrade path in
this file.

## 4. Card-plan merge

`_build_card_plan` currently builds Focused cards only (from `((question))`
markers) and applies the scene-frequency cap alone. Extended to:

1. Gate on `takeover_cards_enabled` first — `False` returns `{"cards":
   []}` immediately, before either candidate source runs (matches legacy
   exactly: the Explainer AI call itself is skipped, not just its
   output).
2. Build Focused candidates exactly as today, but route any candidate
   whose text exceeds `_FOCUSED_MAX_CHARS` (already declared in this file
   — a constant with a full explanatory comment, wired to nothing) to
   `element: "explainer"` instead of `"focused"`. This is not new
   behaviour invented for this sub-slice — it is finishing a fix a prior
   slice already started and left unconnected, discovered while reading
   this exact function for this design.
3. Append Explainer candidates from step 3 (§3) when `explainer_enabled`.
4. Merge by scene: one card per scene, **Focused wins** — ported exactly
   from `Export-CardPlan`'s own dedup ("a question hook beats a concept
   board"), because a deterministic author-placed marker is a stronger
   editorial signal than an AI guess.
5. Apply the existing frequency cap (`_CARD_SCENE_DIVISOR`) to the
   *merged* set, not just the Focused set — the cap's own formula (`max(1,
   scene_count // 3)`) is already identical to legacy's, so no cap-logic
   change, only moving where it applies.

## 5. Data/schema summary

| File | Change |
|---|---|
| `avg/core/settings_schema.py` | +3 `SettingSpec`s |
| `avg/pipeline/prompts.py` | `build_director1_prompt` gains `chapters_enabled`; new `build_explainer_prompt` |
| `avg/pipeline/phases/director.py` | `_normalize_part_entry`; `video_map["parts"]`; Explainer AI call + `_build_card_plan` merge/gate/long-text-routing |
| `avg/media/overlays.py`, `avg/media/vendor/*`, `build_timeline` | **Untouched** — confirmed already complete (see Goal) |

`video_map.json` gains an optional `"parts"` key — additive, absent when
`chapters_enabled` is off (matches every other optional artifact in this
codebase). `card_plan.json`'s cards may now carry `element: "explainer"`
as well as `"focused"` — the vendored drawing dispatch already handles
both.

## 6. Testing

Backend: `build_director1_prompt`'s chapters section (present/absent,
part-count formula, schema field); `_normalize_part_entry` (valid part,
missing/out-of-range scenes, the "part 1 never gets a divider" rule);
`build_timeline` fed a realistic `parts` list — no code change expected,
but a test proving the ALREADY-WRITTEN function correctly turns a
Director-I-shaped `parts` entry into a takeover node closes the loop this
design's central claim rests on; `build_explainer_prompt`'s budget formula
and skip-at-zero; `_build_card_plan`'s merge (focused wins on a shared
scene, cap applies to the merged set, `takeover_cards_enabled=False`
short-circuits both sources, a long focused question routes to
`element:"explainer"`). No frontend test runner (`CLAUDE.md`) — no new
settings UI beyond existing `bool` field renderers.

## 7. Out of scope

- The settings-menu chapters/explainer editing UI beyond the three plain
  toggles above — legacy doesn't have more than that either.
- A third, independent "graphic preset" mode — confirmed there isn't one
  left to port (§1).
- Composite grids, image recycling, caption animation styles — other
  sub-slices.
