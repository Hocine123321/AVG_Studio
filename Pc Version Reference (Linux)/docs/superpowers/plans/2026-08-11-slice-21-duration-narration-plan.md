# Slice 21 — Duration bug + narration trimming rework: Implementation Plan

Implements `docs/superpowers/specs/2026-08-11-slice-21-duration-narration-design.md`.
Read that first.

Interpreter: `.venv-build\Scripts\python.exe`. Full suite:
`.venv-build\Scripts\python.exe -m pytest tests -q`. Type check:
`.venv-build\Scripts\python.exe -m mypy avg`.

## Files touched

| File | Change |
|---|---|
| `avg/pipeline/prompts.py` | `_word_budget_for_duration`; `build_narration_prompt`'s `word_min`/`word_max` become duration-derived defaults |
| `tests/pipeline/test_prompts.py` | +2 tests |
| `avg/core/settings_schema.py` | +2 `SettingSpec` entries (`voice_trim_aggressiveness`, `voice_max_removable_silence_pct`) |
| `avg/pipeline/phases/voice.py` | Fixed constants → aggressiveness-mapped, capped, one-retry trimming |
| `tests/pipeline/test_phases_media.py` | Fix `_patch_audio_pipeline`'s fake to return a realistic result dict (currently returns `None` - see Task 2 Step 2.3); +8 tests |

Two independent tasks - the word-budget fix touches none of the same
files as the trimming fix, so there's no ordering dependency between
them. Doing the word budget first only because it's the smaller, more
self-contained change.

---

## Task 1 — Word budget scales with `duration_seconds`

**Files:** `avg/pipeline/prompts.py`, `tests/pipeline/test_prompts.py`

**Interfaces produced:** `_word_budget_for_duration(duration_seconds: int) -> tuple[int, int]`

### Step 1.1 — failing tests

Append to `tests/pipeline/test_prompts.py`:

```python
from avg.pipeline.prompts import _word_budget_for_duration


@pytest.mark.parametrize(
    "duration_seconds,expected",
    [
        (30, (60, 75)),      # today's fixed values, reproduced exactly
        (60, (120, 150)),
        (90, (180, 225)),
        (120, (240, 300)),   # the "2m" preset
        (240, (480, 600)),   # the "4m" preset
    ],
)
def test_word_budget_scales_with_duration(duration_seconds, expected):
    assert _word_budget_for_duration(duration_seconds) == expected


def test_narration_prompt_default_word_bounds_scale_with_duration():
    text = build_narration_prompt(_inputs(duration_seconds=120))
    assert "240 to 300 words" in text
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/pipeline/test_prompts.py -q`
— both fail (`_word_budget_for_duration` doesn't exist; the 120s case
currently produces "60 to 75 words").

### Step 1.2 — implement

In `avg/pipeline/prompts.py`, insert before `def build_narration_prompt(`:

```python
# Calibrated to reproduce this app's pre-Slice-21 fixed 60-75 word budget
# exactly at the 30s preset (30 * 2.0 = 60, 30 * 2.5 = 75) - not a
# different pace picked from scratch. See
# docs/superpowers/specs/2026-08-11-slice-21-duration-narration-design.md
# §1 for why the old fixed budget was the actual cause of 2m/4m
# collapsing to ~30s.
_WORDS_PER_SECOND_MIN = 2.0
_WORDS_PER_SECOND_MAX = 2.5


def _word_budget_for_duration(duration_seconds: int) -> tuple[int, int]:
    """The Pass 1 narration prompt's word-count target for a given spoken
    length. Was a fixed 60-75 words regardless of `duration_seconds`
    before this - correct only for the 30s preset it was written against.
    """
    return (
        max(1, round(duration_seconds * _WORDS_PER_SECOND_MIN)),
        max(1, round(duration_seconds * _WORDS_PER_SECOND_MAX)),
    )
```

Replace the function signature and docstring:

```python
def build_narration_prompt(
    inputs: CreativeInputs,
    *,
    personality: dict[str, PersonalityPreset] | None = None,
    word_min: int = 60,
    word_max: int = 75,
    pause_frequency: str | None = "medium",
    include_markers: bool = True,
    extra_context: str = "",
) -> str:
    """Phase 1 / Pass 1's prompt: flowing narration prose, no scene grid.

    Ported from `Invoke-NarrationDraft`. `extra_context` is an opaque slot
    for whatever additional context blocks a caller has already built
    (topic research, a rejected-draft note, brief creative direction,
    few-shot examples, house style rules) - this module has no opinion on
    those subsystems and just places the text where the old prompt's
    `=== SYSTEM RULES ===` section did.
    """
    preset = get_preset(inputs.content_shape, personality)
    tone_text = resolve_visual_tone(inputs.visual_tone)
    tone_line = f"\nTone & style: {tone_text} — apply this to the narration voice." if tone_text else ""
    dur_str = _duration_phrase(inputs.duration_seconds)
```

with:

```python
def build_narration_prompt(
    inputs: CreativeInputs,
    *,
    personality: dict[str, PersonalityPreset] | None = None,
    word_min: int | None = None,
    word_max: int | None = None,
    pause_frequency: str | None = "medium",
    include_markers: bool = True,
    extra_context: str = "",
) -> str:
    """Phase 1 / Pass 1's prompt: flowing narration prose, no scene grid.

    Ported from `Invoke-NarrationDraft`. `extra_context` is an opaque slot
    for whatever additional context blocks a caller has already built
    (topic research, a rejected-draft note, brief creative direction,
    few-shot examples, house style rules) - this module has no opinion on
    those subsystems and just places the text where the old prompt's
    `=== SYSTEM RULES ===` section did.

    `word_min`/`word_max` default to `_word_budget_for_duration(inputs.
    duration_seconds)` when not given - pass them explicitly only to
    override that calibration (as one test intentionally does).
    """
    preset = get_preset(inputs.content_shape, personality)
    tone_text = resolve_visual_tone(inputs.visual_tone)
    tone_line = f"\nTone & style: {tone_text} — apply this to the narration voice." if tone_text else ""
    dur_str = _duration_phrase(inputs.duration_seconds)
    if word_min is None or word_max is None:
        default_min, default_max = _word_budget_for_duration(inputs.duration_seconds)
        word_min = default_min if word_min is None else word_min
        word_max = default_max if word_max is None else word_max
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/pipeline/test_prompts.py -q`
— all pass, including the pre-existing
`test_narration_prompt_word_bounds_and_topic_are_present` (explicit
`word_min=55, word_max=80` still wins) and every other test that calls
`build_narration_prompt(_inputs())` without asserting on the word-count
text (unaffected either way).

### Step 1.3 — commit

`fix: narration word budget scales with requested duration — Slice 21 Task 1`

---

## Task 2 — Trimming: aggressiveness setting + capped, one-retry ceiling

**Files:** `avg/core/settings_schema.py`, `avg/pipeline/phases/voice.py`,
`tests/pipeline/test_phases_media.py`

**Interfaces produced:** two new `SettingSpec` ids
(`voice_trim_aggressiveness`, `voice_max_removable_silence_pct`)

### Step 2.1 — settings schema

In `avg/core/settings_schema.py`, replace:

```python
    SettingSpec(
        id="dynamic_pauses_enabled",
        group="voice",
        label="Dynamic pauses",
        help="Insert natural pauses at [pause] markers in the script.",
        type="bool",
        default=True,
        scope="channel",
        invalidates="script",
    ),
    # --- image provider and model ---
```

with:

```python
    SettingSpec(
        id="dynamic_pauses_enabled",
        group="voice",
        label="Dynamic pauses",
        help="Insert natural pauses at [pause] markers in the script.",
        type="bool",
        default=True,
        scope="channel",
        invalidates="script",
    ),
    SettingSpec(
        id="voice_trim_aggressiveness",
        group="voice",
        label="Trim aggressiveness",
        help="How readily the dead-air removal step treats a quiet stretch as silence to cut. \"Medium\" matches this app's original fixed behavior.",
        type="choice",
        default="medium",
        scope="channel",
        choices=["low", "medium", "high"],
        invalidates="voice",
    ),
    SettingSpec(
        id="voice_max_removable_silence_pct",
        group="voice",
        label="Max removable silence (%)",
        help="If dead-air removal would cut more than this share of a segment's own length, it backs off one aggressiveness level and tries once more.",
        type="float",
        default=40.0,
        scope="channel",
        invalidates="voice",
    ),
    # --- image provider and model ---
```

No test added directly for these two entries alone - schema-level
coverage (every id appears in `GET /api/settings/schema`, every default
round-trips) is already exercised generically by
`tests/core/test_settings_schema.py`'s existing structural tests; Step
2.3 below is what actually exercises these two ids' values.

### Step 2.2 — implement in `voice.py`

Replace the module docstring's constants paragraph:

```python
Settings not yet in `avg.core.settings_schema` (silence threshold, minimum
silence length, how much air to keep, and the dynamic-pause target length)
are fixed constants below, mirroring the legacy app's own shipped defaults
(`D:\\AI_Video_Gen\\main.ps1`'s `config.audio`/`config.voice.dynamic_pauses`
- `silence_thresh_dbfs=-40`, `min_silence_len_ms=400`, `keep_silence_ms=100`,
`dynamic_pauses.length_ms=700` for its default "medium" frequency) - the
same "generous, fixed default until a real setting exists" pattern
`avg.pipeline.phases.director`'s `_MIN_IMAGES`/`_MAX_IMAGES` already uses.
"""
```

with:

```python
Silence threshold, minimum silence length and how much air to keep are no
longer fixed constants (Slice 21) - `voice_trim_aggressiveness` (channel
setting) picks one of three named presets, `_AGGRESSIVENESS_PARAMS`,
`"medium"` being the exact legacy-mirroring values
(`silence_thresh_dbfs=-40`, `min_silence_len_ms=400`, `keep_silence_ms=100`)
this phase always used before. `voice_max_removable_silence_pct` caps how
much of a segment's own length that trimming is allowed to remove before
backing off one level and trying once more - see `_clean_segment` below.
The dynamic-pause target length (`_DEFAULT_PAUSE_MS`) stays a fixed
constant - a different concern (how long a *deliberate* pause is held
open), not addressed by this slice - the same "generous, fixed default
until a real setting exists" pattern `avg.pipeline.phases.director`'s
`_MIN_IMAGES`/`_MAX_IMAGES` already uses.
"""
```

Also update the one other docstring reference to the now-variable
constant. Replace:

```python
ordinary dead air would crunch the deliberate gap down to `_KEEP_SILENCE_MS`
```

with:

```python
ordinary dead air would crunch the deliberate gap down to whatever
`voice_trim_aggressiveness` currently keeps
```

Replace the constants block:

```python
# Legacy config.audio defaults (main.ps1) - see module docstring.
_SILENCE_THRESH_DBFS = -40.0
_MIN_SILENCE_LEN_MS = 400
_KEEP_SILENCE_MS = 100

# Legacy voice.dynamic_pauses.length_ms default for "medium" frequency.
_DEFAULT_PAUSE_MS = 700
```

with:

```python
# (silence_thresh_dbfs, min_silence_len_ms, keep_silence_ms) per
# `voice_trim_aggressiveness` level. "medium" is exactly this phase's
# pre-Slice-21 fixed behavior (main.ps1's config.audio defaults) - the
# default setting value, so nobody who doesn't touch this setting sees
# any change.
_AGGRESSIVENESS_PARAMS: dict[str, tuple[float, int, int]] = {
    "low": (-45.0, 500, 150),
    "medium": (-40.0, 400, 100),
    "high": (-35.0, 300, 80),
}
# One step less aggressive than each key - what a capped segment retries
# with. "low" maps to itself: already the least aggressive level, nothing
# to back off to.
_AGGRESSIVENESS_STEP_DOWN: dict[str, str] = {"high": "medium", "medium": "low", "low": "low"}

# Legacy voice.dynamic_pauses.length_ms default for "medium" frequency.
_DEFAULT_PAUSE_MS = 700
```

Replace the per-segment trimming loop:

```python
            cleaned_paths = []
            for i, raw_path in enumerate(raw_paths, start=1):
                clean_path = tmp_path / f"clean_{i:03d}.mp3"
                remove_silences_core(
                    str(raw_path),
                    str(clean_path),
                    _SILENCE_THRESH_DBFS,
                    _MIN_SILENCE_LEN_MS,
                    _KEEP_SILENCE_MS,
                    "",
                    "",
                    "",
                )
                cleaned_paths.append(clean_path)
```

with:

```python
            aggressiveness = str(settings.get("voice_trim_aggressiveness") or "medium")
            if aggressiveness not in _AGGRESSIVENESS_PARAMS:
                aggressiveness = "medium"
            max_removable_pct = float(settings.get("voice_max_removable_silence_pct", 40.0))
            max_removable_pct = max(0.0, min(100.0, max_removable_pct))

            cleaned_paths = []
            for i, raw_path in enumerate(raw_paths, start=1):
                clean_path = tmp_path / f"clean_{i:03d}.mp3"
                _clean_segment(
                    ctx,
                    segment_index=i,
                    raw_path=raw_path,
                    clean_path=clean_path,
                    aggressiveness=aggressiveness,
                    max_removable_pct=max_removable_pct,
                )
                cleaned_paths.append(clean_path)
```

Add the new helper (after the phase class, before `_write_scene_audio_map`
or any other module-level helper - exact placement doesn't matter, this
plan puts it directly after the class body ends):

```python
def _clean_segment(
    ctx: PhaseContext,
    *,
    segment_index: int,
    raw_path: Path,
    clean_path: Path,
    aggressiveness: str,
    max_removable_pct: float,
) -> None:
    """Run `remove_silences_core` once at `aggressiveness`; if it removed
    more than `max_removable_pct` of the segment's own original length,
    retry exactly once at one level less aggressive (a no-op retry if
    already at "low" - see `_AGGRESSIVENESS_STEP_DOWN`). If it's still
    over the cap after that, keep the retried result anyway and report
    the degradation - never a second retry, never silently discarding
    audio the user already paid a provider for.
    """
    thresh, min_sil, keep = _AGGRESSIVENESS_PARAMS[aggressiveness]
    result = remove_silences_core(str(raw_path), str(clean_path), thresh, min_sil, keep, "", "", "")
    removed_pct = _removed_pct(result)
    if removed_pct is None or removed_pct <= max_removable_pct:
        return

    fallback_level = _AGGRESSIVENESS_STEP_DOWN[aggressiveness]
    if fallback_level != aggressiveness:
        thresh, min_sil, keep = _AGGRESSIVENESS_PARAMS[fallback_level]
        result = remove_silences_core(str(raw_path), str(clean_path), thresh, min_sil, keep, "", "", "")
        removed_pct = _removed_pct(result)
        aggressiveness = fallback_level

    if removed_pct is not None and removed_pct > max_removable_pct:
        report_degradation(
            ctx,
            f"Segment {segment_index}: dead-air removal cut {removed_pct:.0f}% of the "
            f"audio, above the {max_removable_pct:.0f}% cap even at \"{aggressiveness}\" "
            "- kept the result anyway rather than discard synthesized audio.",
        )


def _removed_pct(result: dict[str, object]) -> float | None:
    """`None` when there's nothing meaningful to divide by (no speech
    detected at all, or a zero-length original) - callers treat that as
    "no cap concern", not as "0% removed"."""
    if result.get("no_speech") or not result.get("original_dur"):
        return None
    return 100.0 * float(result["removed_dur"]) / float(result["original_dur"])
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/pipeline/test_phases_media.py -q`
— this fails right now, before Step 2.3's mock fix: every `VoicePhase`
test hits the `None`-return incompatibility described there. That fix is
a prerequisite, not optional cleanup - do it before trusting any result
from this step.

### Step 2.3 — fix the shared mock, THEN add tests

Voice phase tests live in `tests/pipeline/test_phases_media.py`
(confirmed - not a separate `test_voice.py`), sharing `_project`/
`_channel`/`_ctx` helpers with `VisualsPhase`'s tests in the same file.
`ctx.channel_values` there is built through the real
`resolve_channel_settings`, so a plain `_channel()` (empty `settings={}`)
already resolves `voice_trim_aggressiveness`/
`voice_max_removable_silence_pct` to their schema defaults once Step 2.1
lands - no test-fixture changes needed for that part.

**One real compatibility break, though:** `_patch_audio_pipeline`'s
`fake_remove_silences_core` currently returns `None` (implicitly - it
just writes the output file and returns nothing). `_clean_segment` (Step
2.2) calls `.get(...)` on whatever `remove_silences_core` returns - with
the current fake, every single existing `VoicePhase` test would start
raising `AttributeError: 'NoneType' object has no attribute 'get'`. Fix
the fake to return a shape matching the real vendored function first, or
nothing else in this task can pass.

In `tests/pipeline/test_phases_media.py`, replace:

```python
    def fake_remove_silences_core(inp, out, thresh, min_sil, keep, beat_map, timeline, scene_map, pause_map="", pause_target_ms=0):
        remove_silences_calls.append((inp, out, thresh, min_sil, keep, beat_map, timeline, scene_map, pause_map, pause_target_ms))
        Path(out).write_bytes(Path(inp).read_bytes())
```

with:

```python
    def fake_remove_silences_core(inp, out, thresh, min_sil, keep, beat_map, timeline, scene_map, pause_map="", pause_target_ms=0):
        remove_silences_calls.append((inp, out, thresh, min_sil, keep, beat_map, timeline, scene_map, pause_map, pause_target_ms))
        Path(out).write_bytes(Path(inp).read_bytes())
        # Matches avg.media.vendor.avg.remove_silences_core's real return
        # shape. removed_dur=0.0 keeps every pre-Slice-21 test's assertion
        # that remove_silences_core is called exactly once per segment
        # true - 0% removed never exceeds any cap, so nothing here ever
        # triggers Slice 21's retry.
        return {"no_speech": False, "original_dur": 1.0, "cleaned_dur": 1.0, "removed_dur": 0.0, "chunks": 1, "pauses_kept": 0}
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/pipeline/test_phases_media.py -q`
— every pre-existing `VoicePhase` test passes again, including
`test_voice_removes_dead_air_via_vendored_remove_silences_core_once_per_segment`,
unchanged, still asserting `thresh == -40.0` (the "medium" default).

Now append the new tests, in the `# VoicePhase` section, after
`test_voice_removes_dead_air_via_vendored_remove_silences_core_once_per_segment`.
Add the import first - replace:

```python
from avg.pipeline.phases.voice import VoicePhase
```

with:

```python
from avg.pipeline.phases.voice import VoicePhase, _AGGRESSIVENESS_PARAMS, _clean_segment, _removed_pct
```

Then append:

```python
def test_medium_aggressiveness_matches_the_original_fixed_values():
    assert _AGGRESSIVENESS_PARAMS["medium"] == (-40.0, 400, 100)


def test_removed_pct_none_when_no_speech_detected():
    assert _removed_pct({"no_speech": True, "original_dur": 5.0, "removed_dur": 5.0}) is None


def test_removed_pct_computed_normally():
    assert _removed_pct({"no_speech": False, "original_dur": 10.0, "removed_dur": 3.0}) == 30.0


def test_segment_under_cap_needs_no_retry(tmp_path, monkeypatch):
    calls = []

    def fake_remove(inp, out, thresh, min_sil, keep, *rest):
        calls.append(thresh)
        return {"no_speech": False, "original_dur": 10.0, "cleaned_dur": 9.0, "removed_dur": 1.0}

    monkeypatch.setattr("avg.pipeline.phases.voice.remove_silences_core", fake_remove)
    ctx, _events = _ctx(_project(tmp_path))

    _clean_segment(
        ctx,
        segment_index=1,
        raw_path=tmp_path / "raw.mp3",
        clean_path=tmp_path / "clean.mp3",
        aggressiveness="medium",
        max_removable_pct=40.0,
    )

    assert calls == [-40.0]  # one call, medium's own threshold, no retry


def test_segment_over_cap_retries_one_level_down(tmp_path, monkeypatch):
    calls = []

    def fake_remove(inp, out, thresh, min_sil, keep, *rest):
        calls.append(thresh)
        removed = 8.0 if thresh == -40.0 else 2.0  # less aggressive → less removed
        return {"no_speech": False, "original_dur": 10.0, "cleaned_dur": 10.0 - removed, "removed_dur": removed}

    monkeypatch.setattr("avg.pipeline.phases.voice.remove_silences_core", fake_remove)
    ctx, _events = _ctx(_project(tmp_path))

    _clean_segment(
        ctx,
        segment_index=1,
        raw_path=tmp_path / "raw.mp3",
        clean_path=tmp_path / "clean.mp3",
        aggressiveness="high",
        max_removable_pct=40.0,
    )

    assert calls == [-35.0, -40.0]  # high, then medium (one level down)


def test_already_lowest_level_gets_no_retry(tmp_path, monkeypatch):
    calls = []

    def fake_remove(inp, out, thresh, min_sil, keep, *rest):
        calls.append(thresh)
        return {"no_speech": False, "original_dur": 10.0, "cleaned_dur": 5.0, "removed_dur": 5.0}

    monkeypatch.setattr("avg.pipeline.phases.voice.remove_silences_core", fake_remove)
    ctx, _events = _ctx(_project(tmp_path))

    _clean_segment(
        ctx,
        segment_index=1,
        raw_path=tmp_path / "raw.mp3",
        clean_path=tmp_path / "clean.mp3",
        aggressiveness="low",
        max_removable_pct=40.0,
    )

    assert calls == [-45.0]  # low's own threshold, once - nowhere lower to retry with


def test_still_over_cap_after_retry_reports_degradation(tmp_path, monkeypatch):
    def fake_remove(inp, out, thresh, min_sil, keep, *rest):
        return {"no_speech": False, "original_dur": 10.0, "cleaned_dur": 2.0, "removed_dur": 8.0}

    monkeypatch.setattr("avg.pipeline.phases.voice.remove_silences_core", fake_remove)
    reported = []
    monkeypatch.setattr(
        "avg.pipeline.phases.voice.report_degradation",
        lambda ctx, summary: reported.append(summary),
    )
    ctx, _events = _ctx(_project(tmp_path))

    _clean_segment(
        ctx,
        segment_index=3,
        raw_path=tmp_path / "raw.mp3",
        clean_path=tmp_path / "clean.mp3",
        aggressiveness="high",
        max_removable_pct=40.0,
    )

    assert len(reported) == 1
    assert "Segment 3" in reported[0]


def test_voice_trim_aggressiveness_setting_reaches_remove_silences_core(tmp_path, monkeypatch):
    """End-to-end, through the real phase: a channel's
    `voice_trim_aggressiveness` setting is what `remove_silences_core`
    actually gets called with, not just what `_clean_segment` does in
    isolation above."""
    fake = _FakeVoiceProvider()
    remove_calls, _assemble_calls = _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    channel = _channel({"voice_trim_aggressiveness": "low"})
    ctx, _events = _ctx(project, channel=channel)
    _write_narration(ctx, "One single segment, no pause markers here.")

    VoicePhase().run(ctx)

    assert len(remove_calls) == 1
    _inp, _out, thresh, min_sil, keep, *_rest = remove_calls[0]
    assert (thresh, min_sil, keep) == _AGGRESSIVENESS_PARAMS["low"]
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/pipeline/test_phases_media.py -q`
— all pass, both the fixed pre-existing tests and the seven new ones.

### Step 2.4 — verify

```
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
```

### Step 2.5 — commit

`feat: capped, settings-driven narration trimming — Slice 21 Task 2`

---

## Task 3 — Full regression, update progress ledger

### Step 3.1

```
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
```

Fix any fallout the same way Slice 20's Task 7 does - fix the assumption,
not the test's intent.

### Step 3.2

Update `.superpowers/sdd/progress.md`: Slice 21 complete, both fixes
noted, test/mypy results recorded.

### Step 3.3 — commit

`docs: Slice 21 complete — duration bug + narration trimming`

---

## Deferred / explicitly out of scope

- Wiring `voice_speed` into actual TTS synthesis.
- Widening `_MIN_SCENES`/`_MAX_SCENES` for long-form videos.
- A dedicated Slider UI treatment for these settings (Settings-selectors
  slice's concern, not this one) - the existing generic settings UI
  already renders a `choice` and a `float` correctly without it.
