# Slice 29 — Settings screen: Models grouping, Slider, pickers, Personality rename: Design

**Goal:** primary/backup models grouped per phase, a real Slider control
for five numeric settings, Personality and Captions Font become pickers
instead of free text, and "Content shape" is renamed "Personality"
everywhere.

Covers backlog items #10, #11, #12 - one slice, one screen
(`Settings.tsx` and everything it renders), one verification pass.

---

## 0. What's already built - checked directly, not assumed

Before designing anything new: `ModelPicker.tsx` is a complete,
already-active Live-List-backed picker (provider filtering, "nothing
ticked yet" fallback, stale-value handling) - wired into every field via
`fields/index.tsx`'s dispatcher already, for any `SettingSpec` whose
`source` is `"model_cache"` or starts with `"live_list:"`. It's what
already renders all ten `{phase}_model`/`{phase}_backup` fields and
`voice_id`. `visual_tone` and `captions_animation_style` are already
`type="choice"`, already rendered through the app's own `Select` via
`ChoiceField` - already proper pickers.

This means two things the backlog literally names are already done:
**"Voice Model"** - if this means the field actually Live-List-backed
today (`voice_id`, labeled "Voice ID"), it's already a picker; the field
literally labeled "Voice model" (`voice_model_id`) is a different
setting (the TTS engine string, e.g. `eleven_multilingual_v2`) with an
existing code comment explicitly refusing this treatment - *"Offering
voice ids here would be a picker whose every option is wrong"* - correct
reasoning, left alone. **"Style"** (`visual_tone`) is already a picker.
Neither gets touched by this slice.

## 1. Models grouping (#10)

`ModelsPanel.tsx` (the read-only spend/routing summary,
`organic_night_v2.html`'s own `MODEL_ROWS` table) already shows Model and
Backup together per phase - it's already correct, matches the reference,
not touched. The actual gap is below it: `SettingsGroup.tsx` is
deliberately generic ("nothing in this file names a specific setting id,
which is the point") and renders the fifteen `models`-group settings (5
phases × model/backup/if-both-fail) as fifteen flat, identical field-rows
- no visual grouping at all.

`Settings.tsx` already has the exact precedent this needs: it already
special-cases `g.group === "models"` to insert `<ModelsPanel>` before the
generic group render. New `ModelsFieldGroups.tsx` swaps in for
`<SettingsGroup>` on that one group only (every other group's call is
untouched) - groups the fifteen specs by stripping each id's
`_model`/`_backup`/`_on_failure` suffix to find its phase, renders
`text_model_default` and `live_list_source` as their own standalone rows
(unchanged), and wraps each phase's three fields in one bordered section
with the phase name as a heading. Reuses `SettingsGroup`'s own per-field
internals (ownership, dirty state, blocked/inherited messaging, `Field`
dispatch) rather than reimplementing any of it - the grouping is a
layout change, not a new field-rendering path.

## 2. A real Slider control (#11)

Five settings, not four - `captions_font_size` added to the backlog's
explicit four (`voice_stability`, `render_threads`, `voice_speed`, `fps`)
for the reason in §0's sibling finding: it's `type="int", default=0,
"0 means auto-fit"` - the identical shape to `render_threads`'s `"0 means
auto"`, which is on the list. Inventing a third control type for one
field that already fits the second one exactly would be the
inconsistency, not including it.

New `Slider.tsx`, one component every numeric setting with real bounds
uses - not five bespoke sliders. Native `<input type="range">`, custom
CSS (no native browser chrome, matching every other control in this
app), current value displayed, keyboard-operable (arrow keys already
work on a native range input without extra code - don't suppress that
while restyling it). Step is 5% of each control's own configured range,
computed, not hand-picked per field:

| Setting | Range | Step (5%) | Zero/sentinel label |
|---|---|---|---|
| `voice_stability` | 0.0 - 1.0 | 0.05 | - |
| `voice_speed` | 0.5 - 2.0 | 0.075 | - |
| `fps` | 15 - 60 | ~2.25 → rounded sensibly | - |
| `render_threads` | 0 - (CPU core count) | core count / 20, min 1 | `0` shows "Auto" |
| `captions_font_size` | 0 - 96px (a reasonable ceiling, not measured against a running build - confirm against how large a caption realistically ever renders) | ~4.8 → rounded | `0` shows "Auto-fit" |

`render_threads`'s upper bound needs the machine's actual core count,
which nothing exposes to the frontend today (`os.cpu_count()` is only
called server-side, for the real render itself). New tiny `GET
/api/system/hardware` returning `{"cpu_count": N}`, fetched once by
whatever renders this specific slider - not folded into the schema
response, since core count is a machine fact, not a setting description.

Wired into `fields/index.tsx`'s dispatcher the same way `ModelPicker` is
- a small, explicit set of ids (or a new opt-in `SettingSpec` flag,
whichever reads cleaner once actually building it) routes to `Slider`
instead of `IntField`/`FloatField`; every other numeric setting keeps its
current plain-input rendering. Sliders don't replace the concept of
"every setting has exactly one UI" - they're a different renderer for
the same five ids, chosen the same way `ModelPicker` already is.

## 3. Personality and Captions Font become pickers (#11)

**Personality** (`content_shape`): `type="text"` today. `avg.pipeline.
personality.load_presets()` already enumerates every bundled preset
(parsed from `personality.txt`, `@lru_cache`d, cheap to call repeatedly)
- becomes `type="choice"`, `choices=list(load_presets())`, computed once
at schema-module-load time (presets are a bundled asset, not
user-editable at runtime, so this needs no new endpoint or Live-List-style
plumbing - a static list computed from a file that doesn't change during
a running process). Renders through the existing `ChoiceField`/`Select`
with zero new frontend code.

**Captions Font** (`font_family`): no equivalent enumeration exists -
confirmed, not assumed (no bundled caption fonts, no OS font-listing
call anywhere in the backend). `type="choice"`, `choices` = a small
curated list of fonts that ship with Windows by default (Arial, Segoe
UI, Calibri, Georgia, Verdana, Impact, Times New Roman) plus a literal
`"custom"` entry - `visual_tone`'s own existing escape-hatch pattern
(it already has a `"custom"` choice), not a new one. `ChoiceField` itself
doesn't currently handle "selecting this choice reveals a text field" -
needs a small, generic addition: when `spec.choices` contains `"custom"`
and the current value isn't one of the other choices, `ChoiceField` shows
a follow-up text input for the actual value. Generic because
`visual_tone` already has the identical "custom" concept sitting unused
today (worth confirming whether it's silently broken the same way font
picking would have been, while already in this exact code path) - one
fix serves both fields rather than a font-specific special case.

## 4. Personality rename (#12)

`content_shape`'s `label` becomes `"Personality"` (from `"Content
shape"`). The field id, the Python module `avg.pipeline.personality`, and
every internal identifier stay exactly as they are - resolved back in
Slice 20's investigation and unchanged since: `content_shape` is already
informally called "personality" in code comments, and
`avg.pipeline.personality.py` is a separate, lower-level concept
(parsing preset files) that happens to share the word. Display-label-only,
everywhere the label renders (Settings, and anywhere else `spec.label`
surfaces for this id - confirm there's no second hardcoded "Content
shape" string anywhere outside the schema before calling this complete).

## 5. Testing

Backend: the new `content_shape` choices resolve correctly (non-empty,
includes the generic fallback); `GET /api/system/hardware` returns a
positive `cpu_count`.

Frontend: no test runner (confirmed Slice 22) - `tsc -b` + `oxlint`
clean, plus manual checks: each phase's three model fields visually group
together; each of the five sliders shows the right range/step/zero-label
and is keyboard-operable; Personality and Captions Font render as
pickers, Font's "custom" choice reveals a text field and `visual_tone`'s
own "custom" does too if it didn't already; every "Content shape" label
now reads "Personality".

## 6. Out of scope

- `voice_model_id` - deliberately stays free text (§0).
- Any change to `ModelPicker.tsx`'s own logic - reused as-is.
- Font enumeration beyond the curated list - real OS font listing is a
  meaningfully bigger backend task than this item asks for.
