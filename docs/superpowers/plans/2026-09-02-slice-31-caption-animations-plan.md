# Slice 31 (sub-slice 4) — Caption Animation Styles: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** wire `captions_animation_style` through to `captions.ass` for
real, and extend it from 3 to all 6 of legacy's mechanisms.

**Architecture:** `_group_cues` keeps each cue's raw per-word list
alongside its existing `start`/`end`/`text`; a new `_animated_text`
dispatcher (six branches, ported line-for-line) replaces
`_dialogue_line`'s hardcoded pop-in; `ScreenTextPhase.run` reads the
setting once and calls the dispatcher per cue. AVG Studio's word timing
is in **seconds** throughout (`_synthetic_word_timings`'s own docstring:
"seconds"), unlike legacy's raw millisecond AssemblyAI dicts — every
port below adjusts its unit scaling accordingly (noted inline where it
matters), not copied as literal legacy arithmetic.

**Tech Stack:** Python 3, pytest — no frontend change (the existing
choice-field renderer already reads `choices` generically).

**Spec:** `docs/superpowers/specs/2026-09-02-slice-31-caption-animations-design.md`

## Global Constraints

- No live API calls in tests (`has_key` is monkeypatched `False` in
  every existing `ScreenTextPhase` test — keep that convention).
- `avg/media/vendor/` is never edited — `.ass` files are burned by
  ffmpeg/libass reading standard override syntax; nothing vendor-specific
  is involved.
- Every reader of a channel setting goes through `ctx.channel_values`.
- Verify with `.venv-linux/bin/python -m pytest tests -q`.

## Files touched

| File | Change |
|---|---|
| `avg/core/settings_schema.py` | `captions_animation_style.choices` grows to 6 |
| `avg/pipeline/phases/captions.py` | `_group_cues` keeps per-word data; new `_animated_text`/`_ass_color_tag`/`_cue_pos`/`_res_scale`; `_dialogue_line` signature change; `ScreenTextPhase.run` wiring |
| `tests/core/test_settings_schema.py`, `tests/pipeline/test_phases_polish.py` | New/extended tests, **plus a fix to one pre-existing test** (see Task 3) |

Three tasks, strictly ordered — 2 needs 1's settings shape for realistic
test values (not a hard code dependency); 3 needs 2's functions to wire.

---

## Task 1 — Settings: extend the existing choices list

**Files:**
- Modify: `avg/core/settings_schema.py`
- Test: `tests/core/test_settings_schema.py`

**Interfaces:**
- Produces: `captions_animation_style.choices ==
  ["none", "fade", "pop", "rise", "highlight", "typewriter"]`.

### Step 1.1 — failing test

Append to `tests/core/test_settings_schema.py`:

```python
def test_captions_animation_style_offers_all_six_mechanisms() -> None:
    by_id = {s.id: s for s in SETTINGS}
    assert by_id["captions_animation_style"].choices == [
        "none", "fade", "pop", "rise", "highlight", "typewriter",
    ]
    assert by_id["captions_animation_style"].default == "fade"  # unchanged
```

Run: `.venv-linux/bin/python -m pytest tests/core/test_settings_schema.py -q -k animation_style`
Expected: FAIL — `choices == ["none", "fade", "pop"]`, not the six-item list.

### Step 1.2 — implement

In `avg/core/settings_schema.py`, replace:

```python
        choices=["none", "fade", "pop"],
```

with:

```python
        choices=["none", "fade", "pop", "rise", "highlight", "typewriter"],
```

(This is the only `choices=["none", "fade", "pop"]` literal in the file —
confirm with `grep -n 'choices=\["none", "fade", "pop"\]'
avg/core/settings_schema.py` before editing if in doubt; it belongs to
the `captions_animation_style` `SettingSpec` specifically.)

### Step 1.3 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/core/test_settings_schema.py -q`
Expected: PASS.

### Step 1.4 — commit

```bash
git add avg/core/settings_schema.py tests/core/test_settings_schema.py
git commit -m "feat: captions_animation_style offers all 6 legacy mechanisms

Schema-only - the choice-field renderer already reads this list
generically, and nothing reads the setting's value yet (that's Task 3)."
```

---

## Task 2 — Six animation mechanisms, ported

**Files:**
- Modify: `avg/pipeline/phases/captions.py`
- Test: `tests/pipeline/test_phases_polish.py`

**Interfaces:**
- Produces: `_group_cues` cues gain a `"words"` key;
  `_animated_text(style, cue, *, width, height, position, font_size) ->
  str`; `_ass_color_tag(ass_color: str) -> str`;
  `_cue_pos(width, height, position, font_size) -> tuple[int, int]`;
  `_res_scale(width, height) -> float`. Task 3 calls `_animated_text` per
  cue and passes its result to `_dialogue_line`'s new signature.

### Step 2.1 — failing tests

Append to `tests/pipeline/test_phases_polish.py` (find the `# ScreenTextPhase`
section header and add near the other captions-module-level tests, or a
new section just above it — match whatever's closest in the file):

```python
def test_group_cues_keeps_each_cues_own_words(monkeypatch):
    from avg.pipeline.phases.captions import _group_cues

    words = [
        {"text": "Hello", "start": 0.0, "end": 0.3},
        {"text": "world", "start": 0.3, "end": 0.6},
        {"text": "again", "start": 0.6, "end": 0.9},
    ]
    cues = _group_cues(words, max_words=2)
    assert cues[0]["words"] == words[:2]
    assert cues[1]["words"] == words[2:]
    assert cues[0]["text"] == "Hello world"  # unchanged shape


def test_animated_text_none_is_plain_escaped_text():
    from avg.pipeline.phases.captions import _animated_text

    cue = {"start": 0.0, "end": 1.0, "text": "Hello {world}", "words": []}
    body = _animated_text("none", cue, width=1080, height=1920, position="bottom", font_size=60)
    assert body == r"Hello \{world\}"


def test_animated_text_pop_matches_the_existing_override_exactly():
    from avg.pipeline.phases.captions import _animated_text, _POP_IN_OVERRIDE

    cue = {"start": 0.0, "end": 1.0, "text": "Hi", "words": []}
    body = _animated_text("pop", cue, width=1080, height=1920, position="bottom", font_size=60)
    assert body == _POP_IN_OVERRIDE + "Hi"


def test_animated_text_fade_is_an_alpha_only_transform():
    from avg.pipeline.phases.captions import _animated_text

    cue = {"start": 0.0, "end": 1.0, "text": "Hi", "words": []}
    body = _animated_text("fade", cue, width=1080, height=1920, position="bottom", font_size=60)
    assert body == r"{\alpha&HFF&\t(0,180,\alpha&H00&)}Hi"
    assert r"\move" not in body  # fade never moves - that's rise's job


def test_animated_text_rise_moves_up_from_below_its_resting_position():
    from avg.pipeline.phases.captions import _animated_text

    cue = {"start": 0.0, "end": 1.0, "text": "Hi", "words": []}
    body = _animated_text("rise", cue, width=1000, height=2000, position="bottom", font_size=100)
    assert r"\move(500," in body
    # y2 (resting) must be smaller than y1 (start) - it moves UP.
    import re

    m = re.search(r"\\move\(\d+,(\d+),\d+,(\d+),0,220\)", body)
    y1, y2 = int(m.group(1)), int(m.group(2))
    assert y1 > y2


def test_animated_text_rise_position_varies_top_vs_bottom():
    from avg.pipeline.phases.captions import _animated_text

    bottom = _animated_text(
        "rise", {"start": 0.0, "end": 1.0, "text": "Hi", "words": []},
        width=1000, height=2000, position="bottom", font_size=100,
    )
    top = _animated_text(
        "rise", {"start": 0.0, "end": 1.0, "text": "Hi", "words": []},
        width=1000, height=2000, position="top", font_size=100,
    )
    assert bottom != top


def test_animated_text_highlight_gives_every_word_a_kf_sweep():
    from avg.pipeline.phases.captions import _animated_text

    cue = {
        "start": 0.0,
        "end": 1.0,
        "text": "Hi there",
        "words": [{"text": "Hi", "start": 0.0, "end": 0.5}, {"text": "there", "start": 0.5, "end": 1.0}],
    }
    body = _animated_text("highlight", cue, width=1080, height=1920, position="bottom", font_size=60)
    assert body.count(r"\kf") == 2
    assert r"{\kf50}Hi" in body  # 0.5s -> 50 centiseconds
    assert "there" in body


def test_animated_text_typewriter_snaps_colour_per_word_not_left_behind():
    from avg.pipeline.phases.captions import _animated_text

    cue = {
        "start": 0.0,
        "end": 1.0,
        "text": "Hi there",
        "words": [{"text": "Hi", "start": 0.0, "end": 0.5}, {"text": "there", "start": 0.5, "end": 1.0}],
    }
    body = _animated_text("typewriter", cue, width=1080, height=1920, position="bottom", font_size=60)
    # 2 words -> each gets a \r reset, an accent \t, and a base-colour \t back.
    assert body.count(r"\r\t") == 2
    assert body.count(r"\t(500,500,") == 1  # second word's start, in ms


def test_ass_color_tag_strips_the_leading_alpha_byte():
    from avg.pipeline.phases.captions import _ass_color_tag

    assert _ass_color_tag("&H00FFFFFF") == r"\c&HFFFFFF&"
    assert _ass_color_tag("&H0000FFFF") == r"\c&H00FFFF&"
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k "animated_text or ass_color_tag or group_cues_keeps"`
Expected: FAIL — `ImportError: cannot import name '_animated_text'`
(`_group_cues`'s existing test would also fail once written, since
`"words"` isn't populated yet).

### Step 2.2 — implement

In `avg/pipeline/phases/captions.py`, add two new constants directly
after `_POP_IN_OVERRIDE`:

```python
# The highlight/typewriter accent - a FIXED colour, not a setting,
# matching legacy's own `_HIGHLIGHT_ACCENT` constant exactly (bright
# yellow). Porting it as a new brand-driven setting would be scope past
# the source (see the design spec's Out of Scope).
_HIGHLIGHT_ACCENT = "&H0000FFFF"

# Rise's move distance and cue_pos's padding are both scaled against this
# reference resolution, matching the pop-in override's own
# resolution-independence (it uses fixed ms/percentages, needing no
# scaling; rise's PIXEL distance does).
_CAP_REF_LONG_EDGE = 1920.0
```

Replace `_group_cues`. Replace:

```python
def _group_cues(words: list[dict], max_words: int = _MAX_WORDS_PER_CUE) -> list[dict]:
    """Consecutive words chunked into `{start, end, text}` caption cues of
    up to `max_words` each - simple fixed-size grouping (no sentence-
    boundary awareness), sufficient for both the real AssemblyAI word
    list and the synthetic estimate."""
    cues: list[dict] = []
    for i in range(0, len(words), max_words):
        chunk = [w for w in words[i : i + max_words] if str(w.get("text") or "").strip()]
        if not chunk:
            continue
        cues.append(
            {
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "text": " ".join(str(w["text"]) for w in chunk),
            }
        )
    return cues
```

with:

```python
def _group_cues(words: list[dict], max_words: int = _MAX_WORDS_PER_CUE) -> list[dict]:
    """Consecutive words chunked into `{start, end, text, words}` caption
    cues of up to `max_words` each - simple fixed-size grouping (no
    sentence-boundary awareness), sufficient for both the real AssemblyAI
    word list and the synthetic estimate.

    `words` (the cue's own raw per-word `{text, start, end}` list) is kept
    alongside the already-joined `text` for `_animated_text`'s
    `"highlight"`/`"typewriter"` styles, which sync colour to individual
    spoken words - every other style only ever reads `start`/`end`/`text`.
    """
    cues: list[dict] = []
    for i in range(0, len(words), max_words):
        chunk = [w for w in words[i : i + max_words] if str(w.get("text") or "").strip()]
        if not chunk:
            continue
        cues.append(
            {
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "text": " ".join(str(w["text"]) for w in chunk),
                "words": chunk,
            }
        )
    return cues
```

Add the remaining helpers and `_animated_text` directly after `_ass_escape`,
before `_group_cues`:

```python
def _res_scale(width: int, height: int) -> float:
    """Pixel distances scale against a 1920px reference long edge, the
    same reference `_CAP_REF_LONG_EDGE` names in legacy - so `rise`'s
    move distance (and its own padding) looks the same fraction of the
    frame at any output resolution, not a fixed pixel count that would be
    oversized on a small preview render or too subtle on 4K."""
    long_edge = max(width, height)
    return long_edge / _CAP_REF_LONG_EDGE if long_edge else 1.0


def _cue_pos(width: int, height: int, position: str, font_size: int) -> tuple[int, int]:
    """Where a caption cue rests once its entrance finishes - centred
    horizontally (captions are already centre-aligned via the Style
    line's own `Alignment` field), vertically per `position`. Ported from
    `cue_pos` (`D:\\AI_Video_Gen\\python\\avg.py`) - simple arithmetic,
    not font-metric text measurement; the same padding constant (`40`)
    `_ass_header`'s own `MarginL`/`MarginR` use, scaled by `_res_scale`."""
    x = width // 2
    pad = int(round(40 * _res_scale(width, height)))
    if position == "bottom":
        y = height - pad - font_size // 2
    elif position == "top":
        y = pad + font_size // 2
    else:
        y = height // 2
    return x, y


def _ass_color_tag(ass_color: str) -> str:
    """This file's own `AABBGGRR` colour constants (`&H00FFFFFF`, always
    10 characters: `&H` + 8 hex digits) rewritten as the `\\c&HBBGGRR&`
    colour-override tag, which carries no alpha byte - strips the leading
    `&H00`. Ported from `Get-AiEditPlan`'s sibling `_c()` closure in
    `reveal_text`/`highlight_text` (`D:\\AI_Video_Gen\\python\\avg.py`)."""
    return r"\c&H" + ass_color[4:] + "&"


def _animated_text(
    style: str,
    cue: dict,
    *,
    width: int,
    height: int,
    position: str,
    font_size: int,
) -> str:
    """The per-cue Dialogue-line body: the escaped caption text, wrapped
    in whichever ASS override the selected style needs. Six genuinely
    different mechanisms, ported line-for-line from `animated_text`/
    `reveal_text`/`highlight_text`
    (`D:\\AI_Video_Gen\\python\\avg.py`) - not reskins of one another:
    pop=scale, fade=opacity, rise=move+opacity, highlight=cumulative
    colour fill (left behind), typewriter=a single whole-word highlight
    that MOVES with the voice (not left behind - that's what makes it
    distinct from highlight). An unrecognised style degrades to plain
    text rather than raising, matching every other closed-vocabulary
    degrade in this codebase.

    All per-word timing below is in SECONDS (`cue["words"]`'s own shape -
    see `_group_cues`'s docstring and `_synthetic_word_timings`'s), NOT
    the milliseconds legacy's raw AssemblyAI dicts carried - the
    centisecond/millisecond conversions below are scaled accordingly, not
    copied as literal legacy arithmetic.
    """
    text = _ass_escape(cue["text"])

    if style == "none":
        return text

    if style == "pop":
        return _POP_IN_OVERRIDE + text

    if style == "fade":
        # Clean opacity fade-in, no movement, 180ms.
        return r"{\alpha&HFF&\t(0,180,\alpha&H00&)}" + text

    if style == "rise":
        # Glides up ~34px (scaled) while fading in, 220ms.
        x, y = _cue_pos(width, height, position, font_size)
        dist = max(6, int(round(34 * _res_scale(width, height))))
        return (
            r"{\alpha&HFF&\move(" + f"{x},{y + dist},{x},{y},0,220" + r")"
            r"\t(0,220,\alpha&H00&)}" + text
        )

    words = cue.get("words") or []
    if not words:
        return text  # no per-word data to sync highlight/typewriter to

    if style == "highlight":
        # Every word visible; each fills base -> accent as it's spoken
        # (\kf sweep), staying accent-coloured once filled.
        parts = []
        for w in words:
            dur_cs = max(1, int(round((float(w["end"]) - float(w["start"])) * 100)))
            parts.append(r"{\kf" + str(dur_cs) + r"}" + _ass_escape(str(w["text"])) + " ")
        return "".join(parts).rstrip()

    if style == "typewriter":
        # Every word visible; a whole-word colour SNAP moves with the
        # voice - lit for exactly the moment it's spoken, then snapped
        # back. \r before each word scopes the transform so only the
        # currently-spoken word is lit at any instant.
        base = _ass_color_tag(_COLOR_PRIMARY)
        accent = _ass_color_tag(_HIGHLIGHT_ACCENT)
        cue_start = float(cue["start"])
        parts = []
        for w in words:
            # ws must be >=1ms: an accent \t at exactly t=0 collides with
            # the \r reset (also at t=0) and \r wins, so the FIRST word
            # would never light.
            ws = max(1, int(round((float(w["start"]) - cue_start) * 1000)))
            we = max(ws + 1, int(round((float(w["end"]) - cue_start) * 1000)))
            parts.append(
                r"{\r\t(" + str(ws) + "," + str(ws) + "," + accent + r")"
                r"\t(" + str(we) + "," + str(we) + "," + base + r")}" + _ass_escape(str(w["text"]))
            )
        return " ".join(parts)

    return text
```

### Step 2.3 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k "animated_text or ass_color_tag or group_cues_keeps"`
Expected: PASS. `_dialogue_line`/`ScreenTextPhase.run` are untouched so
far — the whole-suite run happens at the end of Task 3.

### Step 2.4 — commit

```bash
git add avg/pipeline/phases/captions.py tests/pipeline/test_phases_polish.py
git commit -m "feat: six caption animation mechanisms, ported line-for-line

_animated_text dispatches to pop/fade/rise/highlight/typewriter/none.
_group_cues now keeps each cue's raw per-word list for the two styles
that sync colour to individual spoken words. Not yet wired into
ScreenTextPhase.run - _dialogue_line still hardcodes pop (Task 3)."
```

---

## Task 3 — Wire the setting through `ScreenTextPhase.run`

**Files:**
- Modify: `avg/pipeline/phases/captions.py`
- Test: `tests/pipeline/test_phases_polish.py` (new tests **and a fix to
  one pre-existing test**)

**Interfaces:**
- Consumes: `_animated_text` (Task 2), `captions_animation_style` from
  `ctx.channel_values`.
- Produces: `captions.ass`'s Dialogue lines carry the selected style's
  override.

### Step 3.1 — fix the pre-existing test that assumed pop-by-default

`test_captions_ass_header_matches_the_real_schema`
(`tests/pipeline/test_phases_polish.py`) asserts the pop-in override
appears in `captions.ass` without ever setting
`captions_animation_style` — true today only because the phase hardcodes
pop regardless of the setting, which is the exact bug this sub-slice
fixes. Once real, an unconfigured channel resolves the schema's own
default (`"fade"`), and this test's pop-in assertion would fail against
correct new behaviour, not a regression.

Fix it FIRST, in its own small commit, so Task 3's real change doesn't
land alongside a test that was already quietly wrong:

Replace:

```python
    project = _project(tmp_path, aspect_ratio="16:9", short_edge=1080)
    ctx, _ = _ctx(project, channel=_channel({"font_family": "Impact"}))
```

with:

```python
    project = _project(tmp_path, aspect_ratio="16:9", short_edge=1080)
    # This test is specifically about the pop style's exact override
    # shape - captions_animation_style must be requested explicitly, not
    # relied on as an (incorrect, pre-fix) default.
    ctx, _ = _ctx(project, channel=_channel({"font_family": "Impact", "captions_animation_style": "pop"}))
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k header_matches_the_real_schema`
Expected: PASS (this fix alone doesn't change behaviour yet — `pop` is
still what the hardcoded phase produces either way, until Step 3.3).

Commit this fix on its own:

```bash
git add tests/pipeline/test_phases_polish.py
git commit -m "test: pin the ass-header test to captions_animation_style=pop

It was implicitly relying on the phase's hardcoded pop-in (the bug this
sub-slice fixes) rather than requesting pop explicitly. Fixed ahead of
the real wiring change so that change's own commit is a clean diff."
```

### Step 3.2 — failing tests for the real wiring

Append to `tests/pipeline/test_phases_polish.py`:

```python
def test_screen_text_phase_respects_the_animation_style_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, _ = _ctx(project, channel=_channel({"captions_animation_style": "fade"}))
    _write_audio(ctx)
    _write_scenes(ctx, ["A single simple scene of narration text."])

    ScreenTextPhase().run(ctx)
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")

    assert r"\alpha&HFF&\t(0,180,\alpha&H00&)" in content
    assert r"\fscx65" not in content  # not the pop override


def test_screen_text_phase_none_style_has_no_override_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, _ = _ctx(project, channel=_channel({"captions_animation_style": "none"}))
    _write_audio(ctx)
    _write_scenes(ctx, ["A single simple scene of narration text."])

    ScreenTextPhase().run(ctx)
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")

    dialogue_lines = [line for line in content.splitlines() if line.startswith("Dialogue:")]
    assert dialogue_lines  # sanity: there is at least one cue
    for line in dialogue_lines:
        text_field = line.split(",,", 1)[1]
        assert not text_field.startswith("{")  # no ASS override block at all


def test_screen_text_phase_unset_animation_style_defaults_to_fade(tmp_path, monkeypatch):
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)  # no captions_animation_style set
    _write_audio(ctx)
    _write_scenes(ctx, ["A single simple scene of narration text."])

    ScreenTextPhase().run(ctx)
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")

    assert r"\alpha&HFF&\t(0,180,\alpha&H00&)" in content  # fade is the schema default
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k respects_the_animation_style`
Expected: FAIL — every cue still carries the hardcoded pop override
regardless of the setting.

### Step 3.3 — implement

In `avg/pipeline/phases/captions.py`, replace `_dialogue_line`. Replace:

```python
def _dialogue_line(start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{_POP_IN_OVERRIDE}{_ass_escape(text)}"
```

with:

```python
def _dialogue_line(start: float, end: float, body: str) -> str:
    """`body` is the already-built line content - `_animated_text`'s job,
    not this function's; a plain (`"none"`-style) body is exactly the
    escaped cue text with no override prefix at all."""
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{body}"
```

Replace `ScreenTextPhase.run`'s cue-building loop. Replace:

```python
        cues = _group_cues(words)
        lines = [_ass_header(width, height, font_name, font_size, position)]
        suppressed = 0
        for cue in cues:
            if any(_overlaps(cue["start"], cue["end"], cs, ce) for cs, ce in card_windows):
                suppressed += 1
                continue
            lines.append(_dialogue_line(cue["start"], cue["end"], cue["text"]))
```

with:

```python
        animation_style = str(ctx.channel_values.get("captions_animation_style") or "fade")

        cues = _group_cues(words)
        lines = [_ass_header(width, height, font_name, font_size, position)]
        suppressed = 0
        for cue in cues:
            if any(_overlaps(cue["start"], cue["end"], cs, ce) for cs, ce in card_windows):
                suppressed += 1
                continue
            body = _animated_text(
                animation_style, cue, width=width, height=height, position=position, font_size=font_size
            )
            lines.append(_dialogue_line(cue["start"], cue["end"], body))
```

### Step 3.4 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_polish.py -q`
Expected: PASS, including the Step 3.1 fix and every other pre-existing
`ScreenTextPhase` test — none of the others assert on the override text
itself (only header fields, font family, suppression, degrade paths),
so they're unaffected by which style ends up selected.

Then the whole suite:

Run: `.venv-linux/bin/python -m pytest tests -q`
Expected: PASS at this sandbox's documented baseline.

### Step 3.5 — commit

```bash
git add avg/pipeline/phases/captions.py tests/pipeline/test_phases_polish.py
git commit -m "feat: ScreenTextPhase honours captions_animation_style

The setting has existed in the schema and Settings UI since before this
sub-slice; captions.py hardcoded pop-in regardless until now."
```

---

## Self-review (performed while writing this plan)

**Spec coverage** — §1 → Task 1, §2 → Task 2 (`_group_cues`), §3 → Task 2
(`_animated_text` + helpers), §4 → Task 3, §5 (data/schema) → covered
across all three, §6 (testing) → each task's own test step, §7 (out of
scope) → untouched.

**A real pre-existing-test conflict found while planning, not just
executing:** `test_captions_ass_header_matches_the_real_schema` asserts
the pop override appears with NO `captions_animation_style` set — which
only ever passed because of the exact bug this sub-slice fixes. Wiring
the setting through without touching that test would turn a currently-
green test red for the RIGHT reason (fade is now the correct unconfigured
behaviour) and require whoever hits the failure to figure out whether
it's a regression or an expected consequence. Task 3's Step 3.1 fixes it
in its own commit, before the real wiring change, so the wiring commit's
diff doesn't also carry an unrelated-looking test edit.

**Unit correctness caught while tracing exact code, not assumed from
legacy's own arithmetic:** legacy's `highlight`/`typewriter` scale raw
millisecond AssemblyAI timestamps (`/10` for centiseconds, direct ms for
`\t`). AVG Studio's own `_synthetic_word_timings` docstring states word
timing is in SECONDS throughout this codebase. `_animated_text`'s
centisecond math is `* 100` (not legacy's `/10`) and millisecond math is
`* 1000` (not legacy's identity) — copying legacy's literal arithmetic
would have been off by three orders of magnitude.

**Type consistency:** `cue["words"]` is `list[dict]` with `{text, start,
end}` (seconds) — the exact shape `_get_words`/`_synthetic_word_timings`
already produce, sliced by `_group_cues` — through `_animated_text`'s
`highlight`/`typewriter` branches, no reshaping in between.

**Placeholder scan:** no "TBD"/"similar to Task N" anywhere above; every
step carries real code or an exact shell command.
