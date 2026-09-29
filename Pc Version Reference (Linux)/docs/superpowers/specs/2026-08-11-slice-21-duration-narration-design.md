# Slice 21 — Duration bug + narration trimming rework: Design

**Goal:** 2-minute and 4-minute requests stop collapsing to ~30 seconds,
and the "optimized" narration step stops over-trimming, by fixing the two
mechanisms that actually cause both symptoms — not by stretching or
padding the final render to hit a number.

Covers backlog items #21 (duration bug, high-priority) and #7 (narration
trimming). Grouped together because they share a root: both are about how
much spoken narration survives from "the writer wrote it" to "the render
reads it," and the trace for one is the trace for the other.

---

## 0. What's actually broken (traced, not guessed)

**The word budget never scales with the requested length.**
`avg.pipeline.prompts.build_narration_prompt` has hardcoded defaults
`word_min=60, word_max=75`. `avg.pipeline.phases.writing.ScriptPhase` calls
it with no override, for every request, regardless of `duration_seconds`.
60–75 words is correct for the 30-second preset — it's where those numbers
came from — but the 60s/90s/2m/4m/8m presets all get the identical budget.
At a plausible narration pace that's ~24–38 seconds of speech no matter
what was actually requested. No words-per-second scaling exists anywhere
in the pipeline today (confirmed by search). This is the direct cause of
"2m/4m produces ~30s" — the writer is never told to write more.

Downstream of that, `avg.pipeline.phases.director` correctly derives scene
and render timing from the *actual measured* audio duration, not from the
requested one — there's no separate "stretch to fit" bug. The fix belongs
entirely upstream, in how much text gets written and how much survives
trimming.

**The trimming step has no ceiling.** `avg.pipeline.phases.voice.py` calls
the vendored `avg.media.vendor.avg.remove_silences_core` (protected, not
editable) with fixed constants (`-40dBFS`, 400ms minimum, 100ms keep) and
no cap on total removed duration — it strips every detected gap below
threshold, however much that totals, with no upper bound. A segment that's
quieter than usual, or has more natural breath pauses than the threshold
expects, can lose a disproportionate share of its own length. This
compounds the word-budget problem rather than causing it independently.

**Aside, explicitly not fixed here:** `voice_speed` is a real setting
(schema entry, default `1.0`) that nothing in the pipeline reads. Folding
it into the word-budget math would be wrong until the TTS call itself
consumes it — that's a provider-layer change, separate from this slice.
Flagging it; not touching it.

## 1. Word budget: scale from `duration_seconds`

`build_narration_prompt`'s `word_min`/`word_max` become `None`-default
instead of `60`/`75`. When not given explicitly, they're computed from
`inputs.duration_seconds` via a words-per-second calibration chosen to
reproduce today's 60–75 exactly at the 30-second preset (confirmed: the
schema's own current default `length_preset="60s"` was *also* getting the
30-second-calibrated budget, so this fixes the actual default case too,
not only the flagged 2m/4m ones):

```
word_min = round(duration_seconds * 2.0)   # 30s → 60
word_max = round(duration_seconds * 2.5)   # 30s → 75
```

The one existing caller (`writing.py`) doesn't change at all — it already
calls `build_narration_prompt(inputs, extra_context=...)` without passing
word bounds, so it picks up the scaled default automatically. The one
test that explicitly overrides `word_min`/`word_max` keeps working
unchanged, since explicit values still win.

**Noted, not changed:** `_MIN_SCENES`/`_MAX_SCENES` (3–15) stay fixed.
Scene *segmentation* is content-driven, not word-count-driven — Pass 2
picks a scene count within that range based on what Pass 1 actually wrote,
so a longer narration just means more words per scene on average, not a
failure. Whether very long videos deserve a wider scene-count range is a
pacing/creative question, not a correctness one — out of scope here.

## 2. Trimming: a real ceiling, exposed as one simple setting

Two new channel settings, group `voice`:

- **`voice_trim_aggressiveness`** (`choice`: `low` / `medium` / `high`,
  default `medium`) — replaces the three fixed constants
  (`_SILENCE_THRESH_DBFS`, `_MIN_SILENCE_LEN_MS`, `_KEEP_SILENCE_MS`) with
  three named presets. `medium` reproduces today's exact values
  (`-40dBFS`/400ms/100ms) — the default behavior doesn't change for anyone
  who doesn't touch the setting.
- **`voice_max_removable_silence_pct`** (`float`, default `40.0`) — the
  ceiling, as a percentage of a segment's own original duration rather
  than an absolute time (segments vary too widely in length for an
  absolute cap to mean the same thing twice).

Both `invalidates="voice"`, matching every other setting in that group.

**When a segment exceeds the cap:** retry once, one aggressiveness level
down (`high`→`medium`→`low`; already at `low` means nothing to retry with,
so no second attempt). If it's still over the cap after that, keep the
retried result anyway and report it through `report_degradation` — voice.py
already imports this for exactly this "worked, but worse than asked"
case. No further retries: bounded, deterministic, one extra
`remove_silences_core` call at most per segment.

This is additive around the vendored function, not a reimplementation of
it — `remove_silences_core` itself is untouched; every value it uses was
already caller-supplied, just fixed. The return dict it already provides
(`original_dur`, `removed_dur`) is what the percentage check reads; its
value is currently discarded entirely.

## 3. Testing

- `_word_budget_for_duration`: exact values at several durations, `30 →
  (60, 75)` confirming the calibration reproduces today's numbers.
- `build_narration_prompt`: default (no override) picks up the
  duration-scaled budget; explicit override still wins.
- `voice.py`: aggressiveness levels map to the right
  threshold/min-silence/keep triple; a segment under the cap needs no
  retry; a segment over the cap gets exactly one retry at the next-lower
  level; already-`low` gets no retry; still-over-cap-after-retry calls
  `report_degradation` with the result kept.
- Regression: the one existing prompt test with an explicit override
  (`word_min=55, word_max=80`) keeps passing unchanged.
- Full `pytest tests -q` — no live provider calls, matching the existing
  fixtures for this phase.

## 4. Out of scope for this slice

- Wiring `voice_speed` into actual TTS synthesis.
- Widening `_MIN_SCENES`/`_MAX_SCENES` for long-form videos.
- Any UI work — `voice_trim_aggressiveness` becomes a `choice` setting the
  existing Settings UI already renders generically; a dedicated slider
  treatment (if wanted) belongs with the Settings-selectors slice, not
  here.
