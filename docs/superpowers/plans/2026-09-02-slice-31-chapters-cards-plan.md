# Slice 31 (sub-slice 3) — Chapters + Explainer/Focused Cards: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Director I groups scenes into chapters and writes each part's
divider card into `video_map.json["parts"]` — a shape `build_timeline`
already fully consumes; Director II gains a second, AI-planned card
source (Explainer) alongside the existing deterministic one (Focused),
merged with "Focused wins" and a master on/off switch neither card type
has today.

**Architecture:** the render side needs zero changes (confirmed in the
spec) — every task here is upstream of it: Director I's prompt/response,
a new Director II AI call, and `_build_card_plan`'s merge logic.

**Tech Stack:** Python 3, pytest — no frontend change.

**Spec:** `docs/superpowers/specs/2026-09-02-slice-31-chapters-cards-design.md`

## Global Constraints

- No live API calls in tests.
- `avg/media/vendor/`, `avg/media/overlays.py`, `build_timeline` are never
  edited by this plan — confirmed already complete.
- Every reader of a channel setting goes through `ctx.channel_values`.
- Every `SettingSpec` requires `invalidates`.
- Verify with `.venv-linux/bin/python -m pytest tests -q`.

## Files touched

| File | Change |
|---|---|
| `avg/core/settings_schema.py` | +3 `SettingSpec`s |
| `avg/pipeline/prompts.py` | `build_director1_prompt` gains `chapters_enabled`; new `build_explainer_prompt` |
| `avg/pipeline/phases/director.py` | `_normalize_part_entry`; `video_map["parts"]`; `_normalize_explainer_card`; Explainer AI call in `Director2Phase.run`; `_build_card_plan` merge/gate |
| `tests/core/test_settings_schema.py`, `tests/pipeline/test_prompts.py`, `tests/pipeline/test_phases_text.py`, `tests/pipeline/test_phases_polish.py` | New/extended tests per task |

Four tasks. 2 needs 1 (settings shape for realistic test values). 3 and 4
each need 1; 4 needs 3's `_normalize_explainer_card` shape for its merge
tests. 2 and 3 are independent of each other and could be done in either
order — kept in spec order here.

---

## Task 1 — Settings

**Files:**
- Modify: `avg/core/settings_schema.py:445` (after `overlay_highlight`)
- Test: `tests/core/test_settings_schema.py`

**Interfaces:**
- Produces: `takeover_cards_enabled` (`bool`, default `True`),
  `chapters_enabled` (`bool`, default `False`), `explainer_enabled`
  (`bool`, default `False`), all group `"overlay"`, scope `"channel"`.
  Task 2 reads `chapters_enabled`; Task 3/4 read the other two.

### Step 1.1 — failing test

Append to `tests/core/test_settings_schema.py`:

```python
def test_card_settings_exist_with_expected_defaults() -> None:
    by_id = {s.id: s for s in SETTINGS}
    assert by_id["takeover_cards_enabled"].default is True
    assert by_id["chapters_enabled"].default is False
    assert by_id["explainer_enabled"].default is False
    for setting_id in ("takeover_cards_enabled", "chapters_enabled", "explainer_enabled"):
        assert by_id[setting_id].type == "bool"
        assert by_id[setting_id].group == "overlay"
        assert by_id[setting_id].scope == "channel"
    assert by_id["chapters_enabled"].invalidates == "director1"
    assert by_id["takeover_cards_enabled"].invalidates == "director2"
    assert by_id["explainer_enabled"].invalidates == "director2"
```

Run: `.venv-linux/bin/python -m pytest tests/core/test_settings_schema.py -q -k card_settings`
Expected: FAIL — `KeyError` on `by_id["takeover_cards_enabled"]`.

### Step 1.2 — implement

In `avg/core/settings_schema.py`, replace (note: this is the SAME anchor
Task 1 of sub-slice 1's plan also inserts at — if that plan has already
been executed, `overlay_highlight`'s block will be followed by the
`filters_*` settings instead of `# --- voice provider and options ---`
directly; insert this new block immediately after `overlay_highlight`
regardless of what already follows it):

```python
    SettingSpec(
        id="overlay_highlight",
        group="overlay",
        label="Highlight",
        help=(
            "Card highlight colour, '#RRGGBB', for cards whose plan entry asks for it. "
            "Empty uses the drawing library's own default."
        ),
        type="text",
        default="",
        scope="channel",
        invalidates="director2",
    ),
```

with:

```python
    SettingSpec(
        id="overlay_highlight",
        group="overlay",
        label="Highlight",
        help=(
            "Card highlight colour, '#RRGGBB', for cards whose plan entry asks for it. "
            "Empty uses the drawing library's own default."
        ),
        type="text",
        default="",
        scope="channel",
        invalidates="director2",
    ),
    SettingSpec(
        id="takeover_cards_enabled",
        group="overlay",
        label="Takeover cards",
        help="Master switch for every full-screen card (Focused, Explainer, Chapters). Off means none.",
        type="bool",
        default=True,
        scope="channel",
        invalidates="director2",
    ),
    SettingSpec(
        id="chapters_enabled",
        group="overlay",
        label="Chapters",
        help="Group scenes into named parts, each opening with its own title card.",
        type="bool",
        default=False,
        scope="channel",
        invalidates="director1",
    ),
    SettingSpec(
        id="explainer_enabled",
        group="overlay",
        label="Explainer cards",
        help="Let the AI pick a few scenes worth a full-screen concept card, in addition to Focused cards.",
        type="bool",
        default=False,
        scope="channel",
        invalidates="director2",
    ),
```

### Step 1.3 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/core/test_settings_schema.py -q`
Expected: PASS.

### Step 1.4 — commit

```bash
git add avg/core/settings_schema.py tests/core/test_settings_schema.py
git commit -m "feat: chapters/explainer/takeover-cards settings

takeover_cards_enabled is a master switch this codebase has never had -
Focused cards were unconditional before this. chapters_enabled and
explainer_enabled gate the two new card sources this sub-slice adds."
```

---

## Task 2 — Director I: chapters/parts grouping

**Files:**
- Modify: `avg/pipeline/prompts.py`
- Modify: `avg/pipeline/phases/director.py`
- Test: `tests/pipeline/test_prompts.py`, `tests/pipeline/test_phases_text.py`

**Interfaces:**
- Produces: `build_director1_prompt(..., chapters_enabled: bool = False)`;
  `video_map.json["parts"]` (list, possibly empty — `build_timeline`
  already reads exactly this shape, confirmed in the spec and re-proven
  by this task's own integration test, not re-implemented).

### Step 2.1 — failing tests

Append to `tests/pipeline/test_prompts.py`:

```python
def test_director1_prompt_has_no_chapters_section_by_default():
    from avg.pipeline.prompts import build_director1_prompt

    prompt = build_director1_prompt(_inputs(), ["Scene one.", "Scene two."])
    assert "CHAPTER STRUCTURE" not in prompt
    assert '"parts"' not in prompt


def test_director1_prompt_chapters_section_when_enabled():
    from avg.pipeline.prompts import build_director1_prompt

    scenes = [f"Scene {i}." for i in range(12)]
    prompt = build_director1_prompt(_inputs(), scenes, chapters_enabled=True)
    assert "CHAPTER STRUCTURE" in prompt
    assert "Group the 12 scenes into 2 sequential PARTS" in prompt  # ceil(12/10)=2
    assert '"parts":[{"part":1' in prompt


def test_director1_prompt_part_count_scales_with_scene_count():
    from avg.pipeline.prompts import build_director1_prompt

    scenes = [f"Scene {i}." for i in range(35)]
    prompt = build_director1_prompt(_inputs(), scenes, chapters_enabled=True)
    assert "Group the 35 scenes into 4 sequential PARTS" in prompt  # min(4, ceil(35/10))=4
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_prompts.py -q -k director1_prompt`
Expected: FAIL — `build_director1_prompt() got an unexpected keyword
argument 'chapters_enabled'`.

Append to `tests/pipeline/test_phases_text.py`:

```python
def test_director1_writes_no_parts_when_chapters_is_off(tmp_path, monkeypatch):
    reply = json.dumps({
        "image_count": 2,
        "scenes": [{"scene": 1, "role": "hook", "beat": "HIT", "visual_intent": "POP"}],
        "zoom_words": [],
        "notes": "",
        "parts": [{"part": 1, "title": "Should be ignored", "sub": "", "scenes": [1], "duration_sec": 1.8}],
    })
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)  # chapters_enabled defaults False
    _write_scenes(ctx, ["Scene one text."])
    Director1Phase().run(ctx)

    video_map = read_json(script_dir(ctx) / "video_map.json")
    assert video_map["parts"] == []


def test_director1_writes_parts_and_the_timeline_gets_a_takeover(tmp_path, monkeypatch):
    reply = json.dumps({
        "image_count": 6,
        "scenes": [{"scene": i, "role": "hook", "beat": "none", "visual_intent": "BACKGROUND"} for i in range(1, 7)],
        "zoom_words": [],
        "notes": "",
        "parts": [
            {"part": 1, "title": "Intro", "sub": "", "scenes": [1, 2, 3], "duration_sec": 1.8},
            {"part": 2, "title": "Deep Dive", "sub": "Here's why", "scenes": [4, 5, 6], "duration_sec": 2.0},
        ],
    })
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    channel = _channel({"chapters_enabled": True})
    ctx, _ = _ctx(project, channel=channel)
    _write_scenes(ctx, [f"Scene {i} text." for i in range(1, 7)])
    Director1Phase().run(ctx)

    video_map = read_json(script_dir(ctx) / "video_map.json")
    assert video_map["parts"] == [
        {"after_scene": 3, "title": "Deep Dive", "sub": "Here's why", "duration_sec": 2.0}
    ]  # part 1 covers scene 1 -> after_scene would be 0 -> dropped (never splits scene 1)

    timeline = read_json(script_dir(ctx) / "timeline.json")
    takeover_nodes = [n for n in timeline if n["type"] == "takeover"]
    assert len(takeover_nodes) == 1
    assert takeover_nodes[0]["headline"] == "Deep Dive"
    assert takeover_nodes[0]["sub"] == "Here's why"
    # This assertion is the proof the spec's central claim rests on:
    # build_timeline needed ZERO changes to consume Director I's output -
    # it already did, before this task existed.


def test_director1_drops_a_part_whose_scenes_are_all_invalid(tmp_path, monkeypatch):
    reply = json.dumps({
        "image_count": 2,
        "scenes": [{"scene": 1, "role": "hook", "beat": "none", "visual_intent": "BACKGROUND"}],
        "zoom_words": [],
        "notes": "",
        "parts": [{"part": 1, "title": "Ghost", "sub": "", "scenes": [99], "duration_sec": 1.8}],
    })
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    channel = _channel({"chapters_enabled": True})
    ctx, _ = _ctx(project, channel=channel)
    _write_scenes(ctx, ["Scene one text."])
    Director1Phase().run(ctx)

    assert read_json(script_dir(ctx) / "video_map.json")["parts"] == []
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_text.py -q -k "parts or chapters"`
Expected: FAIL — `KeyError: 'parts'` (the key doesn't exist in
`video_map.json` yet).

### Step 2.2 — implement

In `avg/pipeline/prompts.py`, add `import math` to the top-of-file
imports. Replace:

```python
import re
from dataclasses import dataclass
```

with:

```python
import math
import re
from dataclasses import dataclass
```

Replace `build_director1_prompt`'s signature and body. Replace:

```python
def build_director1_prompt(
    inputs: CreativeInputs,
    scene_texts: list[str],
    *,
    min_images: int = 3,
    max_images: int = 160,
) -> str:
    """Phase 2 (Director I)'s prompt: the finished, scene-segmented script is
    FINAL - this decides the video's visual STRUCTURE around it (how many
    distinct images the story needs, each scene's role/beat/visual-intent,
    and which words deserve zoom emphasis). Ported from `Get-AiVideoMap`
    (`D:\\AI_Video_Gen\\modules\\ai.ps1`), simplified: chapters/parts grouping
    and per-scene filter assignment are a separate, later concern
    (Director II owns filters; chapters are out of scope for this task) -
    this is the non-chaptered, filter-free path only.

    `scene_texts` is each scene's verbatim narration, in order (from
    `avg.pipeline.phases.writing`'s `scenes.json`) - the numbered list the
    model classifies is built from these, never re-derived from raw
    narration, so Director I always sees exactly the scenes the render will
    actually use.
    """
    numbered = "\n".join(f'[{i + 1}] "{text}"' for i, text in enumerate(scene_texts))
    current_count = len(scene_texts)

    return f"""You are the STRUCTURE DIRECTOR for a short narrated video built from still images.
The script below is FINAL. Your job is to decide its VISUAL STRUCTURE: how many
distinct images the story needs, how each scene should feel, and what kind of image
each scene wants.

Personality: {inputs.content_shape}. Total length: ~{inputs.duration_seconds} seconds ({inputs.form_label}).
The script has {current_count} scene(s). ONE image per scene is the default and almost
always correct — the scene count reflects the content's own natural pacing, so scenes
are intentionally uneven in length.

SCRIPT (numbered narration lines):
{numbered}

Decide:
- image_count: how many distinct images tell THIS story. KEEP THIS EQUAL to {current_count}
  (one image per scene) unless the script is CLEARLY mis-sized for its length — only then
  adjust. Must be between {min_images} and {max_images}. Do NOT change it for stylistic
  preference; a changed count triggers a full script rewrite, so only diverge from
  {current_count} when the script is genuinely broken.
- scenes: one entry per EXISTING script scene (exactly {current_count}, indexed 1..{current_count}), each with:
    - scene: the scene number
    - role: hook | tension | reveal | proof | example | pause | hit | cta
    - beat: none | HIT | PAUSE | ZOOM | SLOWDOWN  (HIT = hard punch; PAUSE = held breath;
      ZOOM = one key word deserves zoom emphasis; SLOWDOWN = lower energy; none = normal)
    - visual_intent: BACKGROUND | ASSET | POP
        BACKGROUND = full-frame scene backdrop (most scenes)
        ASSET = a floating graphic/icon/diagram composited over a clean background
        POP = a bold full-frame impact graphic that cuts in hard
- zoom_words: array of {{scene, word}} — for each ZOOM-beat scene, the single word to emphasize.

Return ONLY a JSON object, no preamble, no markdown fences:
{{"version":1,"image_count":<int>,
 "scenes":[{{"scene":1,"role":"hook","beat":"HIT","visual_intent":"POP"}}],
 "zoom_words":[{{"scene":<int>,"word":"<string>"}}],
 "notes":"one line on your structural reasoning"}}"""
```

with:

```python
def build_director1_prompt(
    inputs: CreativeInputs,
    scene_texts: list[str],
    *,
    min_images: int = 3,
    max_images: int = 160,
    chapters_enabled: bool = False,
) -> str:
    """Phase 2 (Director I)'s prompt: the finished, scene-segmented script is
    FINAL - this decides the video's visual STRUCTURE around it (how many
    distinct images the story needs, each scene's role/beat/visual-intent,
    which words deserve zoom emphasis, and - when `chapters_enabled` -
    how scenes group into named chapters). Ported from `Get-AiVideoMap`
    (`D:\\AI_Video_Gen\\modules\\ai.ps1`), simplified: per-scene filter
    assignment is a separate concern (Director II owns filters).

    `scene_texts` is each scene's verbatim narration, in order (from
    `avg.pipeline.phases.writing`'s `scenes.json`) - the numbered list the
    model classifies is built from these, never re-derived from raw
    narration, so Director I always sees exactly the scenes the render will
    actually use.

    `chapters_enabled=False` produces a BYTE-IDENTICAL prompt to before
    this parameter existed - the chapters section and schema field only
    ever appear when there's something for the model to plan.
    """
    numbered = "\n".join(f'[{i + 1}] "{text}"' for i, text in enumerate(scene_texts))
    current_count = len(scene_texts)

    chapters_section = ""
    parts_schema = ""
    if chapters_enabled and current_count > 0:
        est_parts = max(2, min(4, math.ceil(current_count / 10)))
        chapters_section = f"""

CHAPTER STRUCTURE (this video uses chapters):
- Group the {current_count} scenes into {est_parts} sequential PARTS, each covering one sub-topic.
  Split where the sub-topic genuinely changes, not every N scenes. Never split scene 1 away
  from its part; never put a part break right before the final scene. Each part opens with a
  full-screen takeover card (title + short subtitle) shown BEFORE its first scene.
- Chapter title: max 4 words, states the sub-topic plainly. Subtitle: max 6 words, rephrases
  the title as a hook, not a restatement.
- Also return a "parts" array (see schema)."""
        parts_schema = (
            ',\n "parts":[{"part":1,"title":"<max 4 words>","sub":"<max 6 words>",'
            '"scenes":[<scene numbers>],"duration_sec":1.8}]'
        )

    return f"""You are the STRUCTURE DIRECTOR for a short narrated video built from still images.
The script below is FINAL. Your job is to decide its VISUAL STRUCTURE: how many
distinct images the story needs, how each scene should feel, and what kind of image
each scene wants.

Personality: {inputs.content_shape}. Total length: ~{inputs.duration_seconds} seconds ({inputs.form_label}).
The script has {current_count} scene(s). ONE image per scene is the default and almost
always correct — the scene count reflects the content's own natural pacing, so scenes
are intentionally uneven in length.

SCRIPT (numbered narration lines):
{numbered}

Decide:
- image_count: how many distinct images tell THIS story. KEEP THIS EQUAL to {current_count}
  (one image per scene) unless the script is CLEARLY mis-sized for its length — only then
  adjust. Must be between {min_images} and {max_images}. Do NOT change it for stylistic
  preference; a changed count triggers a full script rewrite, so only diverge from
  {current_count} when the script is genuinely broken.
- scenes: one entry per EXISTING script scene (exactly {current_count}, indexed 1..{current_count}), each with:
    - scene: the scene number
    - role: hook | tension | reveal | proof | example | pause | hit | cta
    - beat: none | HIT | PAUSE | ZOOM | SLOWDOWN  (HIT = hard punch; PAUSE = held breath;
      ZOOM = one key word deserves zoom emphasis; SLOWDOWN = lower energy; none = normal)
    - visual_intent: BACKGROUND | ASSET | POP
        BACKGROUND = full-frame scene backdrop (most scenes)
        ASSET = a floating graphic/icon/diagram composited over a clean background
        POP = a bold full-frame impact graphic that cuts in hard
- zoom_words: array of {{scene, word}} — for each ZOOM-beat scene, the single word to emphasize.
{chapters_section}

Return ONLY a JSON object, no preamble, no markdown fences:
{{"version":1,"image_count":<int>,
 "scenes":[{{"scene":1,"role":"hook","beat":"HIT","visual_intent":"POP"}}],
 "zoom_words":[{{"scene":<int>,"word":"<string>"}}]{parts_schema},
 "notes":"one line on your structural reasoning"}}"""
```

In `avg/pipeline/phases/director.py`, add `_normalize_part_entry` directly
after `_normalize_zoom_word`:

```python
def _normalize_part_entry(entry: object, valid_scenes: set[int]) -> dict | None:
    """One `parts[]` entry from Director I's chapters-mode response,
    reduced to the flat shape `build_timeline` already reads
    (`{after_scene, title, sub, duration_sec}`) - or `None` if it names
    no valid scene, or its divider would land before scene 1. "Never
    split scene 1 away from its part" means part 1 never gets a takeover
    card - there is nothing before the video to insert one into."""
    if not isinstance(entry, dict):
        return None
    scenes_in = entry.get("scenes")
    if not isinstance(scenes_in, list):
        return None
    part_scenes = sorted({s for raw in scenes_in if (s := _coerce_int(raw)) is not None and s in valid_scenes})
    if not part_scenes:
        return None
    after_scene = part_scenes[0] - 1
    if after_scene <= 0:
        return None
    return {
        "after_scene": after_scene,
        "title": str(entry.get("title") or "").strip(),
        "sub": str(entry.get("sub") or "").strip(),
        "duration_sec": _coerce_float(entry.get("duration_sec"), _TAKEOVER_SECONDS),
    }
```

Replace `Director1Phase.run`'s body. Replace:

```python
        scene_texts = [str(scene.get("text") or "") for scene in scenes if isinstance(scene, dict)]
        current_count = len(scene_texts)
        inputs = creative_inputs(ctx)

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning visual structure..."))
        prompt = build_director1_prompt(inputs, scene_texts, min_images=_MIN_IMAGES, max_images=_MAX_IMAGES)
        result = generate_text(prompt, "director1", config=ctx.router)
```

with:

```python
        scene_texts = [str(scene.get("text") or "") for scene in scenes if isinstance(scene, dict)]
        current_count = len(scene_texts)
        inputs = creative_inputs(ctx)
        chapters_enabled = bool(ctx.channel_values.get("chapters_enabled", False))

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning visual structure..."))
        prompt = build_director1_prompt(
            inputs,
            scene_texts,
            min_images=_MIN_IMAGES,
            max_images=_MAX_IMAGES,
            chapters_enabled=chapters_enabled,
        )
        result = generate_text(prompt, "director1", config=ctx.router)
```

Then replace where `video_map` is assembled. Replace:

```python
        video_map = {
            "version": 1,
            "image_count": image_count,
            "scenes": scenes_out,
            "zoom_words": zoom_words,
            "notes": str(parsed.get("notes") or "").strip(),
        }
```

with:

```python
        parts_out: list[dict] = []
        if chapters_enabled:
            valid_scenes = set(range(1, current_count + 1))
            parts_out = [
                normalized
                for entry in (parsed.get("parts") or [])
                if (normalized := _normalize_part_entry(entry, valid_scenes)) is not None
            ]

        video_map = {
            "version": 1,
            "image_count": image_count,
            "scenes": scenes_out,
            "zoom_words": zoom_words,
            "parts": parts_out,
            "notes": str(parsed.get("notes") or "").strip(),
        }
```

### Step 2.3 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_prompts.py tests/pipeline/test_phases_text.py -q`
Expected: PASS, including every pre-existing `build_director1_prompt`/
`Director1Phase` test — none pass `chapters_enabled=True` or set the
`chapters_enabled` setting, so they all get `parts: []` and the
byte-identical prompt, matching current behaviour.

### Step 2.4 — commit

```bash
git add avg/pipeline/prompts.py avg/pipeline/phases/director.py \
  tests/pipeline/test_prompts.py tests/pipeline/test_phases_text.py
git commit -m "feat: Director I plans chapters/parts when chapters_enabled

build_timeline needed zero changes - it already read exactly the
{after_scene, title, sub, duration_sec} shape this task now populates,
confirmed by a new integration test asserting timeline.json gets a real
takeover node from a chaptered video_map.json."
```

---

## Task 3 — Explainer cards: a new AI call

**Files:**
- Modify: `avg/pipeline/prompts.py`
- Modify: `avg/pipeline/phases/director.py`
- Test: `tests/pipeline/test_prompts.py`, `tests/pipeline/test_phases_polish.py`

**Interfaces:**
- Produces: `build_explainer_prompt(scene_texts, max_cards) -> str`;
  `_normalize_explainer_card(entry, valid_scenes) -> dict | None`
  (`{"element": "explainer", "scene", "text", "accent"}` — same shape
  `_build_card_plan` already uses for Focused cards). Task 4's merge
  consumes a `list[dict]` of exactly this shape.

### Step 3.1 — failing tests

Append to `tests/pipeline/test_prompts.py`:

```python
def test_explainer_prompt_states_the_card_budget():
    from avg.pipeline.prompts import build_explainer_prompt

    prompt = build_explainer_prompt(["Scene one.", "Scene two."], max_cards=2)
    assert "at most 2 in this whole video" in prompt
    assert "[1] Scene one." in prompt
    assert "[2] Scene two." in prompt


def test_explainer_prompt_schema_is_present():
    from avg.pipeline.prompts import build_explainer_prompt

    prompt = build_explainer_prompt(["Scene one."], max_cards=1)
    assert '{"cards":[{"scene":<int>,"text":"<max 8 words>"}]}' in prompt
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_prompts.py -q -k explainer_prompt`
Expected: FAIL — `ImportError: cannot import name 'build_explainer_prompt'`.

Append to `tests/pipeline/test_phases_polish.py`:

```python
def test_director2_skips_the_explainer_call_when_disabled(tmp_path, monkeypatch):
    from avg.pipeline.phases import director as director_module

    reply = json.dumps({"grade": {"look": "none"}, "scenes": [{"scene": 1, "motion": {}, "transition_in": "cut"}]})
    calls = _QueueGenerator([reply])
    monkeypatch.setattr(director_module, "generate_text", calls)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)  # explainer_enabled defaults False
    _write_scenes(ctx, ["Scene one."])
    director_module.Director2Phase().run(ctx)

    assert len(calls.calls) == 1  # only the director2 motion/grade call - no explainer call


def test_director2_skips_the_explainer_call_when_scene_count_is_too_low(tmp_path, monkeypatch):
    from avg.pipeline.phases import director as director_module

    # max_cards = min(4, scene_count // 6); 5 scenes -> 0, so no call at all
    reply = json.dumps({
        "grade": {"look": "none"},
        "scenes": [{"scene": i, "motion": {}, "transition_in": "cut"} for i in range(1, 6)],
    })
    calls = _QueueGenerator([reply])
    monkeypatch.setattr(director_module, "generate_text", calls)

    project = _project(tmp_path)
    channel = _channel({"explainer_enabled": True})
    ctx, _ = _ctx(project, channel=channel)
    _write_scenes(ctx, [f"Scene {i}." for i in range(1, 6)])
    director_module.Director2Phase().run(ctx)

    assert len(calls.calls) == 1


def test_director2_explainer_cards_reach_card_plan(tmp_path, monkeypatch):
    from avg.pipeline.phases import director as director_module

    direction_reply = json.dumps({
        "grade": {"look": "none"},
        "scenes": [{"scene": i, "motion": {}, "transition_in": "cut"} for i in range(1, 7)],
    })
    explainer_reply = json.dumps({"cards": [{"scene": 3, "text": "A deeper concept here."}]})
    monkeypatch.setattr(
        director_module, "generate_text", _QueueGenerator([direction_reply, explainer_reply])
    )

    project = _project(tmp_path)
    channel = _channel({"explainer_enabled": True})
    ctx, _ = _ctx(project, channel=channel)
    _write_scenes(ctx, [f"Scene {i}." for i in range(1, 7)])  # 6 scenes -> max_cards = 1
    director_module.Director2Phase().run(ctx)

    card_plan = read_json(render_dir(ctx) / "card_plan.json")
    assert card_plan["cards"] == [{"element": "explainer", "scene": 3, "text": "A deeper concept here.", "accent": "accent1"}]


def test_director2_explainer_failure_degrades_to_no_cards(tmp_path, monkeypatch):
    from avg.pipeline.phases import director as director_module

    direction_reply = json.dumps({
        "grade": {"look": "none"},
        "scenes": [{"scene": i, "motion": {}, "transition_in": "cut"} for i in range(1, 7)],
    })

    def _raise(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    calls = _QueueGenerator([direction_reply])
    original_call = calls.__call__

    def _flaky(*args, **kwargs):
        if args and args[1] == "director2_explainer":
            raise RuntimeError("provider unavailable")
        return original_call(*args, **kwargs)

    monkeypatch.setattr(director_module, "generate_text", _flaky)

    project = _project(tmp_path)
    channel = _channel({"explainer_enabled": True})
    ctx, _ = _ctx(project, channel=channel)
    _write_scenes(ctx, [f"Scene {i}." for i in range(1, 7)])

    director_module.Director2Phase().run(ctx)  # must not raise

    card_plan = read_json(render_dir(ctx) / "card_plan.json")
    assert card_plan["cards"] == []
```

Check `_QueueGenerator`'s exact call signature in this file first (`grep
-n "class _QueueGenerator" -A 15 tests/pipeline/test_phases_polish.py`) —
adjust `_flaky`'s `args[1]` index to match whatever positional argument
actually carries the phase-key string (`generate_text(prompt, phase_key,
config=...)` implies index `1`, but confirm against the real call site
in `director.py` before trusting the index).

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k explainer`
Expected: FAIL — `card_plan["cards"]` never contains an `"explainer"`
element (nothing calls the new prompt yet).

### Step 3.2 — implement

In `avg/pipeline/prompts.py`, add `build_explainer_prompt` directly after
`build_director2_prompt` (end of file):

```python
def build_explainer_prompt(scene_texts: list[str], max_cards: int) -> str:
    """The Explainer takeover card planner: picks a FEW scenes whose
    narration states a deeper concept worth a full-screen pause. Ported
    from `Get-AiEditPlan` (`D:\\AI_Video_Gen\\modules\\ai.ps1`) - an AI
    pick, independent of the deterministic Focused cards
    (`avg.pipeline.phases.director._build_card_plan`), which come from
    `((question))` markers instead. `max_cards` is the caller's own
    budget (`avg.pipeline.phases.director.Director2Phase.run`:
    `max(0, min(4, scene_count // 6))`) - stated in the prompt so the
    model self-limits, not enforced here.
    """
    numbered = "\n".join(f"[{i + 1}] {text}" for i, text in enumerate(scene_texts))
    return f"""You are an editor deciding where to drop a full-screen EXPLAINER CARD — a centred "board" that
covers the video for a moment to let the viewer absorb a deeper CONCEPT, definition, or summary
spoken in that scene. Use them RARELY (at most {max_cards} in this whole video), only where a scene
states a genuinely important idea worth pausing on. Most videos need ZERO.

SCENES (number + narration):
{numbered}

For each chosen scene, "text" is a SHORT on-card phrasing of the concept (max 8 words) — it will be
shown centred and synced to the voice, so keep it to the words actually spoken there.

Return ONLY JSON, no preamble/fences: {{"cards":[{{"scene":<int>,"text":"<max 8 words>"}}]}}
If no scene warrants a board, return {{"cards":[]}}."""
```

In `avg/pipeline/phases/director.py`, extend the imports. Replace:

```python
from avg.pipeline.prompts import build_director1_prompt, build_director2_prompt
```

with:

```python
from avg.pipeline.prompts import build_director1_prompt, build_director2_prompt, build_explainer_prompt
```

Add `_normalize_explainer_card` directly after `_normalize_direction_entry`
(the Director II section's own normalizer):

```python
def _normalize_explainer_card(entry: object, valid_scenes: set[int]) -> dict | None:
    """One `cards[]` entry from the Explainer AI response, or `None` if it
    names no valid scene or has no text. Same shape `_build_card_plan`
    already uses for a Focused card - `element` fixed to `"explainer"`,
    since that is the only kind this planner ever produces."""
    if not isinstance(entry, dict):
        return None
    scene_num = _coerce_int(entry.get("scene"))
    if scene_num is None or scene_num not in valid_scenes:
        return None
    text = str(entry.get("text") or "").strip()
    if not text:
        return None
    return {"element": "explainer", "scene": scene_num, "text": text, "accent": _ACCENT}
```

Replace `Director2Phase.run`'s body from `inputs = creative_inputs(ctx)`
through the `card_plan = _build_card_plan(scenes)` line. Replace:

```python
        scene_texts = [str(scene.get("text") or "") for scene in scenes if isinstance(scene, dict)]
        valid_scenes = set(range(1, len(scene_texts) + 1))
        inputs = creative_inputs(ctx)

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning motion and grade..."))
        prompt = build_director2_prompt(inputs, scene_texts)
        result = generate_text(prompt, "director2", config=ctx.router)
```

with:

```python
        scene_texts = [str(scene.get("text") or "") for scene in scenes if isinstance(scene, dict)]
        valid_scenes = set(range(1, len(scene_texts) + 1))
        inputs = creative_inputs(ctx)
        settings = ctx.channel_values

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning motion and grade..."))
        prompt = build_director2_prompt(inputs, scene_texts)
        result = generate_text(prompt, "director2", config=ctx.router)
```

(This only pulls `settings = ctx.channel_values` earlier so both this
call and the explainer call below share it — no other change in this
hunk.)

Then replace the card-plan build site. Replace:

```python
        card_plan = _build_card_plan(scenes)
        write_json(rdir / "card_plan.json", card_plan)
```

with:

```python
        takeover_cards_enabled = bool(settings.get("takeover_cards_enabled", True))
        explainer_enabled = bool(settings.get("explainer_enabled", False))

        explainer_cards: list[dict] = []
        if takeover_cards_enabled and explainer_enabled:
            max_cards = max(0, min(4, len(scene_texts) // 6))
            if max_cards > 0:
                try:
                    explainer_prompt = build_explainer_prompt(scene_texts, max_cards)
                    explainer_result = generate_text(explainer_prompt, "director2_explainer", config=ctx.router)
                    explainer_parsed = extract_json_object(explainer_result.text)
                    explainer_cards = [
                        normalized
                        for entry in (explainer_parsed.get("cards") or [])[:max_cards]
                        if (normalized := _normalize_explainer_card(entry, valid_scenes)) is not None
                    ]
                except Exception as exc:  # noqa: BLE001 - best-effort, matches _draw_overlay_assets below
                    message = f"Explainer cards could not be planned ({exc}); continuing without them."
                    ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=90, message=message))
                    explainer_cards = []

        card_plan = _build_card_plan(
            scenes, takeover_cards_enabled=takeover_cards_enabled, explainer_cards=explainer_cards
        )
        write_json(rdir / "card_plan.json", card_plan)
```

(`_build_card_plan`'s new keyword arguments are added in Task 4 — this
task calls it with them already, which is fine: Python allows passing
keyword arguments a function doesn't yet accept only if the function
signature already has them, so **Task 4 must land its signature change
in the SAME commit boundary consideration** — practically, run Task 3's
tests only after Task 4's signature change exists, or accept that Task 3
alone will fail at Step 3.3 with a `TypeError` until Task 4 is also
done. This is a deliberate ordering note, not a plan error: implement
Task 4 immediately after Task 3, and run both tasks' tests together
before the first commit of either, OR implement Task 4's signature
change first as part of Task 3's Step 3.2 and let Task 4 focus purely on
the merge body. Simplest: do Step 3.2 above, then continue straight into
Task 4 below before running any tests.)

### Step 3.3 — run to verify pass

Do not run yet — continue to Task 4's implementation first (see the note
above), then run both tasks' tests together at the end of Task 4's Step
4.3.

---

## Task 4 — `_build_card_plan` merges Focused + Explainer

**Files:**
- Modify: `avg/pipeline/phases/director.py`
- Test: `tests/pipeline/test_phases_polish.py`

**Interfaces:**
- Consumes: `_normalize_explainer_card`'s output shape (Task 3).
- Produces: `_build_card_plan(scenes, *, takeover_cards_enabled=True,
  explainer_cards=None) -> dict`.

### Step 4.1 — failing tests

Append to `tests/pipeline/test_phases_polish.py`:

```python
def test_build_card_plan_takeover_cards_disabled_returns_nothing():
    from avg.pipeline.phases.director import _build_card_plan

    scenes = [{"text": "So ((why stripes?)) nobody asks."}]
    plan = _build_card_plan(
        scenes, takeover_cards_enabled=False, explainer_cards=[{"element": "explainer", "scene": 1, "text": "x", "accent": "accent1"}]
    )
    assert plan == {"cards": []}


def test_build_card_plan_merges_explainer_cards_on_untouched_scenes():
    from avg.pipeline.phases.director import _build_card_plan

    scenes = [{"text": "So ((why stripes?)) nobody asks."}, {"text": "Plain scene, no marker."}]
    explainer = [{"element": "explainer", "scene": 2, "text": "A deeper concept.", "accent": "accent1"}]
    plan = _build_card_plan(scenes, explainer_cards=explainer)
    assert plan["cards"] == [
        {"element": "focused", "scene": 1, "text": "why stripes?", "accent": "accent1"},
        {"element": "explainer", "scene": 2, "text": "A deeper concept.", "accent": "accent1"},
    ]


def test_build_card_plan_focused_wins_on_a_shared_scene():
    from avg.pipeline.phases.director import _build_card_plan

    scenes = [{"text": "So ((why stripes?)) nobody asks."}]
    explainer = [{"element": "explainer", "scene": 1, "text": "Should lose.", "accent": "accent1"}]
    plan = _build_card_plan(scenes, explainer_cards=explainer)
    assert plan["cards"] == [{"element": "focused", "scene": 1, "text": "why stripes?", "accent": "accent1"}]


def test_build_card_plan_cap_applies_to_the_merged_set():
    from avg.pipeline.phases.director import _build_card_plan

    # 9 scenes, cap = max(1, 9 // 3) = 3. 1 focused + 3 explainer = 4 candidates -> capped to 3.
    scenes = [{"text": "So ((why stripes?)) nobody asks."}] + [{"text": "Plain."}] * 8
    explainer = [
        {"element": "explainer", "scene": i, "text": f"Concept {i}.", "accent": "accent1"} for i in (2, 3, 4)
    ]
    plan = _build_card_plan(scenes, explainer_cards=explainer)
    assert len(plan["cards"]) == 3
    assert plan["cards"][0]["element"] == "focused"  # focused, inserted first, survives the cap


def test_build_card_plan_defaults_match_pre_existing_behaviour():
    from avg.pipeline.phases.director import _build_card_plan

    # No keyword args at all - every pre-existing call site must still work.
    scenes = [{"text": "So ((why stripes?)) nobody asks."}]
    assert _build_card_plan(scenes) == {"cards": [{"element": "focused", "scene": 1, "text": "why stripes?", "accent": "accent1"}]}
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k build_card_plan`
Expected: FAIL — `TypeError: _build_card_plan() got an unexpected keyword
argument 'takeover_cards_enabled'`.

### Step 4.2 — implement

In `avg/pipeline/phases/director.py`, replace `_build_card_plan`. Replace:

```python
def _build_card_plan(scenes: list[dict]) -> dict:
    """Deterministic Focused takeover cards from `((question))` markers in
    each scene's text - see module docstring. No `start`/`end` yet;
    Captions (Phase 6) adds those."""
    cards: list[dict] = []
    for i, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        text = str(scene.get("text") or "")
        questions = extract_question_markers(text)
        if not questions:
            continue
        # One card per scene at most - a scene rarely needs two boards.
        cards.append({"element": "focused", "scene": i, "text": questions[0], "accent": _ACCENT})

    if cards:
        cap = max(1, len(scenes) // _CARD_SCENE_DIVISOR)
        cards = cards[:cap]

    return {"cards": cards}
```

with:

```python
def _build_card_plan(
    scenes: list[dict],
    *,
    takeover_cards_enabled: bool = True,
    explainer_cards: list[dict] | None = None,
) -> dict:
    """Deterministic Focused takeover cards from `((question))` markers,
    merged with any AI-planned Explainer cards the caller already fetched
    (`avg.pipeline.phases.director.Director2Phase.run`, via
    `avg.pipeline.prompts.build_explainer_prompt` - a separate AI call,
    not made here, so this stays a pure function). See module docstring.

    `takeover_cards_enabled=False` is the master switch: no card of any
    kind. The caller should skip the Explainer AI call entirely when this
    is off, rather than fetch cards this function would just discard -
    `Director2Phase.run` already does.

    One card per scene: when a Focused and an Explainer candidate land on
    the same scene, Focused wins - a deterministic, author-placed marker
    is a stronger editorial signal than an AI guess. Ported from
    `Export-CardPlan`'s own dedup (`D:\\AI_Video_Gen\\modules\\ai.ps1`).
    The frequency cap applies to the MERGED set, in insertion order
    (Focused cards are inserted first, so a Focused card always survives
    the cap over an Explainer one on a different scene, matching
    `Export-CardPlan`'s own ordered-dict behaviour). No `start`/`end` yet;
    Captions (Phase 6) adds those.
    """
    if not takeover_cards_enabled:
        return {"cards": []}

    by_scene: dict[int, dict] = {}
    for i, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        text = str(scene.get("text") or "")
        questions = extract_question_markers(text)
        if not questions:
            continue
        # One card per scene at most - a scene rarely needs two boards.
        by_scene[i] = {"element": "focused", "scene": i, "text": questions[0], "accent": _ACCENT}

    for card in explainer_cards or []:
        scene_num = card.get("scene")
        if scene_num not in by_scene:
            by_scene[scene_num] = card

    cards = list(by_scene.values())
    if cards:
        cap = max(1, len(scenes) // _CARD_SCENE_DIVISOR)
        cards = cards[:cap]

    return {"cards": cards}
```

### Step 4.3 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_polish.py -q`
Expected: PASS — this now also exercises Task 3's tests (both tasks'
tests were written before either was verified, per the ordering note at
the end of Task 3). Confirm every pre-existing `_build_card_plan`/
`Director2Phase` test still passes: none pass `explainer_cards` or set
`explainer_enabled`, so every existing call gets `takeover_cards_enabled=
True` (the default, matching today's always-on behaviour) and an empty
`explainer_cards`, producing byte-identical output to before this task.

Then the whole suite:

Run: `.venv-linux/bin/python -m pytest tests -q`
Expected: PASS at this sandbox's documented baseline.

### Step 4.4 — commit

Commit Tasks 3 and 4 together, in that dependency order, as one unit
(the ordering note in Task 3 explains why they cannot be verified
independently):

```bash
git add avg/pipeline/prompts.py avg/pipeline/phases/director.py \
  tests/pipeline/test_prompts.py tests/pipeline/test_phases_polish.py
git commit -m "feat: Explainer AI card call + merge into card_plan

New director2_explainer router phase key, gated on takeover_cards_enabled
AND explainer_enabled AND a non-zero scene-count budget. Failure
degrades to zero explainer cards, never fails the phase - same
best-effort pattern _draw_overlay_assets already uses. _build_card_plan
merges Focused + Explainer with 'Focused wins' dedup (ported from
Export-CardPlan) and applies the existing frequency cap to the merged
set rather than Focused alone."
```

---

## Self-review (performed while writing this plan)

**Spec coverage** — §1 → Task 1, §2 → Task 2, §3 → Tasks 3+4, §4 → Task
4, §5 (data/schema) → covered across all four, §6 (testing) → each
task's own test step, §7 (out of scope) → untouched.

**Deviation from a clean one-task-per-commit rhythm, and why:** Tasks 3
and 4 cannot be independently verified — Task 3's `Director2Phase.run`
change calls `_build_card_plan` with keyword arguments Task 4's signature
change is what adds. Splitting them into two commits would mean an
intermediate commit that fails its own test run, which
`superpowers:verification-before-completion` and this plan's own "run to
verify pass" step both require to be green before a commit. Task 3's
Step 3.3 explicitly defers running tests until Task 4's implementation
exists, and both are committed together in Task 4's Step 4.4. This is
flagged rather than silently done, per the "no placeholders" self-review
rule — an implementer following this plan literally task-by-task needs to
know Step 3.3 is deliberately a no-op, not a mistake to "fix" by running
it anyway.

**Type consistency:** `explainer_cards` is `list[dict]` with keys
`{"element": "explainer", "scene": int, "text": str, "accent": str}` from
`_normalize_explainer_card`'s construction (Task 3) through
`_build_card_plan`'s merge loop (Task 4) — the exact same shape
`_build_card_plan` already builds for a Focused card, so the merge never
needs to special-case one shape against the other. `video_map["parts"]`
is `list[dict]` with keys `{"after_scene", "title", "sub",
"duration_sec"}` from `_normalize_part_entry`'s construction through
`build_timeline`'s consumption — confirmed identical to what
`build_timeline` already reads by tracing its own source, not assumed
from the spec's prose.

**Placeholder scan:** no "TBD"/"similar to Task N" as an implementation
instruction anywhere above. Task 3's Step 3.3 is a deliberate, explained
exception (see above), not an unresolved placeholder.
