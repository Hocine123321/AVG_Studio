# Slice 31 (sub-slice 4) — Caption Animation Styles: Design

**Goal:** the last of the four ranked video-result gaps. `captions_
animation_style` already exists in the schema (`none`/`fade`/`pop`) and
in the Settings UI, but `avg/pipeline/phases/captions.py` hardcodes the
`pop` entrance for every cue regardless of the setting's value — the
"quick win" this sub-slice was ranked as, going in. Reading the actual
render code (`python/avg.py`'s `animated_text`/`reveal_text`/
`highlight_text`, `D:\AI_Video_Gen`) turned up a real, non-trivial finding
worth stating up front: legacy has **six** mechanisms, not three — `rise`,
`highlight`, and `typewriter` don't exist in AVG Studio's schema at all
yet, and two of them (`highlight`, `typewriter`) need per-word timing
data that AVG Studio's current cue-grouping already **discards**
(`_group_cues` collapses a chunk of words into one `{start, end, text}`
cue with no per-word breakdown). This is a genuine data-flow change, not
just a settings wire-up — sized moderately larger than "wire an existing
dropdown," still well short of the other three sub-slices.

## 1. Settings

Extend the existing `captions_animation_style` `SettingSpec`
(`avg/core/settings_schema.py`) — no new setting, its `choices` list
grows from `["none", "fade", "pop"]` to `["none", "fade", "pop", "rise",
"highlight", "typewriter"]`. Everything else about the spec (default
`"fade"`, `invalidates="screen_text"`) is unchanged; the UI's existing
choice-field renderer needs no change either — it already lists whatever
`choices` the schema carries.

## 2. Cue grouping keeps per-word timing

`_group_cues` currently returns `{start, end, text}` per cue — `text` is
already the space-joined words, but the individual `{start, end, text}`
per WORD is thrown away once joined. `highlight` (a per-word colour
sweep synced to speech) and `typewriter` (a per-word colour snap that
moves with the voice) both need that per-word breakdown; `none`/`pop`/
`fade`/`rise` don't. Rather than branching the grouping function on style
(cue-building happens once, before the per-cue text-building loop even
knows which style is selected), each cue keeps its raw word list too:
`{start, end, text, words: [{start, end, text}, ...]}` — small, always
present, cheap, and every consumer that only reads `start`/`end`/`text`
(there are none today beyond `_dialogue_line` itself) is unaffected.

## 3. Six animation mechanisms, ported verbatim

A new `_animated_text(style, cue, *, width, height, position, font_size)
-> str` dispatcher, replacing `_dialogue_line`'s hardcoded
`_POP_IN_OVERRIDE`. Ported line-for-line from `animated_text`/
`reveal_text`/`highlight_text` (`python/avg.py`) — six genuinely
different mechanisms, not reskins of one another, exactly as legacy's
own comment states: *"pop = scale · fade = opacity · rise = move+opacity
· highlight = cumulative colour fill (left behind) · typewriter = a
single whole-word highlight that MOVES with the voice."*

- **none** — the escaped cue text, no override at all.
- **pop** — today's `_POP_IN_OVERRIDE`, unchanged (65%→108%→100% scale,
  90ms/190ms).
- **fade** — `\alpha` opacity fade-in, 180ms, no movement.
- **rise** — glides up ~34px (scaled against a 1920px reference long
  edge, matching `pop`'s own resolution-independence) while fading in,
  220ms. Needs an absolute `\move(x1,y1,x2,y2,0,t)` target — `cue_pos`'s
  formula is simple, not font-metric text-measurement: horizontal centre
  (`width // 2`, captions are centre-aligned already) and a
  position-dependent `y` (`bottom`/`top`/`center`, mirroring
  `_ALIGNMENT_BY_POSITION`'s own three cases) with fixed padding scaled
  the same way as the rise distance.
- **highlight** — per word, a `\kf` karaoke-fill sweep from the caption's
  base colour (`_COLOR_PRIMARY`) to a fixed accent
  (`_HIGHLIGHT_ACCENT = "&H0000FFFF"`, bright yellow — legacy's own fixed
  constant, not a setting; porting it as a *new* brand-driven accent
  setting would be scope invented past the source) — the colour is left
  behind once filled.
- **typewriter** — per word, an instant (not fading) colour SNAP to the
  accent exactly when that word is spoken, then an instant snap back —
  the highlight moves with the voice rather than accumulating. Needs a
  small ASS-colour-tag helper (`\c&HBBGGRR&`, stripping the leading alpha
  byte `_COLOR_PRIMARY`/`_HIGHLIGHT_ACCENT` both carry) that doesn't
  exist in this file yet.

`speed_factor` (legacy's own `animation_speed` CLI arg, all the timing
constants above are scaled by) is fixed at `1.0` — AVG Studio has no
caption-speed setting, and `_POP_IN_OVERRIDE`'s own already-shipped
90ms/190ms values are themselves that exact assumption already baked in;
this design doesn't change that.

## 4. Wiring

`_dialogue_line(start, end, body)` drops its hardcoded
`_POP_IN_OVERRIDE` + inline escape and instead takes the already-built
`body` string — `_animated_text`'s job, not `_dialogue_line`'s.
`ScreenTextPhase.run` reads `captions_animation_style` from
`ctx.channel_values` once (falling back to the schema default `"fade"`)
and calls `_animated_text` per cue when building `captions.ass`'s
Dialogue lines.

## 5. Data/schema summary

| File | Change |
|---|---|
| `avg/core/settings_schema.py` | `captions_animation_style.choices` grows to 6 |
| `avg/pipeline/phases/captions.py` | `_group_cues` keeps per-word data; new `_animated_text` + a small ASS-colour helper; `_dialogue_line` takes a pre-built body; `ScreenTextPhase.run` wires the setting through |
| `avg/media/vendor/*` | **Untouched** — captions are burned by ffmpeg/libass reading the `.ass` file this phase writes; the ASS override syntax is standard, nothing vendor-specific is involved |

No new file, no schema-shape change to `captions.ass` itself beyond the
per-Dialogue-line override text already varying by style (`none`'s lines
simply have no `{...}` prefix at all, which `.ass`/libass already treats
as "use the Style: line's own defaults" — no special-casing needed).

## 6. Testing

Backend: `_group_cues` still returns correct `start`/`end`/`text` per cue
(unchanged) AND a correct `words` list (new); `_animated_text` for every
one of the six styles — exact override string for `pop`/`fade` (fixed,
no per-cue variance beyond the escaped text), `rise`'s position math at
each of the three `position` values, `highlight`/`typewriter`'s per-word
`\kf`/`\r\t` construction against a small multi-word cue fixture,
`none`'s plain-text-only output; the schema's extended `choices` list;
`ScreenTextPhase.run` producing a `captions.ass` whose Dialogue lines
carry the selected style's override (an integration-level check, not just
the unit-level `_animated_text` tests). No frontend test runner
(`CLAUDE.md`) — the existing choice-field renderer needs no change, only
more entries in the schema's `choices` list it already reads generically.

## 7. Out of scope

- A caption animation SPEED setting — legacy's own CLI arg exists but
  AVG Studio has never had an equivalent, and adding one is a new
  feature, not a parity gap.
- A user-configurable highlight/typewriter accent colour — legacy's own
  value is a fixed constant, not a setting; porting it as a new
  brand-driven option would be scope beyond the source.
- Composite grids, image recycling, chapters/cards — other sub-slices.
