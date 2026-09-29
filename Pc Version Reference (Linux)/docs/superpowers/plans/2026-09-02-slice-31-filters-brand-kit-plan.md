# Slice 31 (sub-slice 1) — Filters + Brand Kit: Implementation Plan (Linux port)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** give Director II real filter overlays and a brand preset that
actually changes the video's colour and camera feel, closing the two
Director II gaps found by the legacy-parity comparison.

**Linux port note (2026-09-06):** ported from the `main`/`development`
(Windows) plan of the same name. `avg/pipeline/`, `avg/core/`, and
`avg/media/overlays.py` are byte-identical between branches (confirmed by
diff, not assumed), so every task below except Task 2 (environment.py)
and the new Task 1B (a real, pre-existing packaging gap found while
porting this plan — see Task 1B) applies unchanged, replace-block for
replace-block, against `linux-port`. Every `Run:`/`Verify with` command
below uses this branch's own native venv, not the Windows-port sandbox's
`.venv-linux` ctypes-shim environment the original plan needed (this
branch runs natively on Linux — no shim of any kind).

**Architecture:** reuse the vendored render engine's own filter-catalog
parser (`avg.media.vendor.avg.load_filter_catalog`) rather than writing a
second one; extend the existing `dynamic_options.txt` catalogue parser
(`avg.pipeline.prompts.CatalogOption`) to expose the palette payload it
already reads but discards; thread a `preset_payload` dict through the one
existing brand resolver (`avg.media.overlays.resolve_brand`) so a real
render and the settings-preview panel can never disagree again.

**Tech Stack:** Python 3, pytest, pydantic (`SettingSpec`) — no frontend
change (no new settings UI beyond existing `int`/`text` field renderers).

**Spec:** `docs/superpowers/specs/2026-09-02-slice-31-filters-brand-kit-design.md`

## Global Constraints

- No live API calls in tests — Director II's text-model call is mocked
  (`avg.pipeline.phases.director.generate_text`), same as every existing
  Director II test.
- `avg/media/vendor/` is never edited.
- `avg/media/` may not import `avg/pipeline/` — `avg.media.overlays`/
  `avg.media.preview`/`avg.media.environment` take plain values, never a
  settings dict or a catalogue lookup.
- Every reader of a channel setting goes through `ctx.channel_values`
  (already-resolved dict), never `ctx.channel.settings` directly —
  `tests/core/test_settings_schema.py::test_no_reader_bypasses_the_resolver`
  greps for this and fails the whole suite on a violation.
- Every `SettingSpec` requires `invalidates` (no default — omitting it
  fails at import).
- Verify with a real venv against this branch's own `pyproject.toml`:
  `python3 -m venv .venv && .venv/bin/pip install -e ".[dev,media]" &&
  .venv/bin/python -m pytest tests -q`. No shim setup of any kind is
  needed — confirmed by actually running the full suite this way (996
  passed, 1 skipped) before writing this port.

## Files touched

| File | Change |
|---|---|
| `avg/assets/Filters/Filters.metadata`, `film_grain.mov`, `light_leak.mov` | New — bundled verbatim from the legacy repo |
| `.gitattributes` | New (restored — see Task 1) |
| `pyproject.toml` | Package-data fix (Task 1B, Linux-only — see that task) |
| `avg/core/settings_schema.py` | +3 `SettingSpec`s |
| `avg/media/environment.py` | +`filters_dir()`/`ensure_filters_dir_env()` |
| `avg/pipeline/phases/director.py` | Filter catalog fetch, per-scene validation, anti-clutter cap, brand-preset lookup for `_draw_overlay_assets` |
| `avg/pipeline/prompts.py` | Filters section; `CatalogOption.payload`; `_BRAND_STYLE_DEFAULTS`; style-bible rewrite |
| `avg/media/overlays.py` | `resolve_brand`/`render_layers`/`write_layers` gain `preset_payload` |
| `avg/media/preview.py` | `_draw_to_png`/`overlay_png`/`takeover_png` gain `preset_payload` |
| `avg/api/routes_preview.py` | `OverlayPreview`/`TakeoverPreview` gain `brand_preset`; routes resolve the payload |
| `avg/media/engine.py` | `render()` calls `ensure_filters_dir_env()` before spawn |
| `tests/pipeline/test_prompts.py`, `tests/pipeline/test_phases_polish.py`, `tests/media/test_overlays.py`, `tests/media/test_environment.py`, `tests/media/test_preview.py`, `tests/api/test_routes_preview.py`, `tests/core/test_settings_schema.py` | New/extended tests per task |

Nine tasks, plus one Linux-only packaging task (1B, between 1 and 2 —
found while porting, not part of the original nine). 1-5 are the Filters
thread (assets → packaging → env → prompt → wiring → validation). 6-9 are
the Brand Kit thread (payload parsing → style bible → real render →
preview). 6-9 depend on nothing from 1-5; either thread can be built
first, but tasks within a thread are ordered.

---

## Task 1 — Bundle filter assets, settings, and binary protection

**Files:**
- Create: `avg/assets/Filters/Filters.metadata`, `avg/assets/Filters/film_grain.mov`, `avg/assets/Filters/light_leak.mov`
- Create: `.gitattributes`
- Modify: `avg/core/settings_schema.py:445` (after `overlay_highlight`)
- Test: `tests/core/test_settings_schema.py`

**Interfaces:**
- Produces: `SETTINGS` gains `filters_intensity` (`int`, default `50`),
  `filters_allow`/`filters_block` (`text`, default `""`), all
  `scope="channel"`, `invalidates="director2"`, `group="filters"` — Task 4
  reads all three from `ctx.channel_values`.

### Step 1.1 — Bundle the assets

Copy verbatim from the legacy reference repo (this sandbox already has it
cloned at `/home/claude/AI_Video_Gen`; on the real dev machine it is
`D:\AI_Video_Gen`, per `CLAUDE.md`):

```bash
mkdir -p avg/assets/Filters
cp /home/claude/AI_Video_Gen/Assets/Filters/Filters.metadata avg/assets/Filters/Filters.metadata
cp /home/claude/AI_Video_Gen/Assets/Filters/film_grain.mov avg/assets/Filters/film_grain.mov
cp /home/claude/AI_Video_Gen/Assets/Filters/light_leak.mov avg/assets/Filters/light_leak.mov
```

Verify the copy is byte-for-byte (must match exactly):

```bash
sha256sum avg/assets/Filters/film_grain.mov
# f8b304075f1478bb4504e9f28fa5ebb14f813a03bc275078796b0dc0707acbb
sha256sum avg/assets/Filters/light_leak.mov
# cb25d233bd41c3a7a94a471bed6d8eff87437a53f371865d4c1f20e315c9256
```

### Step 1.2 — Restore binary line-ending protection

`.gitattributes` was added in `ce593e5` ("Pin vendored media files against
line-ending rewriting") to stop `core.autocrlf` from silently corrupting
`avg/media/vendor/`'s stored blob while the working file looks untouched
(`CLAUDE.md`'s own "Rules that are not negotiable" section still describes
this file as in force). It was accidentally deleted by a later "Sync"
commit (`4cefaf5`) and the repo has shipped without it since — found while
bundling these new binaries, which need the identical protection. Restore
it, extended to cover the new asset path:

```bash
cat > .gitattributes << 'EOF'
# Vendored media layer: byte-for-byte copies of the old app's python/avg.py and
# overlay_design.py. Git's autocrlf silently rewrites line endings in the stored
# BLOB while leaving the working file untouched, so a corrupted copy passes an
# on-disk diff and only shows up in git hash-object. Never convert these.
avg/media/vendor/** -text

# Bundled filter overlay clips (ProRes 4444 .mov, alpha channel) - binary,
# never text-convert.
avg/assets/Filters/*.mov -text

# Everything else: normalise to LF in the repository.
* text=auto
EOF
```

This is a repo-hygiene fix riding along with Task 1, not a new task on its
own — restoring it after this task's `git add` would not protect the
`.mov` files added in the same commit.

### Step 1.3 — failing test for the new settings

Append to `tests/core/test_settings_schema.py`:

```python
def test_filters_settings_exist_with_expected_defaults() -> None:
    by_id = {s.id: s for s in SETTINGS}
    assert by_id["filters_intensity"].default == 50
    assert by_id["filters_intensity"].type == "int"
    assert by_id["filters_intensity"].group == "filters"
    assert by_id["filters_allow"].default == ""
    assert by_id["filters_block"].default == ""
    for setting_id in ("filters_intensity", "filters_allow", "filters_block"):
        assert by_id[setting_id].scope == "channel"
        assert by_id[setting_id].invalidates == "director2"
```

Run: `.venv/bin/python -m pytest tests/core/test_settings_schema.py -q`
Expected: FAIL — `KeyError` on `by_id["filters_intensity"]` (setting
doesn't exist yet).

### Step 1.4 — add the settings

In `avg/core/settings_schema.py`, replace:

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
    # --- voice provider and options ---
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
    # --- filter overlays (Director II's optional per-scene clips) ---
    SettingSpec(
        id="filters_intensity",
        group="filters",
        label="Filter intensity",
        help=(
            "How freely Director II reaches for a filter overlay clip on a scene "
            "(0 = off entirely, 50 = occasionally, 100 = freely)."
        ),
        type="int",
        default=50,
        scope="channel",
        invalidates="director2",
    ),
    SettingSpec(
        id="filters_allow",
        group="filters",
        label="Allowed filters",
        help=(
            "Comma-separated filter names Director II may choose from. Empty allows "
            "every cataloged filter."
        ),
        type="text",
        default="",
        scope="channel",
        invalidates="director2",
    ),
    SettingSpec(
        id="filters_block",
        group="filters",
        label="Blocked filters",
        help="Comma-separated filter names Director II may never choose.",
        type="text",
        default="",
        scope="channel",
        invalidates="director2",
    ),
    # --- voice provider and options ---
```

### Step 1.5 — run to verify pass

Run: `.venv/bin/python -m pytest tests/core/test_settings_schema.py -q`
Expected: PASS. `test_result_covers_every_channel_scope_id_and_nothing_else`
and `test_no_reader_bypasses_the_resolver` in the same file must also still
pass unmodified (the new settings are schema-only at this point, read by
nothing yet).

### Step 1.6 — commit

```bash
git add avg/assets/Filters/Filters.metadata avg/assets/Filters/film_grain.mov \
  avg/assets/Filters/light_leak.mov .gitattributes avg/core/settings_schema.py \
  tests/core/test_settings_schema.py
git commit -m "feat: bundle filter overlay assets + filters_* settings

Copies film_grain.mov/light_leak.mov/Filters.metadata verbatim from the
legacy repo. Also restores .gitattributes (deleted by a stray 'Sync'
commit, 4cefaf5) - found while adding these binaries, which need the
same -text protection avg/media/vendor/ already relies on."
```

---

## Task 1B — Fix packaging: `avg/assets/` doesn't reach a real install

**Why this is here, in this plan, on this branch only:** found while
porting Task 1 to Linux — this branch's Flatpak manifest runs a genuine
`pip install .` (`docs/linux-port/WINDOWS_VS_LINUX.md`'s "Packaging
shape"), unlike the Windows build, which never goes through a wheel build
at all (dev runs via `PYTHONPATH`, release packages via PyInstaller,
neither cares about setuptools package-data). Reproduced directly: a real
`pip install .` into a throwaway venv does not copy `avg/assets/` at
all — not just the new filter clips Task 1 adds, the *already-existing*
`dynamic_options.txt`/`personality.txt` too. `avg/assets/` has no
`__init__.py` (it isn't a Python sub-package), so
`[tool.setuptools.packages.find]`'s `include = ["avg*"]` never discovers
it as something to carry data for. This has nothing to do with filters
specifically and was already broken before this plan — every caller of
`avg.pipeline.personality.assets_dir()` degrades to an empty catalogue in
a real packaged build today, silently (`_load_bundled_catalog`'s own
tolerant `OSError` handling swallows the missing directory). This task
fixes the general gap, not a filters-specific carve-out — Task 1's own
new assets are simply the reason it was found now.

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `avg/assets/**` (including everything Task 1 places under
  `avg/assets/Filters/`) present in `site-packages/avg/assets/` after
  `pip install .` — not just `pip install -e .` (an editable install
  symlinks back to the source tree and would never have exposed this
  gap at all).

### Step 1B.1 — reproduce, then verify the fix, with a real install

This task is a packaging config change, not application code — a pytest
run can't observe it (the dev/editable environment every other task
tests against never has this problem). Verify with an actual install
into a scratch prefix, before and after:

```bash
# Before (reproduces the bug - run this on main, prior to this task):
python3 -m venv /tmp/avg_pkg_check
/tmp/avg_pkg_check/bin/pip install --quiet .
find /tmp/avg_pkg_check/lib/python3.*/site-packages/avg/assets -type f 2>&1
# Expect: "No such file or directory" - avg/assets isn't there at all.
rm -rf /tmp/avg_pkg_check
```

### Step 1B.2 — implement

In `pyproject.toml`, replace:

```toml
[tool.setuptools.packages.find]
include = ["avg*"]
```

with:

```toml
[tool.setuptools.packages.find]
include = ["avg*"]

[tool.setuptools.package-data]
# avg/assets/ (dynamic_options.txt, personality.txt, and Filters/ once
# Task 1 lands) has no __init__.py - it isn't a discoverable Python
# sub-package, so packages.find above never sees it and never carries
# its data along. package-data, keyed to the PARENT package `avg`, is
# the standard way to include non-Python files that live inside a
# package directory without being a package themselves.
avg = ["assets/*", "assets/Filters/*"]
```

### Step 1B.3 — run to verify pass

Rebuild and check the same scratch install again:

```bash
python3 -m venv /tmp/avg_pkg_check
/tmp/avg_pkg_check/bin/pip install --quiet .
find /tmp/avg_pkg_check/lib/python3.*/site-packages/avg/assets -type f
# Expect: dynamic_options.txt, personality.txt, and (once Task 1 has
# also landed) Filters/Filters.metadata, Filters/film_grain.mov,
# Filters/light_leak.mov.
rm -rf /tmp/avg_pkg_check
```

Then confirm the normal dev/editable workflow (every other task's own
test runs) is completely unaffected:

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS at the documented baseline — `package-data` only affects
what a *built* install carries; `pip install -e .` and this repo's own
test collection never depended on it.

### Step 1B.4 — commit

```bash
git add pyproject.toml
git commit -m "fix: avg/assets/ doesn't reach a real pip install

avg/assets/ has no __init__.py, so packages.find's 'avg*' discovery
never sees it as something to carry data for - confirmed with a real
(non-editable) install into a scratch venv, both before and after this
fix. Pre-existing gap, unrelated to filters specifically: every caller
of avg.pipeline.personality.assets_dir() (brand presets, video tones,
personality presets) has been silently degrading to an empty catalogue
in any real Flatpak build made from this branch. Found while porting
Task 1's new binary assets - they would have shipped nothing without
this fix, same as the assets already there today."
```

---

## Task 2 — `AVG_FILTERS_DIR` resolution

**Files:**
- Modify: `avg/media/environment.py`
- Test: `tests/media/test_environment.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `filters_dir() -> Path`, `ensure_filters_dir_env() -> None`.
  Task 4 calls `ensure_filters_dir_env()` before reading the filter
  catalogue; Task 1's later render (out of this plan's scope, already
  live in `avg.media.vendor.avg`) reads `AVG_FILTERS_DIR` via
  `_filters_root()`.

### Step 2.1 — failing test

Append to `tests/media/test_environment.py`:

```python
def test_filters_dir_dev_checkout_points_at_bundled_copy():
    from avg.media import environment

    d = environment.filters_dir()
    assert (d / "Filters.metadata").is_file()
    assert (d / "film_grain.mov").is_file()


def test_ensure_filters_dir_env_sets_the_variable(monkeypatch):
    from avg.media import environment

    monkeypatch.delenv("AVG_FILTERS_DIR", raising=False)
    environment.ensure_filters_dir_env()
    assert os.environ["AVG_FILTERS_DIR"] == str(environment.filters_dir())


def test_ensure_filters_dir_env_never_clobbers_an_explicit_value(monkeypatch):
    from avg.media import environment

    monkeypatch.setenv("AVG_FILTERS_DIR", "/some/test/override")
    environment.ensure_filters_dir_env()
    assert os.environ["AVG_FILTERS_DIR"] == "/some/test/override"
```

Check the top of `tests/media/test_environment.py` already imports `os` -
if not, add `import os` to its import block.

Run: `.venv/bin/python -m pytest tests/media/test_environment.py -q`
Expected: FAIL — `AttributeError: module 'avg.media.environment' has no
attribute 'filters_dir'`.

### Step 2.2 — implement

In `avg/media/environment.py`, add after `prepend_to_path()` (after the
`os.environ["IMAGEIO_FFMPEG_EXE"] = str(d / _FFMPEG_EXE_NAME)` line, before
the `# ── Console windows ──` section header):

```python
# ── Filter overlay clips ────────────────────────────────────
#
# `avg.media.vendor.avg._filters_root()` resolves the bundled filter clips
# from `os.environ["AVG_FILTERS_DIR"]`, falling back to a path relative to
# the vendor file itself that assumed the legacy repo's flat layout
# (`Assets/Filters` two directories up from `python/avg.py`) - wrong here,
# and vendor/ is never edited to fix it. This section is the one place
# that resolves the real bundled location (`avg/assets/Filters`, matching
# `avg.pipeline.personality.assets_dir()`'s convention) and sets the env
# var so the vendored resolver finds it without ever needing to change.
#
# Two callers, both idempotent (`os.environ.setdefault`) because either
# can run first depending on how a render was reached:
#   - `avg.pipeline.phases.director.Director2Phase.run` needs the catalogue
#     BEFORE Director II's own prompt is built (same process, no child
#     spawned yet).
#   - `avg.media.engine.MediaEngine.render` needs it before the render
#     child spawns, in case a render is ever triggered without Director II
#     having run first in the same process (e.g. `tools/_dev_serve.py`
#     against an already-planned project).


@lru_cache(maxsize=1)
def filters_dir() -> Path:
    """Directory holding the bundled filter overlay clips
    (`avg/assets/Filters`), in both a dev checkout and a frozen build.

    Independent duplicate of `avg.pipeline.personality.assets_dir()`'s
    exact technique - `avg/media/` may not import `avg/pipeline/`, the
    same reason `bundled_ffmpeg_dir()` above duplicates rather than shares
    its own frozen/dev resolution. The `sys.frozen` branch is inert on
    this branch specifically (never frozen - no PyInstaller here, see
    `docs/linux-port/WINDOWS_VS_LINUX.md`'s "Packaging shape") and always
    falls through to the dev-checkout return below; kept rather than
    removed, matching `bundled_ffmpeg_dir()`'s own precedent immediately
    above of leaving the same dead-but-harmless branch in place rather
    than forking the function's shape between platforms.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / "avg" / "assets" / "Filters"
            if candidate.is_dir():
                return candidate
    return Path(__file__).resolve().parent.parent / "assets" / "Filters"


def ensure_filters_dir_env() -> None:
    """Set `AVG_FILTERS_DIR` once, idempotently, so `avg.media.vendor.avg`'s
    `_filters_root()` finds the bundled clips regardless of which entry
    point reaches it first. Safe to call more than once and from more than
    one place - see the module note above."""
    os.environ.setdefault("AVG_FILTERS_DIR", str(filters_dir()))
```

### Step 2.3 — run to verify pass

Run: `.venv/bin/python -m pytest tests/media/test_environment.py -q`
Expected: PASS.

### Step 2.4 — commit

```bash
git add avg/media/environment.py tests/media/test_environment.py
git commit -m "feat: AVG_FILTERS_DIR resolution in avg.media.environment

filters_dir() mirrors assets_dir()'s frozen/dev resolution (media layer
may not import pipeline, so this is a second, independent copy of the
same technique bundled_ffmpeg_dir() already uses for ffmpeg).
ensure_filters_dir_env() sets it once, idempotently."
```

---

## Task 3 — `build_director2_prompt` filters section

**Files:**
- Modify: `avg/pipeline/prompts.py`
- Test: `tests/pipeline/test_prompts.py`

**Interfaces:**
- Consumes: nothing new (pure string builder, takes a plain
  `dict[str, str]` catalogue and an `int` intensity - no I/O).
- Produces: `build_director2_prompt(..., filter_catalog: dict[str, str]
  | None = None, filters_intensity: int = 50)`. Task 4 is the only caller
  that passes a real, non-empty `filter_catalog`.

### Step 3.1 — failing tests

Append to `tests/pipeline/test_prompts.py`:

```python
def _inputs(**overrides) -> CreativeInputs:
    defaults = dict(topic="The Antikythera Mechanism")
    defaults.update(overrides)
    return CreativeInputs(**defaults)


def test_director2_prompt_omits_filters_section_when_catalog_is_empty():
    from avg.pipeline.prompts import build_director2_prompt

    prompt = build_director2_prompt(_inputs(), ["Scene one."], filter_catalog={})
    assert "AVAILABLE FILTERS" not in prompt


def test_director2_prompt_omits_filters_section_when_catalog_is_none():
    from avg.pipeline.prompts import build_director2_prompt

    prompt = build_director2_prompt(_inputs(), ["Scene one."])
    assert "AVAILABLE FILTERS" not in prompt


def test_director2_prompt_lists_every_cataloged_filter():
    from avg.pipeline.prompts import build_director2_prompt

    catalog = {
        "film_grain": "Old-film grain with subtle flicker.",
        "light_leak": "Warm light bloom drifting across frame.",
    }
    prompt = build_director2_prompt(_inputs(), ["Scene one."], filter_catalog=catalog)
    assert "AVAILABLE FILTERS" in prompt
    assert "film_grain: Old-film grain with subtle flicker." in prompt
    assert "light_leak: Warm light bloom drifting across frame." in prompt
    assert '"filter"' in prompt  # schema line mentions the optional field


@pytest.mark.parametrize(
    "intensity,expected_word",
    [(90, "freely"), (50, "occasionally"), (10, "sparingly")],
)
def test_director2_prompt_filter_frequency_scales_with_intensity(intensity, expected_word):
    from avg.pipeline.prompts import build_director2_prompt

    prompt = build_director2_prompt(
        _inputs(), ["Scene one."], filter_catalog={"film_grain": "desc"}, filters_intensity=intensity
    )
    assert expected_word in prompt
```

Run: `.venv/bin/python -m pytest tests/pipeline/test_prompts.py -q`
Expected: FAIL — `build_director2_prompt() got an unexpected keyword
argument 'filter_catalog'`.

### Step 3.2 — implement

In `avg/pipeline/prompts.py`, add a helper right before
`build_director2_prompt` (after `build_director_style_bible`'s closing
`{style_guide}...`-style line — i.e. immediately above the
`def build_director2_prompt(` line):

```python
def _filters_section(filter_catalog: dict[str, str], intensity: int) -> str:
    """The optional "AVAILABLE FILTERS" block `build_director2_prompt`
    appends - empty (never rendered) when there is nothing to offer, so a
    channel with `filters_intensity == 0` or an empty/fully-blocked
    catalogue gets a byte-identical prompt to before this feature existed.
    Frequency wording mirrors the three-tier phrasing `Get-AiDirectionPlan`
    used for the same `Config.edit.filters.intensity` value."""
    if not filter_catalog:
        return ""
    if intensity >= 75:
        frequency = "freely — use one on any scene that would benefit"
    elif intensity >= 40:
        frequency = "occasionally — only on scenes where it clearly earns its place"
    else:
        frequency = "sparingly — only on the one or two scenes that most need it"
    lines = "\n".join(f"- {name}: {desc}" for name, desc in filter_catalog.items())
    return f"""

AVAILABLE FILTERS (optional overlay clips baked over the finished scene — use them {frequency}):
{lines}
For a scene that should carry one, add "filter": "<name>" (the exact catalog name, lowercase). Most scenes should have none."""
```

Then replace `build_director2_prompt`'s signature and body:

```python
def build_director2_prompt(
    inputs: CreativeInputs,
    scene_texts: list[str],
    *,
    personality: dict[str, PersonalityPreset] | None = None,
    emotional_arc: str = "",
) -> str:
```

with:

```python
def build_director2_prompt(
    inputs: CreativeInputs,
    scene_texts: list[str],
    *,
    personality: dict[str, PersonalityPreset] | None = None,
    emotional_arc: str = "",
    filter_catalog: dict[str, str] | None = None,
    filters_intensity: int = 50,
) -> str:
```

Update the docstring (replace the `"no external filters catalogue"` clause
— it is no longer true):

```python
    """Phase 5 (Director II)'s motion/grade prompt: opens with
    `build_director_style_bible`'s style-bible fragment, then asks for a
    per-scene camera move/transition and one overall colour grade for the
    whole video, and - when `filter_catalog` is non-empty - an optional
    per-scene filter overlay clip. Ported from the corresponding section of
    `Get-AiDirectionPlan` (`D:\\AI_Video_Gen\\modules\\ai.ps1`).
```

(keep the rest of the docstring's "closed vocabulary" paragraph unchanged).

Finally, wire the section into the returned prompt. Replace:

```python
Also choose ONE overall grade (colour look) for the whole video that fits the style bible:
- grade.look: none | warm | cool | cinematic | noir | vibrant | soft
The SAME grade applies to every scene (set it once at top level).

Return ONLY a JSON object, no preamble, no markdown fences. Schema:
{{"version":1,"grade":{{"look":"..."}},
 "scenes":[{{"scene":<int>,"motion":{{"type":"...","intensity":0.5,"ease":"ease_out_cubic"}},"transition_in":"..."}}]}}
Every scene number above MUST appear exactly once."""
```

with:

```python
Also choose ONE overall grade (colour look) for the whole video that fits the style bible:
- grade.look: none | warm | cool | cinematic | noir | vibrant | soft
The SAME grade applies to every scene (set it once at top level).
{filters_section}

Return ONLY a JSON object, no preamble, no markdown fences. Schema:
{{"version":1,"grade":{{"look":"..."}},
 "scenes":[{{"scene":<int>,"motion":{{"type":"...","intensity":0.5,"ease":"ease_out_cubic"}}{scene_filter_field},"transition_in":"..."}}]}}
Every scene number above MUST appear exactly once."""
```

And immediately before the `return f"""..."""` in the same function, add:

```python
    filters_section = _filters_section(filter_catalog or {}, filters_intensity)
    scene_filter_field = ',"filter":"..."' if filters_section else ""
```

(so both new placeholders are defined before the f-string uses them).

### Step 3.3 — run to verify pass

Run: `.venv/bin/python -m pytest tests/pipeline/test_prompts.py -q`
Expected: PASS. Also re-run the whole file to confirm no existing
`build_director2_prompt`/`build_director_style_bible` test broke (none
pass `filter_catalog`, so every existing call still gets the byte-identical
no-section prompt).

### Step 3.4 — commit

```bash
git add avg/pipeline/prompts.py tests/pipeline/test_prompts.py
git commit -m "feat: build_director2_prompt gains an optional filters section

Section is entirely absent when filter_catalog is empty/None, so every
existing caller (and any channel with filters_intensity=0) gets a
byte-identical prompt to before this feature existed."
```

---

## Task 4 — Director II reads the catalogue and calls the new prompt

**Files:**
- Modify: `avg/pipeline/phases/director.py`
- Test: `tests/pipeline/test_phases_polish.py`

**Interfaces:**
- Consumes: `avg.media.environment.ensure_filters_dir_env` (Task 2),
  `avg.media.vendor.avg.load_filter_catalog` (already exists, unmodified),
  `build_director2_prompt(..., filter_catalog=..., filters_intensity=...)`
  (Task 3).
- Produces: `_filter_catalog() -> dict[str, str]`,
  `_eligible_filters(catalog, allow, block) -> dict[str, str]` — Task 5
  needs the eligible catalogue's key set to validate the model's per-scene
  choice against.

### Step 4.1 — failing test

Append to `tests/pipeline/test_phases_polish.py`, in the Director2Phase
section:

```python
def test_director2_prompt_receives_the_eligible_filter_catalog(tmp_path, monkeypatch):
    """filters_allow/filters_block narrow what reaches the prompt; a
    blocked name never appears even if cataloged."""
    from avg.pipeline.phases import director as director_module

    monkeypatch.setattr(
        director_module,
        "load_filter_catalog",
        lambda force=True: {
            "film_grain": {"name": "film_grain", "desc": "Old-film grain.", "vf_file": "film_grain.mov"},
            "light_leak": {"name": "light_leak", "desc": "Warm light bloom.", "vf_file": "light_leak.mov"},
        },
    )
    monkeypatch.setattr(director_module, "ensure_filters_dir_env", lambda: None)

    reply = json.dumps({"grade": {"look": "none"}, "scenes": [{"scene": 1, "motion": {}, "transition_in": "cut"}]})
    fake = _QueueGenerator([reply])
    monkeypatch.setattr(director_module, "generate_text", fake)

    project = _project(tmp_path)
    channel = _channel({"filters_block": "light_leak"})
    ctx, _ = _ctx(project, channel=channel)
    ctx.channel_values = resolve_channel_settings(channel.settings, {})
    _write_scenes(ctx, ["Scene one text."])

    director_module.Director2Phase().run(ctx)

    prompt = fake.calls[0][1]
    assert "film_grain" in prompt
    assert "light_leak" not in prompt


def test_director2_prompt_has_no_filters_section_when_intensity_is_zero(tmp_path, monkeypatch):
    from avg.pipeline.phases import director as director_module

    monkeypatch.setattr(
        director_module,
        "load_filter_catalog",
        lambda force=True: {"film_grain": {"name": "film_grain", "desc": "Old-film grain.", "vf_file": "x.mov"}},
    )
    monkeypatch.setattr(director_module, "ensure_filters_dir_env", lambda: None)

    reply = json.dumps({"grade": {"look": "none"}, "scenes": [{"scene": 1, "motion": {}, "transition_in": "cut"}]})
    fake = _QueueGenerator([reply])
    monkeypatch.setattr(director_module, "generate_text", fake)

    project = _project(tmp_path)
    channel = _channel({"filters_intensity": 0})
    ctx, _ = _ctx(project, channel=channel)
    ctx.channel_values = resolve_channel_settings(channel.settings, {})
    _write_scenes(ctx, ["Scene one text."])

    director_module.Director2Phase().run(ctx)

    assert "AVAILABLE FILTERS" not in fake.calls[0][1]
```

Run: `.venv/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k filter`
Expected: FAIL — `AttributeError: <module 'avg.pipeline.phases.director'>
does not have the attribute 'load_filter_catalog'` (nothing imports it
into the module's namespace yet, so `monkeypatch.setattr` on the module
object has nothing to patch).

### Step 4.2 — implement

In `avg/pipeline/phases/director.py`, extend the import block. Replace:

```python
from avg.pipeline.prompts import build_director1_prompt, build_director2_prompt
from avg.providers.text.router import generate as generate_text
```

with:

```python
from avg.media.environment import ensure_filters_dir_env
from avg.media.vendor.avg import load_filter_catalog
from avg.pipeline.prompts import build_director1_prompt, build_director2_prompt
from avg.providers.text.router import generate as generate_text
```

Add two module-level helpers in the "── Director II ──" section, directly
after the `_ACCENT = "accent1"` line:

```python
def _filter_catalog() -> dict[str, str]:
    """`{name: description}` for every cataloged filter clip, or `{}` if
    none exist yet (empty catalogue is a clean no-op - see
    `avg.pipeline.prompts._filters_section`).

    `force=True` on every call: `load_filter_catalog`'s module-level cache
    lives in the protected vendor file and would otherwise return a stale
    catalogue read under a different `AVG_FILTERS_DIR` (a real risk across
    tests, which routinely monkeypatch the env var to a temp catalogue).
    """
    ensure_filters_dir_env()
    return {entry["name"]: entry["desc"] for entry in load_filter_catalog(force=True).values()}


def _eligible_filters(catalog: dict[str, str], allow: str, block: str) -> dict[str, str]:
    """`catalog` narrowed by the channel's `filters_allow`/`filters_block`
    CSV settings (case-insensitive; blank allow means "everything not
    blocked")."""
    allow_set = {n.strip().lower() for n in allow.split(",") if n.strip()}
    block_set = {n.strip().lower() for n in block.split(",") if n.strip()}
    eligible = {name: desc for name, desc in catalog.items() if name.lower() not in block_set}
    if allow_set:
        eligible = {name: desc for name, desc in eligible.items() if name.lower() in allow_set}
    return eligible
```

Then update `Director2Phase.run` to build the eligible catalogue and pass
it into the prompt. Replace:

```python
        inputs = creative_inputs(ctx)

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning motion and grade..."))
        prompt = build_director2_prompt(inputs, scene_texts)
        result = generate_text(prompt, "director2", config=ctx.router)
```

with:

```python
        inputs = creative_inputs(ctx)
        settings = ctx.channel_values

        filters_intensity = _coerce_int(settings.get("filters_intensity"))
        if filters_intensity is None:
            filters_intensity = 50
        filters_intensity = max(0, min(100, filters_intensity))
        eligible_filters = _eligible_filters(
            _filter_catalog(),
            str(settings.get("filters_allow") or ""),
            str(settings.get("filters_block") or ""),
        )

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning motion and grade..."))
        prompt = build_director2_prompt(
            inputs, scene_texts, filter_catalog=eligible_filters, filters_intensity=filters_intensity
        )
        result = generate_text(prompt, "director2", config=ctx.router)
```

### Step 4.3 — run to verify pass

Run: `.venv/bin/python -m pytest tests/pipeline/test_phases_polish.py -q`
Expected: PASS, all Director2Phase tests including the two new ones and
every pre-existing one (none of them set `filters_*` settings, so
`_filter_catalog()` reads the REAL bundled `avg/assets/Filters` — Task 1's
two real entries — and gets included by default at intensity 50; none of
those tests assert on prompt *content* beyond what they already check, so
this is safe, but confirm by running the full file, not just `-k filter`).

### Step 4.4 — commit

```bash
git add avg/pipeline/phases/director.py tests/pipeline/test_phases_polish.py
git commit -m "feat: Director II reads the filter catalog and settings

_filter_catalog() always forces a fresh read (the vendored cache would
otherwise go stale across a changed AVG_FILTERS_DIR). _eligible_filters
applies filters_allow/filters_block before the catalog ever reaches the
prompt."
```

---

## Task 5 — Per-scene filter validation and the anti-clutter cap

**Files:**
- Modify: `avg/pipeline/phases/director.py`
- Test: `tests/pipeline/test_phases_polish.py`

**Interfaces:**
- Consumes: `_eligible_filters`'s key set (Task 4).
- Produces: `direction_plan.json`'s scenes gain an optional `"filter"` key.

### Step 5.1 — failing tests

Append to `tests/pipeline/test_phases_polish.py`:

```python
def test_direction_plan_carries_a_valid_per_scene_filter(tmp_path, monkeypatch):
    from avg.pipeline.phases import director as director_module

    monkeypatch.setattr(
        director_module,
        "load_filter_catalog",
        lambda force=True: {"film_grain": {"name": "film_grain", "desc": "d", "vf_file": "x.mov"}},
    )
    monkeypatch.setattr(director_module, "ensure_filters_dir_env", lambda: None)
    reply = json.dumps({
        "grade": {"look": "none"},
        "scenes": [{"scene": 1, "motion": {}, "transition_in": "cut", "filter": "film_grain"}],
    })
    monkeypatch.setattr(director_module, "generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one."])
    director_module.Director2Phase().run(ctx)

    plan = read_json(render_dir(ctx) / "direction_plan.json")
    assert plan["scenes"][0]["filter"] == "film_grain"


def test_direction_plan_drops_a_filter_name_the_catalog_never_offered(tmp_path, monkeypatch):
    """A model naming a filter it wasn't given (hallucinated, or offered
    under an old catalog before a rename) must never reach the render -
    the vendored compositor would just skip an unrecognised name, but a
    stale one is still worth degrading here rather than passing through."""
    from avg.pipeline.phases import director as director_module

    monkeypatch.setattr(director_module, "load_filter_catalog", lambda force=True: {})
    monkeypatch.setattr(director_module, "ensure_filters_dir_env", lambda: None)
    reply = json.dumps({
        "grade": {"look": "none"},
        "scenes": [{"scene": 1, "motion": {}, "transition_in": "cut", "filter": "made_up_name"}],
    })
    monkeypatch.setattr(director_module, "generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one."])
    director_module.Director2Phase().run(ctx)

    plan = read_json(render_dir(ctx) / "direction_plan.json")
    assert "filter" not in plan["scenes"][0]


def test_direction_plan_caps_filter_use_to_at_most_a_quarter_of_scenes(tmp_path, monkeypatch):
    """8 scenes, every one naming a valid filter - the 25% cap must keep
    only 2 (floor(8 * 0.25))."""
    from avg.pipeline.phases import director as director_module

    monkeypatch.setattr(
        director_module,
        "load_filter_catalog",
        lambda force=True: {"film_grain": {"name": "film_grain", "desc": "d", "vf_file": "x.mov"}},
    )
    monkeypatch.setattr(director_module, "ensure_filters_dir_env", lambda: None)
    reply = json.dumps({
        "grade": {"look": "none"},
        "scenes": [
            {"scene": i + 1, "motion": {}, "transition_in": "cut", "filter": "film_grain"} for i in range(8)
        ],
    })
    monkeypatch.setattr(director_module, "generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, [f"Scene {i}." for i in range(8)])
    director_module.Director2Phase().run(ctx)

    plan = read_json(render_dir(ctx) / "direction_plan.json")
    filtered = [s for s in plan["scenes"] if "filter" in s]
    assert len(filtered) == 2


def test_direction_plan_caps_a_single_filter_name_to_two_scenes(tmp_path, monkeypatch):
    """6 scenes (well under the 25% scene cap at this count would allow 1 -
    use a catalog with two names and a generous intensity note so the
    per-NAME cap, not the per-scene cap, is what's under test): every scene
    requests the SAME filter name - at most 2 may keep it even though the
    scene-fraction cap alone would allow more names to spread across."""
    from avg.pipeline.phases import director as director_module

    monkeypatch.setattr(
        director_module,
        "load_filter_catalog",
        lambda force=True: {"film_grain": {"name": "film_grain", "desc": "d", "vf_file": "x.mov"}},
    )
    monkeypatch.setattr(director_module, "ensure_filters_dir_env", lambda: None)
    # 12 scenes: the 25% cap allows 3, so this proves the per-name cap (2)
    # is the one that actually bites here, not the scene-fraction cap.
    reply = json.dumps({
        "grade": {"look": "none"},
        "scenes": [
            {"scene": i + 1, "motion": {}, "transition_in": "cut", "filter": "film_grain"} for i in range(12)
        ],
    })
    monkeypatch.setattr(director_module, "generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, [f"Scene {i}." for i in range(12)])
    director_module.Director2Phase().run(ctx)

    plan = read_json(render_dir(ctx) / "direction_plan.json")
    filtered = [s for s in plan["scenes"] if "filter" in s]
    assert len(filtered) == 2
```

Run: `.venv/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k "filter or direction_plan_caps or direction_plan_drops or direction_plan_carries"`
Expected: FAIL — `plan["scenes"][0]["filter"]` raises `KeyError` (the field
doesn't exist yet at all).

### Step 5.2 — implement

In `avg/pipeline/phases/director.py`, add the cap constants next to the
existing card-frequency constant. Replace:

```python
# Roughly one Focused card per 3 scenes (legacy `Export-CardPlan`'s own
# frequency budget) so cards punctuate a video rather than saturating it.
_CARD_SCENE_DIVISOR = 3
```

with:

```python
# Roughly one Focused card per 3 scenes (legacy `Export-CardPlan`'s own
# frequency budget) so cards punctuate a video rather than saturating it.
_CARD_SCENE_DIVISOR = 3

# Same anti-clutter budget `Get-AiDirectionPlan` applied to filter overlays:
# at most a quarter of the video's scenes may carry one, and no single
# filter name may cover more than two scenes, so a model that likes one
# filter can't saturate the whole video with it.
_FILTER_SCENE_FRACTION = 0.25
_FILTER_MAX_PER_NAME = 2
```

Update `_normalize_direction_entry`'s signature and body. Replace:

```python
def _normalize_direction_entry(entry: object, grade: dict, valid_scenes: set[int]) -> dict | None:
    """One `scenes[]` entry from the model, sanitized against the plan's
    closed vocabularies, or `None` if it names no scene that actually
    exists in the finished script. `grade` is shared by every scene (the
    plan sets it once at top level - see module docstring)."""
    if not isinstance(entry, dict):
        return None
    scene_num = _coerce_int(entry.get("scene"))
    if scene_num is None or scene_num not in valid_scenes:
        return None

    motion_in = entry.get("motion") if isinstance(entry.get("motion"), dict) else {}
    mtype = str(motion_in.get("type") or _DEFAULT_MOTION).strip().lower()
    if mtype not in _VALID_MOTION:
        mtype = _DEFAULT_MOTION
    intensity = _coerce_float(motion_in.get("intensity"), 0.5)
    intensity = max(0.0, min(1.0, intensity))
    ease = str(motion_in.get("ease") or _DEFAULT_EASE).strip().lower()
    if ease not in _VALID_EASE:
        ease = _DEFAULT_EASE

    transition_in = str(entry.get("transition_in") or _DEFAULT_TRANSITION).strip().lower()
    if transition_in not in _VALID_TRANSITIONS:
        transition_in = _DEFAULT_TRANSITION

    return {
        "scene": scene_num,
        "motion": {"type": mtype, "intensity": intensity, "ease": ease},
        "transition_in": transition_in,
        "grade": grade,
    }
```

with:

```python
def _normalize_direction_entry(
    entry: object, grade: dict, valid_scenes: set[int], valid_filters: frozenset[str] = frozenset()
) -> dict | None:
    """One `scenes[]` entry from the model, sanitized against the plan's
    closed vocabularies, or `None` if it names no scene that actually
    exists in the finished script. `grade` is shared by every scene (the
    plan sets it once at top level - see module docstring).

    `filter` is CATALOGUE data, not a closed vocabulary like motion/ease/
    transition/grade - `valid_filters` is the eligible catalogue's own key
    set (`avg.pipeline.phases.director._eligible_filters`), not a hardcoded
    constant. An empty `valid_filters` (feature off, or nothing eligible)
    means the key is never added regardless of what the model said - the
    anti-clutter cap in `Director2Phase.run` still runs afterward and may
    drop it further."""
    if not isinstance(entry, dict):
        return None
    scene_num = _coerce_int(entry.get("scene"))
    if scene_num is None or scene_num not in valid_scenes:
        return None

    motion_in = entry.get("motion") if isinstance(entry.get("motion"), dict) else {}
    mtype = str(motion_in.get("type") or _DEFAULT_MOTION).strip().lower()
    if mtype not in _VALID_MOTION:
        mtype = _DEFAULT_MOTION
    intensity = _coerce_float(motion_in.get("intensity"), 0.5)
    intensity = max(0.0, min(1.0, intensity))
    ease = str(motion_in.get("ease") or _DEFAULT_EASE).strip().lower()
    if ease not in _VALID_EASE:
        ease = _DEFAULT_EASE

    transition_in = str(entry.get("transition_in") or _DEFAULT_TRANSITION).strip().lower()
    if transition_in not in _VALID_TRANSITIONS:
        transition_in = _DEFAULT_TRANSITION

    result = {
        "scene": scene_num,
        "motion": {"type": mtype, "intensity": intensity, "ease": ease},
        "transition_in": transition_in,
        "grade": grade,
    }
    if valid_filters:
        filt = str(entry.get("filter") or "").strip().lower()
        if filt and filt in valid_filters:
            result["filter"] = filt
    return result
```

Add the cap function directly after `_normalize_direction_entry`:

```python
def _cap_filter_use(scenes_out: list[dict], total_scenes: int) -> None:
    """Enforce the anti-clutter budget in place, walking scenes in order:
    at most `_FILTER_SCENE_FRACTION` of all scenes keep a filter, and at
    most `_FILTER_MAX_PER_NAME` scenes keep any one filter name. Excess
    entries lose the key entirely (not set to `None`) - matches every
    other optional per-scene field in this codebase, which is absent, not
    null, when there is nothing to say."""
    max_scenes = int(total_scenes * _FILTER_SCENE_FRACTION)
    per_name: dict[str, int] = {}
    used = 0
    for scene in scenes_out:
        name = scene.get("filter")
        if not name:
            continue
        if used >= max_scenes or per_name.get(name, 0) >= _FILTER_MAX_PER_NAME:
            del scene["filter"]
            continue
        per_name[name] = per_name.get(name, 0) + 1
        used += 1
```

Finally, thread `valid_filters` through `Director2Phase.run` and call the
cap. Replace:

```python
        scenes_out = [
            normalized
            for entry in (parsed.get("scenes") or [])
            if (normalized := _normalize_direction_entry(entry, grade, valid_scenes)) is not None
        ]
        if not scenes_out:
```

with:

```python
        valid_filters = frozenset(eligible_filters)
        scenes_out = [
            normalized
            for entry in (parsed.get("scenes") or [])
            if (normalized := _normalize_direction_entry(entry, grade, valid_scenes, valid_filters)) is not None
        ]
        if scenes_out:
            _cap_filter_use(scenes_out, len(scene_texts))
        if not scenes_out:
```

### Step 5.3 — run to verify pass

Run: `.venv/bin/python -m pytest tests/pipeline/test_phases_polish.py -q`
Expected: PASS, including every pre-existing `Director2Phase`/
`_normalize_direction_entry` test (all call it with the old 3-positional-
argument shape or rely on `Director2Phase.run`'s own call, which now
always passes a 4th `valid_filters` argument — the parameter has a
default, so any direct 3-arg test call is unaffected).

### Step 5.4 — commit

```bash
git add avg/pipeline/phases/director.py tests/pipeline/test_phases_polish.py
git commit -m "feat: validate per-scene filter choice + anti-clutter cap

Same budget as legacy Get-AiDirectionPlan: <=25% of scenes, <=2 per
filter name, walked in scene order. A filter name the eligible catalog
never offered is dropped, not passed through."
```

---

## Task 6 — `CatalogOption.payload` parsing

**Files:**
- Modify: `avg/pipeline/prompts.py`
- Test: `tests/pipeline/test_prompts.py`

**Interfaces:**
- Produces: `CatalogOption.payload: dict[str, str]` (default `{}`). Task 7
  reads `payload.get("motion"/"transitions"/"grade")`; Task 8 reads the
  full 10-key palette payload.

### Step 6.1 — failing test

Append to `tests/pipeline/test_prompts.py`:

```python
def test_load_catalog_parses_a_brand_presets_payload_into_a_dict():
    catalog = load_catalog()
    modern_dark = catalog["brand_presets"]["modern_dark"]
    assert modern_dark.payload["bg"] == "#0B0F1A"
    assert modern_dark.payload["accent1"] == "#FF4D6D"
    assert modern_dark.payload["panel"] == "rgba(15,22,36,0.88)"


def test_video_tone_options_get_an_empty_payload_not_a_crash():
    # video_tone's hint is prose, not key=value pairs - payload must
    # degrade to {} rather than raising on the first token with no '='.
    catalog = load_catalog()
    tone = catalog["video_tone"]["documentary_cinematic"]
    assert tone.payload == {}


def test_option_payload_tolerates_a_malformed_token(tmp_path):
    catalog_file = tmp_path / "catalog.txt"
    catalog_file.write_text(
        "## CATEGORY: brand_presets\n"
        "## OPTION: broken | Broken | bg=#111111;not_a_pair;accent1=#222222\n",
        encoding="utf-8",
    )
    catalog = load_catalog(catalog_file)
    opt = catalog["brand_presets"]["broken"]
    assert opt.payload == {"bg": "#111111", "accent1": "#222222"}
```

Run: `.venv/bin/python -m pytest tests/pipeline/test_prompts.py -q -k payload`
Expected: FAIL — `AttributeError: 'CatalogOption' object has no attribute
'payload'`.

### Step 6.2 — implement

In `avg/pipeline/prompts.py`, add `field` to the dataclasses import.
Replace:

```python
from dataclasses import dataclass
```

with:

```python
from dataclasses import dataclass, field
```

Replace the `CatalogOption` class:

```python
@dataclass(frozen=True, slots=True)
class CatalogOption:
    """One `## OPTION: id | Label | hint` row from `dynamic_options.txt`."""

    id: str
    label: str
    hint: str
```

with:

```python
@dataclass(frozen=True, slots=True)
class CatalogOption:
    """One `## OPTION: id | Label | hint` row from `dynamic_options.txt`.

    `payload` is `hint` re-parsed as `key=value;key=value` pairs, when it
    looks like that shape - `brand_presets`' hint carries a real colour/
    style payload (see `resolve_brand_style_bible`, `Task 8`'s
    `resolve_brand`), while every other category's hint is prose a `key=`
    token would never appear in. A malformed token (no `=`) is skipped,
    never raised - matches the legacy `Set-BrandPresetProperty` parser's
    own tolerance. Categories whose hint is prose degrade to `{}`."""

    id: str
    label: str
    hint: str
    payload: dict[str, str] = field(default_factory=dict)
```

Add a parsing helper directly above `_parse_catalog`:

```python
def _parse_payload(raw: str) -> dict[str, str]:
    """`"key=value;key=value"` -> `{"key": "value", ...}`. A token with no
    `=` (ordinary prose, not a payload) is skipped rather than raising -
    this is what lets `_parse_catalog` call it unconditionally for every
    option's hint, prose or not."""
    payload: dict[str, str] = {}
    for token in raw.split(";"):
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip()
        if key:
            payload[key] = value.strip()
    return payload
```

Update `_parse_catalog` to populate it. Replace:

```python
            opt_id = parts[0]
            label = parts[1] if len(parts) > 1 else opt_id
            hint = parts[2] if len(parts) > 2 else ""
            catalog[current][opt_id] = CatalogOption(id=opt_id, label=label, hint=hint)
```

with:

```python
            opt_id = parts[0]
            label = parts[1] if len(parts) > 1 else opt_id
            hint = parts[2] if len(parts) > 2 else ""
            catalog[current][opt_id] = CatalogOption(
                id=opt_id, label=label, hint=hint, payload=_parse_payload(hint)
            )
```

### Step 6.3 — run to verify pass

Run: `.venv/bin/python -m pytest tests/pipeline/test_prompts.py -q`
Expected: PASS. Confirm `test_load_catalog_parses_video_tone_category` and
`test_load_catalog_parses_brand_presets_category` (pre-existing) still
pass unmodified — they only assert `label`/`hint`, both untouched.

### Step 6.4 — commit

```bash
git add avg/pipeline/prompts.py tests/pipeline/test_prompts.py
git commit -m "feat: CatalogOption exposes its hint's key=value payload

brand_presets' full palette was already sitting in the bundled catalog,
unparsed past the raw hint string. Tolerant of prose hints (video_tone)
and malformed tokens - both degrade to an absent/skipped entry."
```

---

## Task 7 — `_BRAND_STYLE_DEFAULTS` and the style-bible rewrite

**Files:**
- Modify: `avg/pipeline/prompts.py`
- Test: `tests/pipeline/test_prompts.py`

**Interfaces:**
- Consumes: `CatalogOption.payload` (Task 6).
- Produces: `resolve_brand_style_bible(value, catalog=None) -> dict[str,
  str]` (always has `"motion"`/`"transitions"`/`"grade"` keys).
  `build_director_style_bible`'s output text changes; Task 4/5 (Director
  II's motion/grade prompt) already consumes `build_director_style_bible`
  transitively via `build_director2_prompt` - no signature change there.

### Step 7.1 — failing tests

Append to `tests/pipeline/test_prompts.py`:

```python
def test_resolve_brand_style_bible_known_preset_matches_legacy_defaults():
    from avg.pipeline.prompts import resolve_brand_style_bible

    bible = resolve_brand_style_bible("neon_cyber")
    assert bible["motion"] == "punchy fast pushes and quick pans, high energy"
    assert bible["transitions"] == "cut, whip, slide_left"
    assert bible["grade"] == "vibrant"


def test_resolve_brand_style_bible_unknown_id_returns_the_fallback():
    from avg.pipeline.prompts import resolve_brand_style_bible

    bible = resolve_brand_style_bible("some_id_that_does_not_exist")
    assert bible == {
        "motion": "balanced push-ins with occasional drifts",
        "transitions": "cut, crossfade",
        "grade": "none",
    }


def test_resolve_brand_style_bible_blank_id_returns_the_fallback():
    from avg.pipeline.prompts import resolve_brand_style_bible

    assert resolve_brand_style_bible("")["grade"] == "none"


def test_resolve_brand_style_bible_every_preset_in_the_bundled_catalog_resolves():
    # Every id catalog() actually ships must produce a full, valid bible -
    # including the 5 presets this app added that legacy never had.
    from avg.pipeline.prompts import _VALID_TRANSITIONS_FOR_TEST, resolve_brand_style_bible

    catalog = load_catalog()
    for preset_id in catalog["brand_presets"]:
        bible = resolve_brand_style_bible(preset_id)
        assert bible["motion"]
        assert bible["transitions"]
        assert bible["grade"]


def test_resolve_brand_style_bible_payload_override_wins_over_the_default():
    catalog = _parse_catalog_for_test(
        "## CATEGORY: brand_presets\n"
        "## OPTION: modern_dark | Modern Dark | motion=totally different motion;grade=noir\n"
    )
    from avg.pipeline.prompts import resolve_brand_style_bible

    bible = resolve_brand_style_bible("modern_dark", catalog)
    assert bible["motion"] == "totally different motion"
    assert bible["grade"] == "noir"
    # transitions wasn't overridden - the built-in default still applies.
    assert bible["transitions"] == "cut, crossfade"


def test_style_bible_text_includes_the_brand_motion_and_grade():
    inputs = _inputs(brand_preset="neon_cyber")
    text = build_director_style_bible(inputs)
    assert "brand motion feel: punchy fast pushes and quick pans, high energy" in text
    assert "brand grade: vibrant" in text


def test_style_bible_text_has_no_brand_lines_when_brand_preset_is_blank():
    inputs = _inputs(brand_preset="")
    text = build_director_style_bible(inputs)
    assert "brand" not in text
```

Two test helpers referenced above don't exist yet — add them near the top
of the "prompts.py: catalogue loading" section (right after the
`KNOWN_TONE_ID`/`KNOWN_TONE_SNIPPET` constants):

```python
def _parse_catalog_for_test(raw: str):
    from avg.pipeline.prompts import _parse_catalog

    return _parse_catalog(raw)
```

Drop the `_VALID_TRANSITIONS_FOR_TEST` import in
`test_resolve_brand_style_bible_every_preset_in_the_bundled_catalog_resolves`
— it isn't needed (leftover from drafting); the test body only needs
`load_catalog`/`resolve_brand_style_bible`. Write it as:

```python
def test_resolve_brand_style_bible_every_preset_in_the_bundled_catalog_resolves():
    # Every id catalog() actually ships must produce a full, valid bible -
    # including the 5 presets this app added that legacy never had.
    from avg.pipeline.prompts import resolve_brand_style_bible

    catalog = load_catalog()
    for preset_id in catalog["brand_presets"]:
        bible = resolve_brand_style_bible(preset_id)
        assert bible["motion"]
        assert bible["transitions"]
        assert bible["grade"]
```

Run: `.venv/bin/python -m pytest tests/pipeline/test_prompts.py -q -k "brand_style or style_bible_text"`
Expected: FAIL — `ImportError: cannot import name 'resolve_brand_style_bible'`.

### Step 7.2 — implement

In `avg/pipeline/prompts.py`, add directly after `resolve_brand_preset_label`:

```python
# Ported from `$script:BRAND_STYLE_DEFAULTS`
# (`D:\\AI_Video_Gen\\modules\\utilities.ps1`): a brand preset carries a full
# LOOK (motion feel, transition palette, colour grade), not just colours -
# this is what turns "changing the brand only recolours" into "a different
# brand feels like a different video." The five entries below are the
# legacy app's own, verbatim. The other five keys
# (wireframe_ink/tiktok_viral/vsauce_curiosity/academic_editorial/
# tactical_terminal) are presets this app added that legacy never had -
# profiles written to match each one's own palette/label in
# `avg/assets/dynamic_options.txt`, checked against the same closed
# `_VALID_TRANSITIONS`/`_VALID_GRADE_LOOKS` vocabularies
# `avg.pipeline.phases.director` validates a model's own choices against.
_BRAND_STYLE_DEFAULTS: dict[str, dict[str, str]] = {
    "modern_dark": {
        "motion": "balanced push-ins with occasional slow drifts",
        "transitions": "cut, crossfade",
        "grade": "cinematic",
    },
    "neon_cyber": {
        "motion": "punchy fast pushes and quick pans, high energy",
        "transitions": "cut, whip, slide_left",
        "grade": "vibrant",
    },
    "warm_organic": {
        "motion": "gentle slow drifts and soft zoom-ins, unhurried",
        "transitions": "crossfade, dip, fade",
        "grade": "warm",
    },
    "clean_light": {
        "motion": "minimal, precise, mostly slow zoom-ins",
        "transitions": "cut, fade",
        "grade": "soft",
    },
    "luxury_gold": {
        "motion": "slow, deliberate, elegant push-ins",
        "transitions": "crossfade, dip",
        "grade": "cinematic",
    },
    "wireframe_ink": {
        "motion": "minimal, precise, mostly static holds with slow pans",
        "transitions": "cut, fade",
        "grade": "none",
    },
    "tiktok_viral": {
        "motion": "fast punchy pushes and quick cuts, high energy",
        "transitions": "cut, whip, slide_left",
        "grade": "vibrant",
    },
    "vsauce_curiosity": {
        "motion": "measured pushes with the occasional quick zoom on a reveal",
        "transitions": "crossfade, cut",
        "grade": "cool",
    },
    "academic_editorial": {
        "motion": "minimal, precise, mostly slow zoom-ins",
        "transitions": "cut, fade",
        "grade": "soft",
    },
    "tactical_terminal": {
        "motion": "sharp fast pushes and quick pans, high-alert energy",
        "transitions": "cut, whip",
        "grade": "noir",
    },
}

_BRAND_STYLE_FALLBACK: dict[str, str] = {
    "motion": "balanced push-ins with occasional drifts",
    "transitions": "cut, crossfade",
    "grade": "none",
}


def resolve_brand_style_bible(
    value: str,
    catalog: dict[str, dict[str, CatalogOption]] | None = None,
) -> dict[str, str]:
    """`{"motion", "transitions", "grade"}` for a `brand_preset` id: the
    built-in profile in `_BRAND_STYLE_DEFAULTS` (or `_BRAND_STYLE_FALLBACK`
    for a blank/unrecognised id), overridden per-key by the preset's own
    catalogue payload when it carries `motion=`/`transitions=`/`grade=` -
    ported from `Get-BrandStyleProfile`
    (`D:\\AI_Video_Gen\\modules\\utilities.ps1`). Never returns a dict
    missing any of the three keys."""
    v = (value or "").strip()
    base = dict(_BRAND_STYLE_DEFAULTS.get(v, _BRAND_STYLE_FALLBACK))
    options = (catalog or _load_bundled_catalog()).get("brand_presets", {})
    opt = options.get(v)
    if opt is not None:
        for key in ("motion", "transitions", "grade"):
            override = opt.payload.get(key, "").strip()
            if override:
                base[key] = override
    return base
```

Replace `build_director_style_bible`'s body. Replace:

```python
    preset = get_preset(inputs.content_shape, personality)
    tone_text = resolve_visual_tone(inputs.visual_tone)
    brand_label = resolve_brand_preset_label(inputs.brand_preset)
    brand_line = f"\n- brand: {brand_label}" if brand_label else ""

    return f"""VIDEO STYLE (the creative bible — keep the whole piece feeling consistent with this):
- personality: {preset.description or preset.name}
- visual tone: {tone_text}
- emotional arc: {emotional_arc}{brand_line}"""
```

with:

```python
    preset = get_preset(inputs.content_shape, personality)
    tone_text = resolve_visual_tone(inputs.visual_tone)
    brand_label = resolve_brand_preset_label(inputs.brand_preset)
    brand_section = ""
    if brand_label:
        bible = resolve_brand_style_bible(inputs.brand_preset)
        brand_section = (
            f"\n- brand: {brand_label}"
            f"\n- brand motion feel: {bible['motion']}"
            f"\n- brand transitions: {bible['transitions']}"
            f"\n- brand grade: {bible['grade']}"
        )

    return f"""VIDEO STYLE (the creative bible — keep the whole piece feeling consistent with this):
- personality: {preset.description or preset.name}
- visual tone: {tone_text}
- emotional arc: {emotional_arc}{brand_section}"""
```

Update `build_director_style_bible`'s own docstring (the "per-brand
motion-feel table" clause is no longer true) and `build_director2_prompt`'s
docstring (its "no brand motion-feel table" clause too). Replace:

```python
    """The "VIDEO STYLE (the creative bible)" block Director II's
    motion/grade prompt opens with, ported from the corresponding section
    of `ai.ps1`'s Director 2 prompt builder. Only the style-bible fragment
    is ported here (motion/transition/grade primitives, filters, and the
    per-brand motion-feel table are a separate, larger Director II
    implementation task, not this one)."""
```

with:

```python
    """The "VIDEO STYLE (the creative bible)" block Director II's
    motion/grade prompt opens with, ported from the corresponding section
    of `ai.ps1`'s Director 2 prompt builder, including the per-brand
    motion-feel table (`resolve_brand_style_bible`)."""
```

And in `build_director2_prompt`'s docstring, replace:

```python
    """Phase 5 (Director II)'s motion/grade prompt: opens with
    `build_director_style_bible`'s style-bible fragment, then asks for a
    per-scene camera move/transition and one overall colour grade for the
    whole video, and - when `filter_catalog` is non-empty - an optional
    per-scene filter overlay clip. Ported from the corresponding section of
    `Get-AiDirectionPlan` (`D:\\AI_Video_Gen\\modules\\ai.ps1`).
```

with:

```python
    """Phase 5 (Director II)'s motion/grade prompt: opens with
    `build_director_style_bible`'s style-bible fragment (now including the
    brand's own motion-feel/transitions/grade bias), then asks for a
    per-scene camera move/transition and one overall colour grade for the
    whole video, and - when `filter_catalog` is non-empty - an optional
    per-scene filter overlay clip. Ported from the corresponding section of
    `Get-AiDirectionPlan` (`D:\\AI_Video_Gen\\modules\\ai.ps1`).
```

### Step 7.3 — run to verify pass

Run: `.venv/bin/python -m pytest tests/pipeline/test_prompts.py -q`
Expected: PASS, including every existing `build_director_style_bible`
test — check for one asserting the OLD single `"- brand: {label}"` line
shape; if `test_style_bible_includes_brand_when_set` (or similarly named)
exists and asserts the exact old one-line form, update its assertion to
check for `"brand motion feel:"` instead (same intent - brand info is
present - just proving the new richer shape). Search first:

```bash
grep -n "brand:" tests/pipeline/test_prompts.py
```

### Step 7.4 — commit

```bash
git add avg/pipeline/prompts.py tests/pipeline/test_prompts.py
git commit -m "feat: brand-preset style bible (motion/transitions/grade)

resolve_brand_style_bible ports $script:BRAND_STYLE_DEFAULTS verbatim
for the 5 presets legacy had, plus mood-matched profiles for the 5 this
app added. Director II's style bible now biases toward the selected
brand's feel, not just its label."
```

---

## Task 8 — `resolve_brand` uses the preset's palette; real render threads it

**Files:**
- Modify: `avg/media/overlays.py`, `avg/pipeline/phases/director.py`
- Test: `tests/media/test_overlays.py`, `tests/pipeline/test_phases_polish.py`

**Interfaces:**
- Consumes: `CatalogOption.payload` via `avg.pipeline.prompts.load_catalog`
  (Task 6, called only from `director.py` — `avg/media/` still never
  imports `avg/pipeline/`).
- Produces: `resolve_brand(..., preset_payload: dict[str, str] | None =
  None)`, `render_layers`/`write_layers` gain the same trailing parameter.
  Task 9 threads the identical parameter through the preview path.

### Step 8.1 — failing tests

Append to `tests/media/test_overlays.py`:

```python
def test_resolve_brand_uses_the_preset_payload_as_its_base():
    from avg.media.overlays import resolve_brand

    brand = resolve_brand(preset_payload={"bg": "#111111", "accent1": "#ABCDEF"})
    assert brand["bg"] == "#111111"
    assert brand["accent1"] == "#ABCDEF"
    # A key the payload didn't carry keeps the library's own default.
    assert brand["highlight"] == "#FFD93D"


def test_resolve_brand_explicit_accent_still_wins_over_the_preset_payload():
    from avg.media.overlays import resolve_brand

    brand = resolve_brand(accent1="#00FF00", preset_payload={"accent1": "#ABCDEF"})
    assert brand["accent1"] == "#00FF00"


def test_resolve_brand_with_no_preset_payload_is_unchanged_from_before():
    from avg.media.overlays import resolve_brand
    from avg.media.vendor import overlay_design as od

    assert resolve_brand() == dict(od.DEFAULT_BRAND)
```

Run: `.venv/bin/python -m pytest tests/media/test_overlays.py -q -k preset_payload`
Expected: FAIL — `TypeError: resolve_brand() got an unexpected keyword
argument 'preset_payload'`.

Append to `tests/pipeline/test_phases_polish.py`:

```python
def test_draw_overlay_assets_resolves_the_brand_presets_payload(tmp_path, monkeypatch):
    from avg.pipeline.phases.director import _draw_overlay_assets

    monkeypatch.setattr("avg.media.vendor.avg._target_size", lambda ar, se: (1080, 1920))
    captured: list[tuple] = []
    monkeypatch.setattr("avg.media.overlays.write_layers", lambda *args: captured.append(args) or 1)

    project = _project(tmp_path)
    channel = _channel({"brand_preset": "neon_cyber"})
    ctx, _ = _ctx(project, channel=channel)
    ctx.channel_values = resolve_channel_settings(channel.settings, {})
    rdir = render_dir(ctx)
    rdir.mkdir(parents=True, exist_ok=True)

    _draw_overlay_assets(ctx, rdir, {"overlays": []})

    preset_payload = captured[0][9]
    assert preset_payload["bg"] == "#050014"
    assert preset_payload["accent1"] == "#00F0FF"


def test_draw_overlay_assets_with_no_brand_preset_passes_none(tmp_path, monkeypatch):
    from avg.pipeline.phases.director import _draw_overlay_assets

    monkeypatch.setattr("avg.media.vendor.avg._target_size", lambda ar, se: (1080, 1920))
    captured: list[tuple] = []
    monkeypatch.setattr("avg.media.overlays.write_layers", lambda *args: captured.append(args) or 1)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)  # default channel: no brand_preset set
    rdir = render_dir(ctx)
    rdir.mkdir(parents=True, exist_ok=True)

    _draw_overlay_assets(ctx, rdir, {"overlays": []})

    assert captured[0][9] is None
```

Run: `.venv/bin/python -m pytest tests/pipeline/test_phases_polish.py -q -k brand_presets_payload`
Expected: FAIL — `IndexError: tuple index out of range` (`write_layers` is
still only called with 9 positional args).

### Step 8.2 — implement `resolve_brand`/`render_layers`/`write_layers`

In `avg/media/overlays.py`, replace `resolve_brand`'s signature and body:

```python
def resolve_brand(font: str = "", accent1: str = "", accent2: str = "", highlight: str = "") -> dict[str, Any]:
    """The ONE brand resolver, shared by a real render (`render_layers`
    below, via Director II) and a settings preview
    (`avg.media.preview._draw_to_png`) - so an overlay setting changes the
    frame the same way in both places, which is the entire point of Slice
    14 review's Important 4: `avg.media.preview._brand()` used to hardcode
    `DEFAULT_BRAND` regardless of what the channel's settings said, so no
    overlay setting could ever change the preview it was supposedly
    driving.

    Takes raw setting VALUES rather than a `PhaseContext`, same shape as
    `avg.pipeline.phases._common.resolve_font_family_value`: `avg/media/`
    may not import `avg/pipeline/`, so the pipeline layer resolves its own
    settings and hands plain strings in here, and a preview route (which
    has no channel to read from beyond the request body) can call this
    with exactly the same values a debounced settings change carries.

    Every argument left blank keeps the library's own `DEFAULT_BRAND`
    value - a setting that has never been touched must draw exactly as it
    did before this function existed.
    """
    from avg.media.vendor import overlay_design as od

    brand: dict[str, Any] = dict(od.DEFAULT_BRAND)
    if font:
        # The brand carries font PATHS, not family names; a family name is
        # all this app's settings have, so it is only useful when it happens
        # to be a path. Anything else leaves the library's own default.
        if Path(font).suffix.lower() in (".ttf", ".otf"):
            brand["font_path"] = font
    if accent1:
        brand["accent1"] = accent1
    if accent2:
        brand["accent2"] = accent2
    if highlight:
        brand["highlight"] = highlight
    return brand
```

with:

```python
def resolve_brand(
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The ONE brand resolver, shared by a real render (`render_layers`
    below, via Director II) and a settings preview
    (`avg.media.preview._draw_to_png`) - so an overlay setting changes the
    frame the same way in both places, which is the entire point of Slice
    14 review's Important 4: `avg.media.preview._brand()` used to hardcode
    `DEFAULT_BRAND` regardless of what the channel's settings said, so no
    overlay setting could ever change the preview it was supposedly
    driving.

    Takes raw setting VALUES rather than a `PhaseContext`, same shape as
    `avg.pipeline.phases._common.resolve_font_family_value`: `avg/media/`
    may not import `avg/pipeline/`, so the pipeline layer resolves its own
    settings and hands plain strings in here, and a preview route (which
    has no channel to read from beyond the request body) can call this
    with exactly the same values a debounced settings change carries.

    `preset_payload` (the selected `brand_preset`'s own catalogue payload -
    `bg`/`panel`/`panel_stroke`/`text`/`text2`/`accent1`/`accent2`/
    `highlight`/`font_path`/`font_bold_path`, resolved by
    `avg.pipeline.prompts.load_catalog` at the call site, never here) is
    the new BASE palette, applied over `DEFAULT_BRAND` before the explicit
    arguments below - so `font`/`accent1`/`accent2`/`highlight` still win
    when a channel has set them by hand, exactly as before. `None`/`{}`
    behaves exactly as it always has: `DEFAULT_BRAND` untouched.

    Every argument left blank keeps whichever value is already in `brand`
    at that point - a setting that has never been touched must draw
    exactly as it did before this function existed.
    """
    from avg.media.vendor import overlay_design as od

    brand: dict[str, Any] = dict(od.DEFAULT_BRAND)
    if preset_payload:
        for key in (
            "bg", "panel", "panel_stroke", "text", "text2",
            "accent1", "accent2", "highlight", "font_path", "font_bold_path",
        ):
            value = preset_payload.get(key, "").strip()
            if value:
                brand[key] = value
    if font:
        # The brand carries font PATHS, not family names; a family name is
        # all this app's settings have, so it is only useful when it happens
        # to be a path. Anything else leaves the library's own default.
        if Path(font).suffix.lower() in (".ttf", ".otf"):
            brand["font_path"] = font
    if accent1:
        brand["accent1"] = accent1
    if accent2:
        brand["accent2"] = accent2
    if highlight:
        brand["highlight"] = highlight
    return brand
```

Replace `render_layers`'s signature and its `resolve_brand` call. Replace:

```python
def render_layers(
    project_folder: Path,
    edit_plan: dict[str, Any],
    scene_timings: list[dict[str, Any]],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
) -> list[dict[str, Any]]:
    """One `{png, start, end}` row per drawable layer.

    An overlay whose scene has no timing is SKIPPED rather than drawn at a
    guessed moment - the vendored compositor makes the same choice with the
    same input (`avg.py:3181-3183`), and failing here would throw away a
    whole render over one stale card. A drawing failure is skipped for the
    same reason, per layer.
    """
    from avg.media.vendor import overlay_design as od

    brand = resolve_brand(font, accent1, accent2, highlight)
```

with:

```python
def render_layers(
    project_folder: Path,
    edit_plan: dict[str, Any],
    scene_timings: list[dict[str, Any]],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One `{png, start, end}` row per drawable layer.

    An overlay whose scene has no timing is SKIPPED rather than drawn at a
    guessed moment - the vendored compositor makes the same choice with the
    same input (`avg.py:3181-3183`), and failing here would throw away a
    whole render over one stale card. A drawing failure is skipped for the
    same reason, per layer.
    """
    from avg.media.vendor import overlay_design as od

    brand = resolve_brand(font, accent1, accent2, highlight, preset_payload)
```

Replace `write_layers`'s signature and its `render_layers` call. Replace:

```python
def write_layers(
    project_folder: Path,
    edit_plan: dict[str, Any],
    scene_timings: list[dict[str, Any]],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
) -> int:
    """Render the layers and write `render/_final_layers.json`.

    Writes the file only when there is at least one layer: its very
    EXISTENCE is the flag that tells the caption pass to bake overlays in
    the same encode, so an empty list would send the render down the
    combined path with nothing to combine.
    """
    layers = render_layers(
        project_folder, edit_plan, scene_timings, width, height, font, accent1, accent2, highlight
    )
```

with:

```python
def write_layers(
    project_folder: Path,
    edit_plan: dict[str, Any],
    scene_timings: list[dict[str, Any]],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> int:
    """Render the layers and write `render/_final_layers.json`.

    Writes the file only when there is at least one layer: its very
    EXISTENCE is the flag that tells the caption pass to bake overlays in
    the same encode, so an empty list would send the render down the
    combined path with nothing to combine.
    """
    layers = render_layers(
        project_folder, edit_plan, scene_timings, width, height, font, accent1, accent2, highlight, preset_payload
    )
```

### Step 8.3 — implement `_draw_overlay_assets`'s brand-preset lookup

In `avg/pipeline/phases/director.py`, add `load_catalog` to the existing
prompts import. Replace:

```python
from avg.pipeline.prompts import build_director1_prompt, build_director2_prompt
```

with:

```python
from avg.pipeline.prompts import build_director1_prompt, build_director2_prompt, load_catalog
```

Replace `_draw_overlay_assets`'s body:

```python
    try:
        width, height = _target_size(ctx.project.aspect_ratio, ctx.project.short_edge)  # type: ignore[no-untyped-call]
        settings = ctx.channel_values
        return write_layers(
            Path(ctx.project.folder),
            edit_plan,
            read_json(rdir / "scene_timings.json") or [],
            int(width),
            int(height),
            resolve_font_family(ctx),
            str(settings.get("overlay_accent1") or ""),
            str(settings.get("overlay_accent2") or ""),
            str(settings.get("overlay_highlight") or ""),
        )
```

with:

```python
    try:
        width, height = _target_size(ctx.project.aspect_ratio, ctx.project.short_edge)  # type: ignore[no-untyped-call]
        settings = ctx.channel_values
        brand_preset_id = str(settings.get("brand_preset") or "")
        preset_payload = None
        if brand_preset_id:
            option = load_catalog().get("brand_presets", {}).get(brand_preset_id)
            if option is not None:
                preset_payload = option.payload
        return write_layers(
            Path(ctx.project.folder),
            edit_plan,
            read_json(rdir / "scene_timings.json") or [],
            int(width),
            int(height),
            resolve_font_family(ctx),
            str(settings.get("overlay_accent1") or ""),
            str(settings.get("overlay_accent2") or ""),
            str(settings.get("overlay_highlight") or ""),
            preset_payload,
        )
```

Also update `_draw_overlay_assets`'s module-level docstring reference (the
class docstring block near the top of the file already says "Cards carry
only..." — no change needed there) and the function's own inline comment
about "three brand colours" in the module docstring's earlier "plan entry
-> vendored spec" section of `avg/media/overlays.py` if present — check:

```bash
grep -n "three brand colour" avg/media/overlays.py
```

If found, leave it — it describes `_vendor_spec`'s own accent handling,
unrelated to this task's palette-base change.

### Step 8.4 — run to verify pass

Run: `.venv/bin/python -m pytest tests/media/test_overlays.py tests/pipeline/test_phases_polish.py -q`
Expected: PASS, including the pre-existing
`test_draw_overlay_assets_inherits_accent_colors_from_the_app_wide_defaults`
(indices `captured[0][6..8]` are unaffected by the new 10th positional
arg) and every `render_layers`/`write_layers` test in `test_overlays.py`
(none pass `preset_payload`, so they all exercise the `None` default,
identical to before).

### Step 8.5 — commit

```bash
git add avg/media/overlays.py avg/pipeline/phases/director.py \
  tests/media/test_overlays.py tests/pipeline/test_phases_polish.py
git commit -m "feat: resolve_brand's base palette is the selected preset

Picking a brand_preset now visibly changes card colours, not just a
Director II prompt label. Explicit overlay_accent1/accent2/highlight
settings still win over the preset - same precedence as before, just
layered on a real base instead of always DEFAULT_BRAND."
```

---

## Task 9 — Thread `preset_payload` through the settings-preview path

**Files:**
- Modify: `avg/media/preview.py`, `avg/api/routes_preview.py`
- Test: `tests/media/test_preview.py` (create if it doesn't already exist —
  check first), `tests/api/test_routes_preview.py`

**Interfaces:**
- Consumes: `resolve_brand`'s `preset_payload` param (Task 8),
  `avg.pipeline.prompts.load_catalog` (Task 6 — imported in
  `routes_preview.py`, the API layer, never in `avg/media/`).
- Produces: `overlay_png`/`takeover_png`/`_draw_to_png` gain
  `preset_payload`; `OverlayPreview`/`TakeoverPreview` gain `brand_preset`.

### Step 9.1 — check what already exists

```bash
find tests -iname "test_preview.py"
grep -n "def test_" tests/media/test_preview.py 2>/dev/null | head -20
grep -n "def test_.*overlay\|def test_.*takeover" tests/api/test_routes_preview.py
```

Match the existing style found (fixture names, whether `preview.py` is
tested via direct function calls or FastAPI `TestClient`) before adding —
if `tests/media/test_preview.py` doesn't exist, add the new tests to
`tests/api/test_routes_preview.py` instead (whichever file already covers
`overlay_png`/`preview_overlay` today) rather than creating a new file
that duplicates existing fixtures.

### Step 9.2 — failing tests

Add (adjusting fixture setup to match whatever `test_routes_preview.py`
already uses for its FastAPI app/client — mirror an existing
`preview_overlay`/`preview_takeover` test's request-building exactly,
changing only the asserted field):

```python
def test_preview_overlay_resolves_a_named_brand_preset(client):
    response = client.post(
        "/api/preview/overlay",
        json={"spec": {"element": "focused", "headline": "Hi"}, "brand_preset": "neon_cyber"},
    )
    assert response.status_code == 200
    # A pixel-level colour assertion is brittle against PNG encoding - the
    # request succeeding with a named preset (previously an unrecognised
    # field FastAPI would simply ignore) plus the unit-level
    # resolve_brand tests in Task 8 are the correctness proof; this proves
    # the wiring reaches resolve_brand without raising.


def test_preview_overlay_with_no_brand_preset_still_works(client):
    response = client.post(
        "/api/preview/overlay",
        json={"spec": {"element": "focused", "headline": "Hi"}},
    )
    assert response.status_code == 200
```

Also add a focused unit test for `preview.py`'s own functions (adjust the
import path if these already live in a `tests/media/test_preview.py`):

```python
def test_overlay_png_passes_preset_payload_through_to_resolve_brand(monkeypatch):
    from avg.media import preview

    captured = {}
    monkeypatch.setattr(
        "avg.media.overlays.resolve_brand",
        lambda font, accent1, accent2, highlight, preset_payload=None: captured.update(
            preset_payload=preset_payload
        )
        or {},
    )
    monkeypatch.setattr(
        "avg.media.vendor.overlay_design.render_overlay", lambda spec, w, h, brand: None
    )

    preview.overlay_png({"element": "focused"}, 100, 100, preset_payload={"bg": "#111111"})

    assert captured["preset_payload"] == {"bg": "#111111"}
```

Run: `.venv/bin/python -m pytest tests/api/test_routes_preview.py tests/media/test_preview.py -q -k "brand_preset or preset_payload"`
(drop whichever file doesn't exist from the command)
Expected: FAIL — `preview_overlay`/`overlay_png` don't accept
`brand_preset`/`preset_payload` yet.

### Step 9.3 — implement `preview.py`

In `avg/media/preview.py`, replace `_draw_to_png`'s signature and body:

```python
def _draw_to_png(
    spec: dict[str, Any],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
) -> bytes:
```

with:

```python
def _draw_to_png(
    spec: dict[str, Any],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> bytes:
```

and its body's `resolve_brand` call, replacing:

```python
    try:
        brand = resolve_brand(font, accent1, accent2, highlight)
```

with:

```python
    try:
        brand = resolve_brand(font, accent1, accent2, highlight, preset_payload)
```

Replace `overlay_png`'s signature and body:

```python
def overlay_png(
    spec: dict[str, Any],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
) -> bytes:
    """One overlay, drawn exactly as the render would draw it."""
    return _draw_to_png(spec, width, height, font, accent1, accent2, highlight)
```

with:

```python
def overlay_png(
    spec: dict[str, Any],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> bytes:
    """One overlay, drawn exactly as the render would draw it."""
    return _draw_to_png(spec, width, height, font, accent1, accent2, highlight, preset_payload)
```

Replace `takeover_png`'s signature and body:

```python
def takeover_png(
    headline: str,
    sub: str,
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
) -> bytes:
    """One takeover card, drawn exactly as the render would draw it.

    The element name matches what `avg.media.overlays.render_layers` builds
    for a real takeover, so this is the same card the video will show.
    """
    return _draw_to_png(
        {"element": "chapters_takeover_cards", "headline": headline, "sub": sub},
        width,
        height,
        font,
        accent1,
        accent2,
        highlight,
    )
```

with:

```python
def takeover_png(
    headline: str,
    sub: str,
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> bytes:
    """One takeover card, drawn exactly as the render would draw it.

    The element name matches what `avg.media.overlays.render_layers` builds
    for a real takeover, so this is the same card the video will show.
    """
    return _draw_to_png(
        {"element": "chapters_takeover_cards", "headline": headline, "sub": sub},
        width,
        height,
        font,
        accent1,
        accent2,
        highlight,
        preset_payload,
    )
```

### Step 9.4 — implement `routes_preview.py`

In `avg/api/routes_preview.py`, add `brand_preset` to both request models.
Replace:

```python
class OverlayPreview(BaseModel):
    spec: dict[str, Any]
    width: int = 1080
    height: int = 1920
    #: RAW setting values (`avg.core.settings_schema`: `font_family=""`,
    #: `overlay_accent1`/`overlay_accent2`/`overlay_highlight` all default
    #: `""`), resolved by `avg.media.overlays.resolve_brand` - the same
    #: function a real render calls - rather than by a fallback invented
    #: here. Same shape as `CaptionPreview`'s `font_name`/`font_size` above,
    #: and for the same reason (Important 1's fix, applied to Important 4).
    font: str = ""
    accent1: str = ""
    accent2: str = ""
```

with:

```python
class OverlayPreview(BaseModel):
    spec: dict[str, Any]
    width: int = 1080
    height: int = 1920
    #: RAW setting values (`avg.core.settings_schema`: `font_family=""`,
    #: `overlay_accent1`/`overlay_accent2`/`overlay_highlight` all default
    #: `""`), resolved by `avg.media.overlays.resolve_brand` - the same
    #: function a real render calls - rather than by a fallback invented
    #: here. Same shape as `CaptionPreview`'s `font_name`/`font_size` above,
    #: and for the same reason (Important 1's fix, applied to Important 4).
    font: str = ""
    accent1: str = ""
    accent2: str = ""
    #: The channel's `brand_preset` catalogue id (raw, same as the above) -
    #: resolved to its palette payload in the route below, same
    #: `avg.pipeline.prompts.load_catalog` call `_draw_overlay_assets` makes
    #: for a real render, so a brand-preset colour change previews exactly
    #: as it will render (Slice 14's "one resolver" rule, extended).
    brand_preset: str = ""
```

Look for `highlight: str = ""` immediately after — confirm it's still
there unchanged (it is; only the lines above it move). Then replace
`TakeoverPreview`:

```python
class TakeoverPreview(BaseModel):
    headline: str = ""
    sub: str = ""
    width: int = 1080
    height: int = 1920
    font: str = ""
    accent1: str = ""
    accent2: str = ""
    highlight: str = ""
```

with:

```python
class TakeoverPreview(BaseModel):
    headline: str = ""
    sub: str = ""
    width: int = 1080
    height: int = 1920
    font: str = ""
    accent1: str = ""
    accent2: str = ""
    highlight: str = ""
    brand_preset: str = ""
```

Add a shared resolver right above `preview_overlay` (after the
`_preview_failure` function, before the `@router.post("/api/preview/overlay")`
line):

```python
def _preset_payload(brand_preset: str) -> dict[str, str] | None:
    """`body.brand_preset`'s catalogue payload, or `None` - the same lookup
    `avg.pipeline.phases.director._draw_overlay_assets` makes for a real
    render, so a preview and the render it previews can never disagree
    about a brand preset's colours again (Slice 14's "one resolver" rule)."""
    from avg.pipeline.prompts import load_catalog  # noqa: PLC0415

    if not brand_preset:
        return None
    option = load_catalog().get("brand_presets", {}).get(brand_preset)
    return option.payload if option is not None else None
```

Replace `preview_overlay`'s body:

```python
    data = await asyncio.to_thread(
        overlay_png, body.spec, body.width, body.height, body.font, body.accent1, body.accent2, body.highlight
    )
```

with:

```python
    data = await asyncio.to_thread(
        overlay_png,
        body.spec,
        body.width,
        body.height,
        body.font,
        body.accent1,
        body.accent2,
        body.highlight,
        _preset_payload(body.brand_preset),
    )
```

Replace `preview_takeover`'s body:

```python
    data = await asyncio.to_thread(
        takeover_png,
        body.headline,
        body.sub,
        body.width,
        body.height,
        body.font,
        body.accent1,
        body.accent2,
        body.highlight,
    )
```

with:

```python
    data = await asyncio.to_thread(
        takeover_png,
        body.headline,
        body.sub,
        body.width,
        body.height,
        body.font,
        body.accent1,
        body.accent2,
        body.highlight,
        _preset_payload(body.brand_preset),
    )
```

### Step 9.5 — run to verify pass

Run: `.venv/bin/python -m pytest tests/api/test_routes_preview.py tests/media/test_preview.py -q`
(drop whichever file doesn't exist)
Expected: PASS, including every pre-existing `preview_overlay`/
`preview_takeover` test (none send `brand_preset`, so `_preset_payload("")`
returns `None`, identical to before).

Then the whole suite, to catch anything Tasks 1-9 collectively missed:

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS at this sandbox's documented baseline (the pre-existing
DPAPI/font-related environment failures noted in
`.superpowers/sdd/progress.md` — nothing from this plan should add to that
count).

### Step 9.6 — commit

```bash
git add avg/media/preview.py avg/api/routes_preview.py \
  tests/api/test_routes_preview.py tests/media/test_preview.py
git commit -m "feat: settings-preview panel resolves the brand preset too

Without this, the preview would silently diverge from a real render for
any preset carrying a non-default palette - the exact class of bug
Slice 14 (Important 4) was written to fix, reintroduced by Task 8 if
left here."
```

---

## Self-review (performed while writing this plan; Linux-port pass added 2026-09-06)

**Linux port pass:** every file this plan touches except
`avg/media/environment.py` was confirmed byte-identical between
`main`/`development` and `linux-port` by direct `diff`, not assumed —
so every replace-block in Tasks 1, 3-9 applies exactly as written on
this branch. `avg/media/environment.py` needed no functional change
either (Task 2): `bundled_ffmpeg_dir()`'s frozen/dev dual-path pattern
this task deliberately mirrors is itself untouched code on this branch
(only its two `_FFMPEG_EXE_NAME`/`_FFPROBE_EXE_NAME` string constants
differ, which Task 2 never references). The one genuine addition, Task
1B, is a real packaging gap this port surfaced and fixed with a change
that has nothing to do with filters specifically — `avg/assets/` was
already failing to reach a real `pip install .` before this plan
existed, confirmed by reproducing it in a scratch install both before
and after the fix.

**Spec coverage** — every §-numbered item in
`docs/superpowers/specs/2026-09-02-slice-31-filters-brand-kit-design.md`
maps to a task: §1/§2 → Task 1, §3 → Tasks 3-5, §4 → Task 2 (+ the
already-live vendor render side), §5 → Tasks 6, 8, 9 (spec only anticipated
8; 9 is a correction to a gap the spec's own §5 claim missed - see below),
§6 → Task 7, §7 (data/schema table) → covered across all nine, §8 (testing)
→ each task's own test step, §9 (out of scope) → untouched by this plan.

**Deviation from the spec, and why:** §5 asserted "[Both call sites]
change together automatically" once `resolve_brand`'s base palette
changes. Tracing the actual call chain during planning showed this is
false as written — `avg/api/routes_preview.py`'s `OverlayPreview`/
`TakeoverPreview` never carried a `brand_preset` field at all, so the
preview path would have kept calling `resolve_brand` with `preset_payload`
always `None`, silently diverging from a real render for any preset with a
non-default palette. Task 9 closes that gap; without it this plan would
reintroduce the exact bug Slice 14 (Important 4) fixed once already.

**Also dropped from the spec's original sketch:** a standalone
`avg.pipeline.filters` module (spec §1). `avg.media.vendor.avg` already
has a tolerant, cached `load_filter_catalog()` parsing the identical file
format; Task 4 calls it directly (already permitted — `director.py`
imports other vendor functions the same way) instead of duplicating the
parser. Same data, same settings, same caps the spec describes — leaner
path to get there.

**Placeholder scan:** no "TBD"/"add error handling"/"similar to Task N"
anywhere above; every step carries real code or an exact shell command.

**Type consistency:** `filter_catalog: dict[str, str]` is the same shape
from Task 3's definition through Task 4's construction and Task 5's
`valid_filters = frozenset(eligible_filters)` (a `dict`'s keys, matching
`_normalize_direction_entry`'s `valid_filters: frozenset[str]` parameter).
`preset_payload: dict[str, str] | None` is identical from Task 6
(`CatalogOption.payload`) through Tasks 8 and 9's every signature. No
function is called anywhere with an arity or type its own task didn't
just define.
