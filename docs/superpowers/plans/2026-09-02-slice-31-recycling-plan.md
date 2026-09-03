# Slice 31 (sub-slice 2) — Image Recycling: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** let Script decide, per scene, to reuse a hand-picked pool image
instead of writing a fresh prompt; let Visuals resolve that decision with a
file copy instead of a provider call. Composite multi-scene grids are
explicitly out of scope (dropped from the parity effort entirely).

**Architecture:** a new global, hand-maintained pool under `UserAssets`
(no in-app editor — legacy never had one either); a per-project snapshot
(`recycle_pool.json`) so a `[RECYCLE #k]` index means the same image
regardless of what the global pool looks like later; Script's segmentation
prompt gains pool-aware wording only when there's something to stitch;
Visuals recognises the canonical token and copies instead of generating,
falling back to generation on any resolution failure.

**Tech Stack:** Python 3, pytest, Pillow (`Image.verify()` for pool-file
validation, already a dependency) — no frontend change.

**Spec:** `docs/superpowers/specs/2026-09-02-slice-31-recycling-design.md`

## Global Constraints

- No live API calls in tests.
- `avg/media/vendor/` is never edited — recycling never reaches it; it
  only changes what's already in `images/` before render starts.
- Every reader of a channel setting goes through `ctx.channel_values`.
- Every `SettingSpec` requires `invalidates`.
- Tests must not touch the real `%APPDATA%/AVG Studio` or
  `Documents/AVG Studio` — `avg.config.recycle_pool.read_recyclable_pool`
  takes a `root` override for exactly this reason.
- Verify with `.venv-linux/bin/python -m pytest tests -q` in this sandbox.

## Files touched

| File | Change |
|---|---|
| `avg/config/user_assets.py` | +`default_reusable_images_dir()` |
| `avg/config/recycle_pool.py` | New — `read_recyclable_pool()` |
| `avg/core/settings_schema.py` | +2 `SettingSpec`s (`recycle_enabled`, `recycle_ratio`) |
| `avg/pipeline/prompts.py` | `build_scene_segmentation_prompt` gains `recycle_pool`/`recycle_ratio` |
| `avg/pipeline/phases/writing.py` | `ScriptPhase.run`: pool snapshot, pool-aware prompt call, pick normalisation, contract enforcement |
| `avg/pipeline/phases/visuals.py` | `VisualsPhase.run`: recycled-job resolution before queuing generation |
| `tests/config/test_user_assets.py`, new `tests/config/test_recycle_pool.py`, `tests/core/test_settings_schema.py`, `tests/pipeline/test_prompts.py`, `tests/pipeline/test_phases_text.py`, `tests/pipeline/test_phases_media.py` | New/extended tests per task |

Four tasks, strictly ordered — 2 needs 1's settings shape (for its own
tests' realism, not a hard code dependency); 3 needs 1 (pool reading) and
2 (prompt); 4 needs 3 (the `recycle_pool.json` snapshot it reads).

---

## Task 1 — Pool storage, reading, and settings

**Files:**
- Modify: `avg/config/user_assets.py`
- Create: `avg/config/recycle_pool.py`
- Modify: `avg/core/settings_schema.py:598` (after `image_quality`)
- Test: `tests/config/test_user_assets.py`, new `tests/config/test_recycle_pool.py`, `tests/core/test_settings_schema.py`

**Interfaces:**
- Produces: `default_reusable_images_dir() -> Path`,
  `read_recyclable_pool(root: Path | None = None) -> list[dict[str, str]]`
  (`[{"file": <absolute path str>, "description": str}, ...]`),
  `SETTINGS` gains `recycle_enabled`/`recycle_ratio`. Task 3 calls
  `read_recyclable_pool()` with no argument (real pool); its own tests
  call it with `root=tmp_path`.

### Step 1.1 — failing tests

Append to `tests/config/test_user_assets.py` (mirrors
`test_default_errors_path_is_under_logs`'s exact pattern):

```python
def test_default_reusable_images_dir_is_at_the_userassets_root(monkeypatch, tmp_path):
    monkeypatch.setattr(user_assets_module, "user_assets_root", lambda: tmp_path)
    assert user_assets_module.default_reusable_images_dir() == tmp_path / "ReusableImages"
```

Create `tests/config/test_recycle_pool.py`:

```python
"""Tests for avg.config.recycle_pool.read_recyclable_pool - the hand-
maintained image recycling pool `avg.pipeline.phases.writing.ScriptPhase`
reads. Ported from `Get-RecyclableImagePool`
(`D:\\AI_Video_Gen\\modules\\utilities.ps1`): tolerant of everything short
of a well-formed entry whose file actually exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from avg.config.recycle_pool import read_recyclable_pool


def _write_manifest(root: Path, images: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "images.json").write_text(json.dumps({"images": images}), encoding="utf-8")


def test_missing_directory_is_an_empty_pool_not_an_error(tmp_path):
    assert read_recyclable_pool(root=tmp_path / "does_not_exist") == []


def test_missing_manifest_is_an_empty_pool(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    assert read_recyclable_pool(root=tmp_path) == []


def test_malformed_manifest_json_is_an_empty_pool_not_a_crash(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "images.json").write_text("{not valid json", encoding="utf-8")
    assert read_recyclable_pool(root=tmp_path) == []


def test_a_well_formed_entry_whose_file_exists_is_returned(tmp_path):
    (tmp_path / "sunset.jpg").write_bytes(b"fake image bytes")
    _write_manifest(tmp_path, [{"file": "sunset.jpg", "description": "A warm sunset."}])
    pool = read_recyclable_pool(root=tmp_path)
    assert pool == [{"file": str(tmp_path / "sunset.jpg"), "description": "A warm sunset."}]


def test_an_entry_whose_file_does_not_exist_is_skipped(tmp_path):
    _write_manifest(tmp_path, [{"file": "missing.jpg", "description": "Never uploaded."}])
    assert read_recyclable_pool(root=tmp_path) == []


def test_an_entry_missing_file_or_description_is_skipped(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    _write_manifest(
        tmp_path,
        [
            {"file": "a.jpg"},  # no description
            {"description": "no file key at all"},
        ],
    )
    assert read_recyclable_pool(root=tmp_path) == []


def test_manifest_order_is_preserved(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"y")
    _write_manifest(
        tmp_path,
        [{"file": "b.jpg", "description": "second"}, {"file": "a.jpg", "description": "first"}],
    )
    pool = read_recyclable_pool(root=tmp_path)
    assert [p["description"] for p in pool] == ["second", "first"]
```

Append to `tests/core/test_settings_schema.py`:

```python
def test_recycle_settings_exist_with_expected_defaults() -> None:
    by_id = {s.id: s for s in SETTINGS}
    assert by_id["recycle_enabled"].default is True
    assert by_id["recycle_enabled"].type == "bool"
    assert by_id["recycle_ratio"].default == 70
    assert by_id["recycle_ratio"].type == "int"
    for setting_id in ("recycle_enabled", "recycle_ratio"):
        assert by_id[setting_id].group == "images"
        assert by_id[setting_id].scope == "channel"
        assert by_id[setting_id].invalidates == "script"
```

Run: `.venv-linux/bin/python -m pytest tests/config/test_user_assets.py tests/config/test_recycle_pool.py tests/core/test_settings_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'avg.config.recycle_pool'`
and `AttributeError` on `default_reusable_images_dir`.

### Step 1.2 — implement

In `avg/config/user_assets.py`, add after `default_working_models_path`
(match its docstring style exactly):

```python
def default_reusable_images_dir() -> Path:
    """`Documents/AVG Studio/UserAssets/ReusableImages` - the hand-
    maintained image recycling pool `avg.config.recycle_pool.
    read_recyclable_pool` reads (`images.json` plus the image files
    themselves). No in-app editor exists for this folder in either app
    version - see `docs/superpowers/specs/2026-09-02-slice-31-recycling-
    design.md` §1.
    """
    return user_assets_root() / "ReusableImages"
```

Create `avg/config/recycle_pool.py`:

```python
"""The hand-maintained image recycling pool: `avg.pipeline.phases.writing.
ScriptPhase` reads this once per run to offer Director... no, to offer
Pass 2 (scene segmentation) a set of verified images it may reuse instead
of writing a fresh prompt, cutting both API cost and generation time for
the scenes a pool image genuinely fits.

Ported from `Get-RecyclableImagePool`
(`D:\\AI_Video_Gen\\modules\\utilities.ps1`) - same shape, same tolerance.
No in-app editor exists in either app version; the manifest is edited by
hand, same as `dynamic_options.txt`.
"""

from __future__ import annotations

import json
from pathlib import Path

from avg.config.user_assets import default_reusable_images_dir


def read_recyclable_pool(root: Path | None = None) -> list[dict[str, str]]:
    """`[{"file": <absolute path str>, "description": str}, ...]`, in
    manifest order, for every `images.json` entry whose image file
    actually exists on disk.

    Tolerant of everything short of that: a missing directory or
    manifest, unparseable JSON, a manifest that isn't `{"images": [...]}`,
    and an individual entry missing `file`/`description` (either blank)
    are all skipped rather than raised - the feature is a no-op for an
    empty/absent/malformed pool, never a crash, matching every other
    catalogue reader in this codebase (`avg.pipeline.prompts.
    _load_bundled_catalog`, `avg.core.model_cache`).

    `root` overrides `default_reusable_images_dir()` - tests use this to
    avoid the real `Documents/AVG Studio` (`CLAUDE.md`: tests must not
    touch the real user profile).
    """
    d = root if root is not None else default_reusable_images_dir()
    manifest_path = d / "images.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    entries = data.get("images") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []

    pool: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_name = str(entry.get("file") or "").strip()
        description = str(entry.get("description") or "").strip()
        if not file_name or not description:
            continue
        full_path = d / file_name
        if full_path.is_file():
            pool.append({"file": str(full_path), "description": description})
    return pool
```

In `avg/core/settings_schema.py`, replace:

```python
    SettingSpec(
        id="image_quality",
        group="images",
        label="Image quality",
        help="Rendering quality/resolution tier for generated images.",
        type="choice",
        default="medium",
        scope="channel",
        choices=["low", "medium", "high"],
        invalidates="visuals",
    ),
    # --- render engine and quality ---
```

with:

```python
    SettingSpec(
        id="image_quality",
        group="images",
        label="Image quality",
        help="Rendering quality/resolution tier for generated images.",
        type="choice",
        default="medium",
        scope="channel",
        choices=["low", "medium", "high"],
        invalidates="visuals",
    ),
    # --- image recycling (reuse a verified pool image instead of generating) ---
    SettingSpec(
        id="recycle_enabled",
        group="images",
        label="Image recycling",
        help=(
            "Reuse verified images from the pool "
            "(Documents/AVG Studio/UserAssets/ReusableImages) to cut API usage. "
            "A no-op if the pool is empty."
        ),
        type="bool",
        default=True,
        scope="channel",
        invalidates="script",
    ),
    SettingSpec(
        id="recycle_ratio",
        group="images",
        label="Recycled image ratio",
        help=(
            "Target % of scenes to reuse from the pool (needs enough images in it). "
            "0 turns recycling off; 100 reuses every scene and generates nothing."
        ),
        type="int",
        default=70,
        scope="channel",
        invalidates="script",
    ),
    # --- render engine and quality ---
```

### Step 1.3 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/config/test_user_assets.py tests/config/test_recycle_pool.py tests/core/test_settings_schema.py -q`
Expected: PASS.

### Step 1.4 — commit

```bash
git add avg/config/user_assets.py avg/config/recycle_pool.py avg/core/settings_schema.py \
  tests/config/test_user_assets.py tests/config/test_recycle_pool.py tests/core/test_settings_schema.py
git commit -m "feat: image recycling pool storage + recycle_* settings

New UserAssets/ReusableImages/images.json, hand-edited like
dynamic_options.txt - no in-app editor in either app version.
read_recyclable_pool() tolerant of everything short of a well-formed
entry whose file exists, matching Get-RecyclableImagePool."
```

---

## Task 2 — `build_scene_segmentation_prompt` becomes pool-aware

**Files:**
- Modify: `avg/pipeline/prompts.py`
- Test: `tests/pipeline/test_prompts.py`

**Interfaces:**
- Consumes: nothing new (pure string builder, plain `list[dict]` in).
- Produces: `build_scene_segmentation_prompt(..., recycle_pool:
  list[dict] | None = None, recycle_ratio: int = 0)`. Task 3 is the only
  caller passing a real, non-empty `recycle_pool`.

### Step 2.1 — failing tests

Append to `tests/pipeline/test_prompts.py`:

```python
def test_segmentation_prompt_is_unchanged_with_no_pool():
    from avg.pipeline.prompts import build_scene_segmentation_prompt

    prompt = build_scene_segmentation_prompt(_inputs(), "Some narration.")
    assert "RE-USABLE IMAGE POOL" not in prompt
    assert "<the image prompt>" in prompt
    assert "[RECYCLE" not in prompt


def test_segmentation_prompt_is_unchanged_when_ratio_is_zero():
    from avg.pipeline.prompts import build_scene_segmentation_prompt

    pool = [{"index": 1, "description": "A warm sunset."}]
    prompt = build_scene_segmentation_prompt(
        _inputs(), "Some narration.", recycle_pool=pool, recycle_ratio=0
    )
    assert "RE-USABLE IMAGE POOL" not in prompt


def test_segmentation_prompt_partial_mode_lists_the_pool_and_ratio():
    from avg.pipeline.prompts import build_scene_segmentation_prompt

    pool = [{"index": 1, "description": "A warm sunset."}, {"index": 2, "description": "A city skyline."}]
    prompt = build_scene_segmentation_prompt(
        _inputs(), "Some narration.", recycle_pool=pool, recycle_ratio=40
    )
    assert "RE-USABLE IMAGE POOL" in prompt
    assert "#1: A warm sunset." in prompt
    assert "#2: A city skyline." in prompt
    assert "Re-use at most 40% of the scenes." in prompt
    assert "<the image prompt, or [RECYCLE #k]>" in prompt
    assert "assign each scene a re-usable image" not in prompt  # that's "all" mode's job line


def test_segmentation_prompt_all_mode_forces_every_scene_to_recycle():
    from avg.pipeline.prompts import build_scene_segmentation_prompt

    pool = [{"index": 1, "description": "A warm sunset."}]
    prompt = build_scene_segmentation_prompt(
        _inputs(), "Some narration.", recycle_pool=pool, recycle_ratio=100
    )
    assert "ALWAYS the token [RECYCLE #k]" in prompt
    assert "EVERY scene must" in prompt
    assert "Never write a description." in prompt


def test_segmentation_prompt_pool_rules_only_appear_when_pool_is_active():
    from avg.pipeline.prompts import build_scene_segmentation_prompt

    pool = [{"index": 1, "description": "A warm sunset."}]
    prompt = build_scene_segmentation_prompt(
        _inputs(), "Some narration.", recycle_pool=pool, recycle_ratio=50
    )
    assert "Only use image numbers (#k) that appear in the pool above." in prompt
    assert "may serve more than one scene" in prompt
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_prompts.py -q -k segmentation_prompt`
Expected: FAIL — `build_scene_segmentation_prompt() got an unexpected
keyword argument 'recycle_pool'`.

### Step 2.2 — implement

In `avg/pipeline/prompts.py`, replace the whole
`build_scene_segmentation_prompt` function (signature through its closing
`{narration}\"\"\"`) with:

```python
def build_scene_segmentation_prompt(
    inputs: CreativeInputs,
    narration: str,
    *,
    min_scenes: int = 3,
    max_scenes: int = 15,
    exact_scene_count: int = 0,
    extra_style_guide: str = "",
    recycle_pool: list[dict] | None = None,
    recycle_ratio: int = 0,
) -> str:
    """Phase 1 / Pass 2's prompt: split the finished narration into scenes
    and write one image prompt per scene — or, for a scene a pool image
    genuinely fits, the canonical `[RECYCLE #k]` token instead. Ported
    from `Invoke-SceneSegmentation` (`D:\\AI_Video_Gen\\modules\\ai.ps1`),
    including its pool-stitching path.

    Mode is derived here, not passed in as a separate flag (legacy's
    `RecycleMode` parameter): `"off"` when `recycle_pool` is empty/`None`
    or `recycle_ratio <= 0`, `"all"` at `>= 100` (every scene must
    recycle, no prompts written at all), else `"partial"` (the model's own
    judgement, guided by `recycle_ratio`). `"off"` mode's prompt is
    BYTE-IDENTICAL to before this parameter existed — pool wording only
    ever appears when there is something to stitch.

    `extra_style_guide` is an opaque slot (mirrors `SceneCtx` in the old
    prompt, which came from a `scene_descriptions.md` context file this
    task has no copy of) for any additional visual-style prose a caller
    wants appended after the resolved `visual_tone` text.
    """
    tone_text = resolve_visual_tone(inputs.visual_tone)
    style_lock = inputs.style_lock.strip()
    style_tail = f" Append this style tag verbatim at the end: {style_lock}" if style_lock else ""
    count_rule = build_scene_count_rule(min_scenes, max_scenes, exact_scene_count)

    style_guide_lines = [f"Visual tone: {tone_text}"] if tone_text else []
    if extra_style_guide:
        style_guide_lines.append(extra_style_guide)
    style_guide = "\n".join(style_guide_lines) if style_guide_lines else "No specific visual style constraints."

    pool = recycle_pool or []
    use_pool = bool(pool) and recycle_ratio > 0
    mode = "all" if (use_pool and recycle_ratio >= 100) else ("partial" if use_pool else "off")

    pool_section = ""
    job_line = "Below is a finished narration. Split it into scenes and write one image prompt per scene."
    image_rule = (
        f"- an Image line: ONE concrete visual subject suited to a {inputs.aspect_ratio} frame. Describe what\n"
        f"  is literally seen — no metaphors, no text-in-image.{style_tail}\n"
    )
    pool_rules = ""
    image_schema_hint = "<the image prompt>"

    if mode != "off":
        pool_lines = "\n".join(f"#{entry['index']}: {entry['description']}" for entry in pool)
        pool_section = f"\n\n=== RE-USABLE IMAGE POOL (#k : description) ===\n{pool_lines}\n"
        image_schema_hint = "<the image prompt, or [RECYCLE #k]>"
        pool_rules = (
            "- Only use image numbers (#k) that appear in the pool above.\n"
            "- The same pool image MAY serve more than one scene; each scene gets at most one.\n"
        )
        if mode == "all":
            job_line = (
                "Below is a finished narration. Split it into scenes and assign each scene a re-usable image."
            )
            image_rule = (
                "- an Image line: ALWAYS the token [RECYCLE #k] naming the pool image that fits this scene best.\n"
                "  This video re-uses ONLY pooled images — no new images will be generated — so EVERY scene must\n"
                "  get a [RECYCLE #k]. When nothing fits well, still pick the nearest. Never write a description.\n"
            )
        else:
            image_rule = (
                "- an Image line, which is ONE of:\n"
                "    [RECYCLE #k]  — when a pool image GENUINELY fits this scene. Re-using a good match saves\n"
                "                    generation, but a FORCED match looks worse than a fresh image, so only\n"
                f"                    re-use on a strong, obvious fit. Re-use at most {recycle_ratio}% of the scenes.\n"
                f"    a prompt      — otherwise: ONE concrete visual subject suited to a {inputs.aspect_ratio} frame,\n"
                f"                    describing what is literally seen. No metaphors, no text-in-image.{style_tail}\n"
                "  Decide this scene by scene, in order, from the first scene to the last.\n"
            )
            job_line = (
                "Below is a finished narration. Split it into scenes, re-use pool images where they genuinely "
                "fit, and write an image prompt for every other scene."
            )

    return f"""=== VISUAL STYLE GUIDE ===
{style_guide}{pool_section}

=== TASK ===
Topic: "{inputs.topic}"
Image aspect ratio: {inputs.aspect_ratio}

{job_line}

{count_rule}

ABSOLUTE RULE — the narration is FINAL:
- Reproduce every word EXACTLY as written, in the original order.
- Do NOT reword, rewrite, shorten, expand, summarise, or fix anything.
- Do NOT add or drop a single word.
- Preserve any [pause], [[double square]] and ((double parenthesis)) markers exactly where
  they appear. They are delivery cues, not typos.
- Concatenating your scene texts in order must reproduce the narration verbatim.

For each scene also write:
{image_rule}- a Label: one or two words naming the scene's job (Hook, Reveal, Context, Payoff, ...).
{pool_rules}
Output EXACTLY this structure for every scene, in order, and nothing else:

@@SCENE@@
<the verbatim narration words for this scene>
@@IMAGE@@
{image_schema_hint}
@@LABEL@@
<the label>

No preamble, no commentary, no markdown, no code fences, no numbering.

=== NARRATION TO SPLIT ===
{narration}"""
```

### Step 2.3 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_prompts.py -q`
Expected: PASS, including every pre-existing
`build_scene_segmentation_prompt` test (none pass `recycle_pool`, so they
all exercise `mode == "off"`, byte-identical to before).

### Step 2.4 — commit

```bash
git add avg/pipeline/prompts.py tests/pipeline/test_prompts.py
git commit -m "feat: build_scene_segmentation_prompt gains pool-aware modes

off/partial/all wording ported verbatim from Invoke-SceneSegmentation.
off mode (no pool, or recycle_ratio<=0) is byte-identical to before -
every existing caller and every channel with recycling off is
unaffected."
```

---

## Task 3 — `ScriptPhase.run` wires the pool in

**Files:**
- Modify: `avg/pipeline/phases/writing.py`
- Test: `tests/pipeline/test_phases_text.py`

**Interfaces:**
- Consumes: `avg.config.recycle_pool.read_recyclable_pool` (Task 1),
  `build_scene_segmentation_prompt(..., recycle_pool=..., 
  recycle_ratio=...)` (Task 2).
- Produces: `<project>/script/recycle_pool.json` (present only when the
  pool is non-empty and recycling is on). `scenes.json`'s `image_prompt`
  may now be a canonical `"[RECYCLE #k]"` token. Task 4 reads both.

### Step 3.1 — failing tests

Append to `tests/pipeline/test_phases_text.py`:

```python
def test_script_phase_writes_no_recycle_snapshot_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.read_recyclable_pool",
        lambda: [{"file": "/tmp/x.jpg", "description": "d"}],
    )
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, SCENE_BLOCKS]))

    project = _project(tmp_path)
    channel = _channel({"recycle_enabled": False})
    ctx, _ = _ctx(project, channel=channel)
    ScriptPhase().run(ctx)

    assert not (script_dir(ctx) / "recycle_pool.json").exists()


def test_script_phase_writes_no_recycle_snapshot_when_pool_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("avg.pipeline.phases.writing.read_recyclable_pool", lambda: [])
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, SCENE_BLOCKS]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)  # recycle_enabled defaults True, ratio defaults 70
    ScriptPhase().run(ctx)

    assert not (script_dir(ctx) / "recycle_pool.json").exists()


def test_script_phase_writes_the_recycle_snapshot_when_pool_is_active(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.read_recyclable_pool",
        lambda: [{"file": "/tmp/sunset.jpg", "description": "A warm sunset."}],
    )
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, SCENE_BLOCKS]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    ScriptPhase().run(ctx)

    snapshot = read_json(script_dir(ctx) / "recycle_pool.json")
    assert snapshot == [{"index": 1, "file": "/tmp/sunset.jpg", "description": "A warm sunset."}]


def test_script_phase_segmentation_prompt_receives_the_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.read_recyclable_pool",
        lambda: [{"file": "/tmp/sunset.jpg", "description": "A warm sunset."}],
    )
    fake = _QueueGenerator([NARRATION, SCENE_BLOCKS])
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", fake)

    project = _project(tmp_path)
    channel = _channel({"recycle_ratio": 40})
    ctx, _ = _ctx(project, channel=channel)
    ScriptPhase().run(ctx)

    segmentation_prompt = fake.calls[1][1]  # [0] is Pass 1 (narration)
    assert "A warm sunset." in segmentation_prompt
    assert "Re-use at most 40% of the scenes." in segmentation_prompt


def test_script_phase_normalizes_a_loosely_written_recycle_pick(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.read_recyclable_pool",
        lambda: [{"file": "/tmp/sunset.jpg", "description": "A warm sunset."}],
    )
    loose_blocks = SCENE_BLOCKS.replace(
        "A weathered bronze mechanism glowing under lantern light", "Use [recycle #1] for this one"
    )
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, loose_blocks])
    )

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    ScriptPhase().run(ctx)

    scenes = read_json(script_dir(ctx) / "scenes.json")
    assert scenes[0]["image_prompt"] == "[RECYCLE #1]"


def test_script_phase_drops_an_out_of_range_recycle_pick(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.read_recyclable_pool",
        lambda: [{"file": "/tmp/sunset.jpg", "description": "A warm sunset."}],
    )
    bad_blocks = SCENE_BLOCKS.replace(
        "A weathered bronze mechanism glowing under lantern light", "[RECYCLE #99]"
    )
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, bad_blocks]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    ScriptPhase().run(ctx)

    scenes = read_json(script_dir(ctx) / "scenes.json")
    assert scenes[0]["image_prompt"] == ""


def test_script_phase_all_mode_force_fills_every_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.read_recyclable_pool",
        lambda: [{"file": "/tmp/sunset.jpg", "description": "A warm sunset."}],
    )
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, SCENE_BLOCKS]))

    project = _project(tmp_path)
    channel = _channel({"recycle_ratio": 100})
    ctx, _ = _ctx(project, channel=channel)
    ScriptPhase().run(ctx)

    scenes = read_json(script_dir(ctx) / "scenes.json")
    assert all(s["image_prompt"] == "[RECYCLE #1]" for s in scenes)


def test_script_phase_partial_mode_emits_an_event_when_over_the_guided_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.read_recyclable_pool",
        lambda: [{"file": "/tmp/sunset.jpg", "description": "A warm sunset."}],
    )
    both_recycled = SCENE_BLOCKS.replace(
        "A weathered bronze mechanism glowing under lantern light", "[RECYCLE #1]"
    ).replace("Ancient gears turning in golden light", "[RECYCLE #1]")
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, both_recycled])
    )

    project = _project(tmp_path)
    # ratio=10 on 2 scenes -> cap is 0, so even one recycled scene is "over".
    channel = _channel({"recycle_ratio": 10})
    ctx, events = _ctx(project, channel=channel)
    ScriptPhase().run(ctx)

    scenes = read_json(script_dir(ctx) / "scenes.json")
    assert scenes[0]["image_prompt"] == "[RECYCLE #1]"  # kept, not trimmed
    assert any("over the 10% guidance" in getattr(e, "message", "") for e in events)
```

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_text.py -q -k recycle`
Expected: FAIL — `AttributeError: <module 'avg.pipeline.phases.writing'>
does not have the attribute 'read_recyclable_pool'`.

### Step 3.2 — implement

In `avg/pipeline/phases/writing.py`, extend the import block. Replace:

```python
from avg.pipeline.prompts import (
    build_brief_prompt,
    build_narration_prompt,
    build_research_prompt,
    build_scene_segmentation_prompt,
)
from avg.providers.text.router import generate as generate_text
```

with:

```python
from avg.config.recycle_pool import read_recyclable_pool
from avg.pipeline.prompts import (
    build_brief_prompt,
    build_narration_prompt,
    build_research_prompt,
    build_scene_segmentation_prompt,
)
from avg.providers.text.router import generate as generate_text
```

Add `re` to the standard-library imports at the top of the file if it
isn't already there:

```bash
grep -n "^import re" avg/pipeline/phases/writing.py || sed -i '/^from __future__/a\\nimport re' avg/pipeline/phases/writing.py
```

Add three module-level helpers in the "── Script Phase ──" section,
directly above `class ScriptPhase:`:

```python
_RECYCLE_PICK_RE = re.compile(r"(?i)\[?\s*recycle\s*#?\s*(\d+)\s*\]?")
_CANONICAL_RECYCLE_RE = re.compile(r"^\[RECYCLE #\d+\]$")


def _normalize_recycle_picks(scenes: list[dict], pool_size: int) -> None:
    """Mutates each scene's `image_prompt` in place: a loosely-written
    recycle pick ("Use [RECYCLE #3]", "[recycle #3]", "RECYCLE #3")
    becomes the canonical `"[RECYCLE #3]"` token every downstream reader
    (`avg.pipeline.phases.visuals`) expects; an out-of-range pick is
    stripped instead, leaving whatever prose (if any) came with it. Only
    called when pool mode is active — `pool_size` is always > 0 then.
    Ported from `Invoke-SceneSegmentation`'s own post-parse normalisation
    (`D:\\AI_Video_Gen\\modules\\ai.ps1`)."""
    for scene in scenes:
        match = _RECYCLE_PICK_RE.search(scene["image_prompt"])
        if not match:
            continue
        k = int(match.group(1))
        if 1 <= k <= pool_size:
            scene["image_prompt"] = f"[RECYCLE #{k}]"
        else:
            scene["image_prompt"] = _RECYCLE_PICK_RE.sub("", scene["image_prompt"]).strip()


def _force_fill_all_mode(scenes: list[dict], pool_size: int) -> None:
    """`"all"` mode's hard contract: every scene not already carrying a
    valid `[RECYCLE #k]` after `_normalize_recycle_picks` is force-filled
    by cycling the pool round-robin. Always succeeds — `"all"` mode
    requires a non-empty pool to have been entered in the first place.
    Ported from the `$rr % $poolCount` force-fill in `ai.ps1`."""
    n = 0
    for scene in scenes:
        if _CANONICAL_RECYCLE_RE.match(scene["image_prompt"]):
            continue
        k = (n % pool_size) + 1
        n += 1
        scene["image_prompt"] = f"[RECYCLE #{k}]"
```

Replace `ScriptPhase.run`'s body from the invariant check onward. Replace:

```python
        # The invariant: scenes, concatenated in order, must reproduce the
        # narration - whitespace-normalised, nothing else. A model that
        # rewords/reorders/drops/pads a scene fails here, not silently.
        reconstructed = _normalize_whitespace(" ".join(scene["text"] for scene in scenes))
        if reconstructed != _normalize_whitespace(narration):
            raise AvgError(
                ErrorKind.bug,
                "script phase: pass 2's scenes are not a verbatim partition of the pass 1 narration",
            )

        sdir = script_dir(ctx)
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "narration.txt").write_text(narration, encoding="utf-8")
        write_json(
            sdir / "scenes.json",
            [
                {
                    "index": i + 1,
                    "text": scene["text"],
                    "image_prompt": scene["image_prompt"],
                    "label": scene["label"],
                }
                for i, scene in enumerate(scenes)
            ],
        )
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message="Script complete."))
```

with:

```python
        # The invariant: scenes, concatenated in order, must reproduce the
        # narration - whitespace-normalised, nothing else. A model that
        # rewords/reorders/drops/pads a scene fails here, not silently.
        reconstructed = _normalize_whitespace(" ".join(scene["text"] for scene in scenes))
        if reconstructed != _normalize_whitespace(narration):
            raise AvgError(
                ErrorKind.bug,
                "script phase: pass 2's scenes are not a verbatim partition of the pass 1 narration",
            )

        sdir = script_dir(ctx)
        sdir.mkdir(parents=True, exist_ok=True)

        # ── Image recycling: pick normalisation + contract enforcement ──
        # Reading the pool and building the prompt happened above, before
        # Pass 2's call - see the pool-aware segmentation_prompt already
        # built. This block only sanitises what the model sent back.
        if pool_snapshot:
            write_json(sdir / "recycle_pool.json", pool_snapshot)
            _normalize_recycle_picks(scenes, len(pool_snapshot))
            if recycle_ratio >= 100:
                _force_fill_all_mode(scenes, len(pool_snapshot))
            else:
                used = sum(1 for s in scenes if _CANONICAL_RECYCLE_RE.match(s["image_prompt"]))
                cap = int(len(scenes) * recycle_ratio / 100)
                if used > cap:
                    ctx.emit(
                        ProgressEvent(
                            project_id=ctx.project.id,
                            percent=90,
                            message=(
                                f"Recycled {used}/{len(scenes)} scene(s), over the {recycle_ratio}% "
                                "guidance - kept (trimming would leave a scene with no image prompt)."
                            ),
                        )
                    )

        (sdir / "narration.txt").write_text(narration, encoding="utf-8")
        write_json(
            sdir / "scenes.json",
            [
                {
                    "index": i + 1,
                    "text": scene["text"],
                    "image_prompt": scene["image_prompt"],
                    "label": scene["label"],
                }
                for i, scene in enumerate(scenes)
            ],
        )
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message="Script complete."))
```

That references `pool_snapshot`/`recycle_ratio`, computed BEFORE Pass 2's
prompt is built (the pool has to exist before the prompt can mention it).
Replace Pass 2's own block:

```python
        # ── Pass 2: verbatim segmentation ───────────────────────────────
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=50, message="Segmenting into scenes..."))
        segmentation_prompt = build_scene_segmentation_prompt(
            inputs, narration, min_scenes=_MIN_SCENES, max_scenes=_MAX_SCENES
        )
        segmentation_result = generate_text(segmentation_prompt, "script_segmentation", config=ctx.router)
```

with:

```python
        # ── Pass 2: verbatim segmentation ───────────────────────────────
        settings = ctx.channel_values
        try:
            recycle_ratio = int(settings.get("recycle_ratio", 70))
        except (TypeError, ValueError):
            recycle_ratio = 70
        recycle_ratio = max(0, min(100, recycle_ratio))
        pool_snapshot: list[dict] = []
        if bool(settings.get("recycle_enabled", True)) and recycle_ratio > 0:
            pool = read_recyclable_pool()
            pool_snapshot = [
                {"index": i + 1, "file": entry["file"], "description": entry["description"]}
                for i, entry in enumerate(pool)
            ]

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=50, message="Segmenting into scenes..."))
        segmentation_prompt = build_scene_segmentation_prompt(
            inputs,
            narration,
            min_scenes=_MIN_SCENES,
            max_scenes=_MAX_SCENES,
            recycle_pool=pool_snapshot,
            recycle_ratio=recycle_ratio,
        )
        segmentation_result = generate_text(segmentation_prompt, "script_segmentation", config=ctx.router)
```

### Step 3.3 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_text.py -q`
Expected: PASS, including every pre-existing `ScriptPhase` test — none of
them set `recycle_*` settings or monkeypatch `read_recyclable_pool`, so
`read_recyclable_pool()` hits the REAL (empty, in this sandbox)
`Documents/AVG Studio/UserAssets/ReusableImages`, giving `pool_snapshot =
[]` and therefore byte-identical prompts/behaviour to before this task.

### Step 3.4 — commit

```bash
git add avg/pipeline/phases/writing.py tests/pipeline/test_phases_text.py
git commit -m "feat: ScriptPhase wires the recycle pool into Pass 2

Snapshot written only when recycling is on and the pool isn't empty.
Pick normalisation ported verbatim from Invoke-SceneSegmentation's own
post-parse block: loose-match/strict-rewrite, out-of-range drop, all-
mode force-fill, partial-mode soft-cap logged (never trimmed)."
```

---

## Task 4 — `VisualsPhase` resolves a recycled scene

**Files:**
- Modify: `avg/pipeline/phases/visuals.py`
- Test: `tests/pipeline/test_phases_media.py`

**Interfaces:**
- Consumes: `<project>/script/recycle_pool.json` (Task 3).
- Produces: a recycled scene's `images/{i:03d}.png` is a verbatim copy,
  never a provider call; `images/manifest.json` and `render/index.json`
  are unaffected in shape (a recycled scene's `_SceneResult` looks exactly
  like a generated one from their point of view).

### Step 4.1 — check the existing test file's fixtures first

```bash
grep -n "def _project\|def _ctx\|def _channel\|class Test\|def test_" tests/pipeline/test_phases_media.py | head -40
```

Match whatever `_project`/`_ctx`/`_channel` already look like there (this
file, unlike `test_phases_text.py`/`test_phases_polish.py`, may have its
own slightly different scaffolding for `VisualsPhase` — mirror it exactly
rather than assuming the shape from the other two files).

### Step 4.2 — failing tests

Append to `tests/pipeline/test_phases_media.py` (adjust fixture calls to
match Step 4.1's findings):

```python
def test_visuals_phase_recycles_instead_of_generating(tmp_path, monkeypatch):
    from avg.pipeline.phases import visuals as visuals_module

    pool_image = tmp_path / "pool_sunset.jpg"
    pool_image.write_bytes(_tiny_valid_png_bytes())  # see helper below

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    write_json(
        script_dir(ctx) / "scenes.json",
        [{"index": 1, "text": "Scene one.", "image_prompt": "[RECYCLE #1]", "label": "Hook"}],
    )
    write_json(
        script_dir(ctx) / "recycle_pool.json",
        [{"index": 1, "file": str(pool_image), "description": "A warm sunset."}],
    )

    called = []
    monkeypatch.setattr(visuals_module, "_generate_one", lambda *a, **k: called.append(1) or None)

    visuals_module.VisualsPhase().run(ctx)

    assert not called  # no provider call at all
    out = images_dir_for(ctx) / "001.png"
    assert out.is_file()
    assert out.read_bytes() == pool_image.read_bytes()
    manifest = read_json(images_dir_for(ctx) / "manifest.json")
    assert manifest["scenes"][0]["status"] == "ok"


def test_visuals_phase_falls_back_to_generation_on_out_of_range_pick(tmp_path, monkeypatch):
    from avg.pipeline.phases import visuals as visuals_module

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    write_json(
        script_dir(ctx) / "scenes.json",
        [{"index": 1, "text": "Scene one.", "image_prompt": "[RECYCLE #99]", "label": "Hook"}],
    )
    write_json(
        script_dir(ctx) / "recycle_pool.json",
        [{"index": 1, "file": str(tmp_path / "sunset.jpg"), "description": "A warm sunset."}],
    )

    captured_prompts = []

    def _fake_generate_one(provider, prompt, aspect_ratio, model, scene, out_path):
        captured_prompts.append(prompt)
        out_path.write_bytes(b"generated")
        from avg.pipeline.phases.visuals import _SceneResult

        return _SceneResult(scene=scene, ok=True, file=out_path.name)

    monkeypatch.setattr(visuals_module, "_generate_one", _fake_generate_one)

    visuals_module.VisualsPhase().run(ctx)

    assert captured_prompts == ["A warm sunset."]  # pool description, not a generic fallback


def test_visuals_phase_falls_back_when_the_pool_file_is_not_a_real_image(tmp_path, monkeypatch):
    from avg.pipeline.phases import visuals as visuals_module

    bad_image = tmp_path / "corrupt.jpg"
    bad_image.write_bytes(b"not actually an image")

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    write_json(
        script_dir(ctx) / "scenes.json",
        [{"index": 1, "text": "Scene one.", "image_prompt": "[RECYCLE #1]", "label": "Hook"}],
    )
    write_json(
        script_dir(ctx) / "recycle_pool.json",
        [{"index": 1, "file": str(bad_image), "description": "A warm sunset."}],
    )

    def _fake_generate_one(provider, prompt, aspect_ratio, model, scene, out_path):
        out_path.write_bytes(b"generated")
        from avg.pipeline.phases.visuals import _SceneResult

        return _SceneResult(scene=scene, ok=True, file=out_path.name)

    monkeypatch.setattr(visuals_module, "_generate_one", _fake_generate_one)

    visuals_module.VisualsPhase().run(ctx)

    out = images_dir_for(ctx) / "001.png"
    assert out.read_bytes() == b"generated"  # fell back, didn't copy the corrupt file


def test_visuals_phase_a_scene_with_no_recycle_marker_is_unaffected(tmp_path, monkeypatch):
    from avg.pipeline.phases import visuals as visuals_module

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    write_json(
        script_dir(ctx) / "scenes.json",
        [{"index": 1, "text": "Scene one.", "image_prompt": "A regular prompt.", "label": "Hook"}],
    )
    # No recycle_pool.json at all - matches "recycling was off" exactly.

    def _fake_generate_one(provider, prompt, aspect_ratio, model, scene, out_path):
        out_path.write_bytes(b"generated")
        from avg.pipeline.phases.visuals import _SceneResult

        return _SceneResult(scene=scene, ok=True, file=out_path.name)

    monkeypatch.setattr(visuals_module, "_generate_one", _fake_generate_one)

    visuals_module.VisualsPhase().run(ctx)

    out = images_dir_for(ctx) / "001.png"
    assert out.read_bytes() == b"generated"
```

Add a small helper near the top of the test file (or reuse one if this
file already has a tiny-valid-image fixture — check first):

```python
def _tiny_valid_png_bytes() -> bytes:
    """A real, 1x1 PNG - `Image.open(...).verify()` must accept it, unlike
    an arbitrary byte string."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return buf.getvalue()
```

Also confirm `images_dir_for`/`script_dir`/`write_json`/`read_json` are
already imported in this file at Step 4.1; add whichever aren't.

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_media.py -q -k recycl`
Expected: FAIL — the recycled scene still goes through `_generate_one`
(the `called`/provider-call assertion fails; `image_prompt` is still the
literal `"[RECYCLE #1]"` string sent to the provider as a real prompt).

### Step 4.3 — implement

In `avg/pipeline/phases/visuals.py`, extend the imports. Find the current
top-of-file import block and add:

```python
import re
import shutil

from PIL import Image
```

(placed with the other standard-library imports at the top, `PIL` grouped
with third-party imports below them, matching whatever import-grouping
convention `oxlint`'s Python-adjacent tooling — actually `ruff`/whatever
this file's existing imports already follow — check the top of the file
and slot in accordingly rather than guessing an order).

Add near `_scenes_json_path`, after it:

```python
_RECYCLE_TOKEN_RE = re.compile(r"^\[RECYCLE #(\d+)\]$")


def _recycle_pool_path(ctx: PhaseContext) -> Path:
    return script_dir(ctx) / "recycle_pool.json"


def _pool_entry(index: int, pool_snapshot: list) -> dict | None:
    """The `recycle_pool.json` snapshot's own 1-indexed entry for
    `index`, or `None` for an out-of-range/malformed one."""
    if not isinstance(pool_snapshot, list) or index < 1 or index > len(pool_snapshot):
        return None
    entry = pool_snapshot[index - 1]
    return entry if isinstance(entry, dict) else None


def _is_valid_image(path: Path) -> bool:
    """A real, openable image file - Pillow's own `verify()`, which checks
    the file isn't corrupt/truncated/not-an-image without decoding full
    pixel data. Ported from legacy's `Test-ValidImageFile`: a corrupt or
    non-image pool entry must fall back to generation, never poison a
    render."""
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


def _resolve_recycled_scene(index: int, pool_snapshot: list, images_dir: Path, scene: int) -> "_SceneResult | None":
    """Copy the pool image `[RECYCLE #index]` names straight to
    `images/{scene:03d}.png`, no provider call — or `None` if the
    snapshot/file/validity check fails, in which case the caller falls
    back to generation. Ported from the B2.15 recycle-copy block
    (`D:\\AI_Video_Gen\\modules\\visuals.ps1`)."""
    entry = _pool_entry(index, pool_snapshot)
    if entry is None:
        return None
    src = Path(str(entry.get("file") or ""))
    if not src.is_file() or not _is_valid_image(src):
        return None
    out_path = images_dir / f"{scene:03d}.png"
    try:
        shutil.copy2(src, out_path)
    except OSError:
        return None
    return _SceneResult(scene=scene, ok=True, file=out_path.name)
```

(`_scenes_json_path` needs to already be defined above this point in the
file — it is; this block goes directly after it, before `_SceneResult`'s
own class definition needs to exist first for the type hint to resolve at
runtime as a string literal, which is why it's quoted above — no import
ordering problem.)

Now replace `VisualsPhase.run`'s opening section. Replace:

```python
    def run(self, ctx: PhaseContext) -> None:
        scenes = read_json(_scenes_json_path(ctx))
        if not isinstance(scenes, list) or not scenes:
            raise AvgError(ErrorKind.bug, "visuals phase: no finished scenes.json to generate images from")

        jobs: list[tuple[int, str]] = []
        for i, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                continue
            prompt = str(scene.get("image_prompt") or scene.get("text") or "").strip()
            if not prompt:
                prompt = f"{ctx.project.title}, scene {i}"
            jobs.append((i, prompt))
        if not jobs:
            raise AvgError(ErrorKind.bug, "visuals phase: scenes.json had no usable scene entries")

        settings = ctx.channel_values
        provider_name = str(settings.get("image_provider") or _DEFAULT_PROVIDER)
        model = str(settings.get("image_model") or _DEFAULT_MODEL)
        aspect_ratio = ctx.project.aspect_ratio

        transport = ctx.router if isinstance(ctx.router, Transport) else None
        provider = _build_provider(provider_name, transport)

        images_dir = _images_dir(ctx)
        images_dir.mkdir(parents=True, exist_ok=True)

        total = len(jobs)
        workers = max(1, min(_worker_cap(_total_ram_gb()), total))

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message=f"Generating {total} image(s)..."))
```

with:

```python
    def run(self, ctx: PhaseContext) -> None:
        scenes = read_json(_scenes_json_path(ctx))
        if not isinstance(scenes, list) or not scenes:
            raise AvgError(ErrorKind.bug, "visuals phase: no finished scenes.json to generate images from")

        images_dir = _images_dir(ctx)
        images_dir.mkdir(parents=True, exist_ok=True)
        pool_snapshot = read_json(_recycle_pool_path(ctx))
        if not isinstance(pool_snapshot, list):
            pool_snapshot = []

        jobs: list[tuple[int, str]] = []
        recycled: dict[int, _SceneResult] = {}
        for i, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                continue
            prompt = str(scene.get("image_prompt") or scene.get("text") or "").strip()
            recycle_match = _RECYCLE_TOKEN_RE.match(prompt)
            if recycle_match:
                k = int(recycle_match.group(1))
                result = _resolve_recycled_scene(k, pool_snapshot, images_dir, i)
                if result is not None:
                    recycled[i] = result
                    continue
                # Resolution failed (bad index / missing / corrupt file) -
                # fall back to generation, using the pool entry's own
                # description when the snapshot still names one - richer
                # than a blank scene, same choice legacy makes.
                entry = _pool_entry(k, pool_snapshot)
                prompt = str((entry or {}).get("description") or "").strip()
            if not prompt:
                prompt = f"{ctx.project.title}, scene {i}"
            jobs.append((i, prompt))
        if not jobs and not recycled:
            raise AvgError(ErrorKind.bug, "visuals phase: scenes.json had no usable scene entries")

        settings = ctx.channel_values
        provider_name = str(settings.get("image_provider") or _DEFAULT_PROVIDER)
        model = str(settings.get("image_model") or _DEFAULT_MODEL)
        aspect_ratio = ctx.project.aspect_ratio

        transport = ctx.router if isinstance(ctx.router, Transport) else None
        provider = _build_provider(provider_name, transport)

        total = len(jobs) + len(recycled)
        workers = max(1, min(_worker_cap(_total_ram_gb()), len(jobs)))

        ctx.emit(
            ProgressEvent(project_id=ctx.project.id, percent=0, message=f"Generating {len(jobs)} image(s)...")
        )
```

Then replace the generation-and-fallback block. Replace:

```python
        results = _generate_all(provider, model)

        primary_provider_name = provider_name
        primary_kind_label = ""
        fell_back = False
        if not any(r.ok for r in results.values()):
            # Exactly once, never chained - the same rule the text router
            # inherited from the legacy `$triedFallback` flag.
            backup_provider = str(settings.get("image_provider_backup") or "")
            backup_model = str(settings.get("image_model_backup") or "")
            on_failure = str(settings.get("image_on_failure") or "stop")
            kind = _dominant_kind(results)
            if (
                on_failure == "fall_back"
                and backup_provider
                and backup_model
                and backup_provider != provider_name
                and _should_fall_back(kind)
            ):
                ctx.emit(
                    ProgressEvent(
                        project_id=ctx.project.id,
                        percent=0,
                        message=f"Falling back to {backup_provider}/{backup_model}...",
                    )
                )
                primary_kind_label = kind.value
                fell_back = True
                provider_name, model = backup_provider, backup_model
                results = _generate_all(_build_provider(backup_provider, transport), backup_model)

        ok_count = sum(1 for r in results.values() if r.ok)
```

with:

```python
        gen_results = _generate_all(provider, model)

        primary_provider_name = provider_name
        primary_kind_label = ""
        fell_back = False
        if jobs and not any(r.ok for r in gen_results.values()):
            # Exactly once, never chained - the same rule the text router
            # inherited from the legacy `$triedFallback` flag. Gated on
            # `jobs` being non-empty: an all-recycled run has nothing to
            # have failed, and must never trigger a fallback provider it
            # was never going to need.
            backup_provider = str(settings.get("image_provider_backup") or "")
            backup_model = str(settings.get("image_model_backup") or "")
            on_failure = str(settings.get("image_on_failure") or "stop")
            kind = _dominant_kind(gen_results)
            if (
                on_failure == "fall_back"
                and backup_provider
                and backup_model
                and backup_provider != provider_name
                and _should_fall_back(kind)
            ):
                ctx.emit(
                    ProgressEvent(
                        project_id=ctx.project.id,
                        percent=0,
                        message=f"Falling back to {backup_provider}/{backup_model}...",
                    )
                )
                primary_kind_label = kind.value
                fell_back = True
                provider_name, model = backup_provider, backup_model
                gen_results = _generate_all(_build_provider(backup_provider, transport), backup_model)

        results: dict[int, _SceneResult] = {**recycled, **gen_results}
        ok_count = sum(1 for r in results.values() if r.ok)
```

Everything after this point (`if ok_count == 0: ...`,
`if ok_count < total: ...`, the manifest/index write, the final
`ProgressEvent`) reads `results`/`total`/`ok_count` exactly as it already
does — no further change needed; `total` now correctly means "every
scene, recycled or generated" everywhere it's read downstream.

### Step 4.4 — run to verify pass

Run: `.venv-linux/bin/python -m pytest tests/pipeline/test_phases_media.py -q`
Expected: PASS, including every pre-existing `VisualsPhase` test — none of
them write a `recycle_pool.json` or an `image_prompt` matching the
canonical token, so `pool_snapshot` is always `[]` and every scene takes
the same path it did before this task (`recycled` stays empty, `jobs`
unchanged, `total = len(jobs) + 0`).

Then the whole suite:

Run: `.venv-linux/bin/python -m pytest tests -q`
Expected: PASS at this sandbox's documented baseline (pre-existing
DPAPI/font-related failures only — nothing from this plan adds to that
count).

### Step 4.5 — commit

```bash
git add avg/pipeline/phases/visuals.py tests/pipeline/test_phases_media.py
git commit -m "feat: VisualsPhase resolves a recycled scene without generating

Copy (with a real-image validity check) instead of a provider call for
a canonical [RECYCLE #k] scene. Any resolution failure falls back to
generation using the pool entry's own description. The provider-
fallback trigger is now gated on jobs being non-empty, so an
all-recycled run can never mistake 'nothing was attempted' for
'everything failed'."
```

---

## Self-review (performed while writing this plan)

**Spec coverage** — spec §1 → Task 1, §2 → Task 1, §3 → Task 3 (snapshot
half), §4 → Task 2, §5 → Task 3 (normalisation half), §6 → Task 4, §7
(data/schema table) → covered across all four, §8 (testing) → each task's
own test step, §9 (out of scope) → untouched.

**A subtlety caught while tracing the actual `VisualsPhase.run` code, not
apparent from the spec's prose alone:** the existing single-provider
"nothing succeeded, try the backup" check (`if not any(r.ok for r in 
results.values())`) is true both when everything failed AND when nothing
was attempted at all (`any([])` is `False` either way). A 100%-recycle run
with zero generation jobs would have silently satisfied that condition and
triggered a pointless (and, if a backup provider is configured, possibly
costly) fallback attempt for a run that never needed the image provider in
the first place. Task 4 gates the check on `jobs` being non-empty — a
one-line fix caught only by reading the exact boolean logic, not visible
from the spec's higher-level description of "resolve a recycled scene."

**Placeholder scan:** no "TBD"/"similar to Task N" anywhere above; every
step carries real code or an exact shell command. Task 4's "check the
existing test file's fixtures first" step is a deliberate exception — this
plan was written without opening `tests/pipeline/test_phases_media.py`
directly (only `test_phases_text.py`/`test_phases_polish.py` were used to
establish the harness pattern), so Step 4.1 is an explicit, bounded
verification step, not an unresolved placeholder: it tells the
implementer exactly what to check and why before writing Step 4.2's tests
against whatever fixtures that file actually has.

**Type consistency:** `recycle_pool`/`pool_snapshot` is
`list[dict[str, str | int]]` (`{"index", "file", "description"}`)
consistently from Task 1's `read_recyclable_pool` (which returns
`{"file", "description"}` only — Task 3 adds the `"index"` key when
building the snapshot) through Task 2's prompt parameter and Task 4's
lookup. `_SceneResult` — Task 4's `_resolve_recycled_scene` constructs the
exact same dataclass `_generate_one` already returns, so every downstream
consumer (`manifest.json`, `index_entries`, the completeness check)
handles a recycled scene identically to a generated one without any
special-casing beyond where `results` is assembled.
