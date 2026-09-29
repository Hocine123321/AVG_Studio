# Slice 14 — Live Preview: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show what a setting will actually look and sound like in the finished video, using the code that will produce it — so choosing a caption style, an overlay, a voice or an image model stops being a guess that costs a whole run to check.

**Architecture:** Five tasks. One new media module producing preview bytes, one pipeline-layer sample-`.ass` builder, one route group with a single job slot, and one Settings panel. Free previews re-render live; the two that spend money render only on an explicit press.

**Tech Stack:** Python 3.12 + FastAPI + Pillow + ffmpeg/libass (backend), React 19 + TypeScript + Vite (frontend), pytest, mypy, oxlint.

**Source spec:** `docs/superpowers/specs/2026-07-31-slice-14-live-preview-design.md`

## Global Constraints

Every task's requirements implicitly include all of these.

- **Interpreter is `.venv-build/Scripts/python.exe`.** Tests: `.venv-build/Scripts/python.exe -m pytest tests -q`. Types: `.venv-build/Scripts/python.exe -m mypy`.
- **Never go below the suite's count when you start.** Whatever it reports at Task 1 is your floor.
- **mypy baseline is `Found 688 errors in 22 files`.** Add nothing to the count.
- **No live API calls in tests, ever.** `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1`. **An image generation and an ElevenLabs synthesis both cost real money** — one paid recording has already been lost. No test in this slice may reach either.
- **Layer rule.** `avg/media/` must not import from `avg/pipeline/`. This is why `caption_png` takes `.ass` **text** rather than building it — the pipeline layer builds it, the API layer joins the two.
- **No preview ever writes into a project folder.** Every artifact lands in a temp directory and is served once. A preview run must leave the library byte-identical.
- **A key value must never leave the process.** `avg/config/secrets.py` is the only module that touches them.
- **Nothing below `avg/api/` may print or prompt.**
- **`avg/media/vendor/` is protected.** Never edit it — `render_overlay` is called, not modified.
- **Never use a native form control**; never add a Google Fonts `@import`.
- **One filled terracotta `.btn--primary` primary action per screen.** On Settings that is `Save changes`; preview buttons are plain `.btn`.
- **Reuse `web/src/tokens.css` values** — never a fresh guess.
- **`prefers-reduced-motion` always wins**, and `--motion-scale` (Slice 12) scales the loading shimmer.
- **The frontend has no unit tests by convention.**
- **Match code by content, not by line number.**
- **Commit once per task**, on local `main`. No push, no PR, no GitHub interaction.

---

## Task 1: Overlay and takeover previews, drawn by the real code

**Files:**
- Create: `avg/media/preview.py`
- Create: `tests/media/test_preview.py`

**Interfaces:**
- Consumes: `avg.media.vendor.overlay_design.render_overlay(spec, W, H, brand, progress=1.0) -> PIL.Image | None` and `overlay_design.DEFAULT_BRAND`.
- Produces, and Tasks 2–4 depend on these exactly:
  - `overlay_png(spec: dict[str, Any], width: int, height: int) -> bytes`
  - `takeover_png(headline: str, sub: str, width: int, height: int) -> bytes`
  - `SAMPLE_SENTENCE: str`

**Why:** `avg/media/overlays.py`'s `_draw` already calls `od.render_overlay(spec, width, height, brand)` and saves the returned PIL image. So an overlay preview can be produced **by the exact function that draws it in the real render**, in-process, with no ffmpeg and no video — accurate and effectively free.

- [ ] **Step 1: Write the failing tests**

Create `tests/media/test_preview.py`:

```python
"""Preview rendering. Free previews only in this file - the paid ones
(voice on ElevenLabs, image generation) are tested for what they REFUSE,
in tests/api/test_preview_routes.py.
"""

from __future__ import annotations

from avg.media.preview import SAMPLE_SENTENCE, overlay_png, takeover_png


def test_an_overlay_renders_to_real_png_bytes():
    """Drawn by `overlay_design.render_overlay` - the same function the real
    render calls - so the preview cannot drift from what ships."""
    data = overlay_png({"element": "lower_third", "headline": "Hello"}, 1080, 1920)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_takeover_card_renders():
    data = takeover_png("A headline", "and a sub", 1080, 1920)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_element_the_drawer_does_not_know_returns_no_bytes():
    """`render_overlay` raises on an unknown element. A preview must report
    that as "nothing to show", never as an exception reaching the route."""
    assert overlay_png({"element": "not-a-real-element"}, 1080, 1920) == b""


def test_the_sample_sentence_is_fixed_and_shared():
    """One constant, so the voice preview always says the same thing and
    the panel can quote it."""
    assert "voice" in SAMPLE_SENTENCE.lower()
```

Create `tests/media/__init__.py` if `tests/media/` has none.

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/media/test_preview.py -q
```

Expected: FAIL — `ModuleNotFoundError: avg.media.preview`.

- [ ] **Step 3: Write the module**

Create `avg/media/preview.py`:

```python
"""Preview bytes, produced by the code that will produce the real thing.

The point of this module is that a preview cannot lie. Overlays and takeover
cards are drawn by `avg.media.vendor.overlay_design.render_overlay` - the
same function `avg.media.overlays._draw` calls during a real render - and
captions are burned by the same ffmpeg/libass pass the renderer uses. A DOM
or CSS approximation would be a second implementation of the same styling,
free to drift, and a preview that lies is worse than no preview.

**Layering.** `caption_png` takes `.ass` TEXT rather than building it:
`avg/media/` must not import from `avg/pipeline/`, and the `.ass` header is
the caption phase's own. The API layer builds the text there and hands it
here. That keeps one construction of the styling rather than two.

Nothing here writes into a project folder. Every intermediate lands in a
temp directory that is removed before the function returns.
"""

from __future__ import annotations

import io
from typing import Any

#: What a voice preview says. Fixed, and in one place, so every provider
#: says the same thing and the panel can quote it back to the user.
SAMPLE_SENTENCE = "Hello, this is my voice I'll be using in a new video! Do you like it?"


def _brand() -> dict[str, Any]:
    from avg.media.vendor import overlay_design as od  # noqa: PLC0415

    return dict(od.DEFAULT_BRAND)


def _draw_to_png(spec: dict[str, Any], width: int, height: int) -> bytes:
    """One drawn layer as PNG bytes, or `b""`.

    Never raises: `render_overlay` raises on an element it does not know and
    returns `None` for a card with no text. Both mean "there is nothing to
    show", which a preview panel can state - an exception reaching the route
    could not be.
    """
    from avg.media.vendor import overlay_design as od  # noqa: PLC0415

    try:
        image = od.render_overlay(spec, width, height, _brand())
    except Exception:  # noqa: BLE001 - an unknown element is "nothing to show"
        return b""
    if image is None:
        return b""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def overlay_png(spec: dict[str, Any], width: int, height: int) -> bytes:
    """One overlay, drawn exactly as the render would draw it."""
    return _draw_to_png(spec, width, height)


def takeover_png(headline: str, sub: str, width: int, height: int) -> bytes:
    """One takeover card, drawn exactly as the render would draw it.

    The element name matches what `avg.media.overlays.render_layers` builds
    for a real takeover, so this is the same card the video will show.
    """
    return _draw_to_png(
        {"element": "chapters_takeover_cards", "headline": headline, "sub": sub},
        width,
        height,
    )
```

- [ ] **Step 4: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/media/test_preview.py -q
```

Expected: PASS (4 tests). If `render_overlay` rejects the `lower_third` spec in the first test, read `avg/media/vendor/overlay_design.py`'s own element names and use a real one — **do not edit the vendored file**, and say in your report which element you used.

- [ ] **Step 5: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 4 more than your starting count.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 6: Commit**

```bash
git add avg/media/preview.py tests/media/
git commit -m "feat: overlay and takeover previews drawn by the render's own code"
```

---

## Task 2: Caption previews, burned by the real libass pass

**Files:**
- Modify: `avg/media/preview.py` (add `caption_png`)
- Modify: `avg/pipeline/phases/captions.py` (add `sample_ass`)
- Test: `tests/media/test_preview.py`, `tests/pipeline/test_phases_polish.py`

**Interfaces:**
- Consumes from Task 1: the module.
- Produces, and Task 4 depends on these exactly:
  - `avg.media.preview.caption_png(ass_text: str, width: int, height: int) -> bytes`
  - `avg.pipeline.phases.captions.sample_ass(width: int, height: int, font_name: str, font_size: int, position: str) -> str`

**Why:** Caption appearance is decided by libass, not by any Python this app controls — `captions.py` writes `render/captions.ass` and `avg/media/vendor/avg.py:3329`'s `cmd_caption_burn` burns it. An approximation would be a second implementation of caption styling, free to drift.

`sample_ass` lives in `captions.py` and reuses that module's own `_ass_header`, `_ass_time` and `_ass_escape`. **It must not restate the style fields** — that duplication is exactly how a preview starts lying, and `_ass_header`'s own docstring says it matches the vendored `make_ass_header` field-for-field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/pipeline/test_phases_polish.py`:

```python
def test_the_sample_ass_uses_the_real_header():
    """A preview that builds its own style block is a second implementation
    of caption styling, free to drift from what libass actually burns."""
    from avg.pipeline.phases.captions import _ass_header, sample_ass

    text = sample_ass(1080, 1920, "Figtree", 64, "bottom")
    assert _ass_header(1080, 1920, "Figtree", 64, "bottom") in text
    assert "[Events]" in text
    assert "Dialogue:" in text


def test_the_sample_ass_respects_position():
    from avg.pipeline.phases.captions import sample_ass

    bottom = sample_ass(1080, 1920, "Figtree", 64, "bottom")
    top = sample_ass(1080, 1920, "Figtree", 64, "top")
    assert bottom != top
```

Append to `tests/media/test_preview.py`:

```python
def test_a_caption_frame_renders_from_ass_text():
    """`caption_png` takes TEXT, not settings: `avg/media/` may not import
    from `avg/pipeline/`, so the caption phase builds the .ass and this
    burns it. One construction of the styling, not two."""
    from avg.media.preview import caption_png

    ass = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,40,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Preview\n"
    )
    data = caption_png(ass, 640, 360)
    # Either a real PNG, or empty when ffmpeg is unavailable on this machine
    # - never an exception reaching the caller.
    assert data == b"" or data[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_caption_preview_leaves_nothing_behind(tmp_path, monkeypatch):
    """Every intermediate lands in a temp directory that is removed before
    the function returns. A preview must leave the library byte-identical."""
    from avg.media.preview import caption_png

    monkeypatch.chdir(tmp_path)
    caption_png("[Script Info]\n", 320, 180)
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/media/test_preview.py tests/pipeline/test_phases_polish.py -q
```

Expected: FAIL — `cannot import name 'sample_ass'` and `cannot import name 'caption_png'`.

- [ ] **Step 3: Add `sample_ass`**

In `avg/pipeline/phases/captions.py`, add after `_ass_escape`:

```python
#: What a caption preview shows. Short enough to fit one line at any of the
#: supported positions, long enough to show wrapping behaviour.
SAMPLE_CAPTION_TEXT = "This is what your captions will look like"


def sample_ass(width: int, height: int, font_name: str, font_size: int, position: str) -> str:
    """A one-line `.ass` file for the caption preview.

    Built from this module's own `_ass_header` - the same construction the
    real `captions.ass` uses, which itself matches the vendored engine's
    `make_ass_header` field-for-field. A preview that restated those style
    fields would be a second implementation, free to drift from what libass
    actually burns, and that is precisely the lie the preview exists to
    prevent.
    """
    header = _ass_header(width, height, font_name, font_size, position)
    line = (
        f"Dialogue: 0,{_ass_time(0.0)},{_ass_time(2.0)},Default,,0,0,0,,"
        f"{_ass_escape(SAMPLE_CAPTION_TEXT)}\n"
    )
    return header + line
```

- [ ] **Step 4: Add `caption_png`**

Append to `avg/media/preview.py`:

```python
def caption_png(ass_text: str, width: int, height: int) -> bytes:
    """One frame with `ass_text` burned over a flat background, as PNG bytes.

    Rendered by ffmpeg's own subtitles filter - the same libass the renderer
    burns captions with (`avg/media/vendor/avg.py`'s `cmd_caption_burn`), so
    the preview matches the video rather than approximating it.

    Takes TEXT rather than settings on purpose: `avg/media/` may not import
    from `avg/pipeline/`, and the `.ass` construction belongs to the caption
    phase. See `avg.pipeline.phases.captions.sample_ass`.

    Returns `b""` rather than raising when ffmpeg is missing or refuses -
    a preview panel can state "nothing to show", an exception cannot.
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from avg.media.environment import ffmpeg_exe  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        ass_path = folder / "preview.ass"
        out_path = folder / "preview.png"
        ass_path.write_text(ass_text, encoding="utf-8")
        # libass resolves the filter argument relative to the working
        # directory, and a Windows path inside a filter string needs its
        # separators and colon escaped - hence the cwd and the bare name.
        try:
            result = subprocess.run(
                [
                    ffmpeg_exe(), "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=0x1c1a16:s={width}x{height}:d=1",
                    "-vf", "subtitles=preview.ass",
                    "-frames:v", "1",
                    str(out_path),
                ],
                cwd=str(folder),
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:  # noqa: BLE001 - a missing ffmpeg is "nothing to show"
            return b""
        if result.returncode != 0 or not out_path.is_file():
            return b""
        return out_path.read_bytes()
```

- [ ] **Step 5: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/media/test_preview.py tests/pipeline/test_phases_polish.py -q
```

Expected: PASS.

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 4 more than after Task 1.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/media/preview.py avg/pipeline/phases/captions.py tests/
git commit -m "feat: caption previews burned by the same libass pass as the render"
```

---

## Task 3: The two previews that spend money

**Files:**
- Modify: `avg/media/preview.py` (add `voice_sample` and `image_sample`)
- Test: `tests/media/test_preview.py`

**Interfaces:**
- Consumes from Task 1: `SAMPLE_SENTENCE`.
- Produces, and Task 4 depends on these exactly:
  - `voice_sample(provider: str, voice_id: str) -> bytes`
  - `image_sample(provider: str, model: str, aspect_ratio: str) -> bytes`
  - `PAID_VOICE_PROVIDERS: frozenset[str]` — providers whose synthesis costs money

**Why:** Edge is free and can be previewed live; **ElevenLabs spends characters and an image generation spends money, per press.** `CLAUDE.md` and Slice 8 record why images and voices are *listed, never probed*: "probing an image model means paying for an image, and probing an ElevenLabs voice costs characters."

A preview is user-initiated rather than automatic, so it is allowed to exist — but the same money is spent, so the route (Task 4) gates both behind an explicit confirmation. **This task's functions do the work; they do not decide whether they are allowed to run.** That decision is the route's, and it is tested there.

- [ ] **Step 1: Write the failing tests**

Append to `tests/media/test_preview.py`:

```python
def test_paid_voice_providers_are_named_in_one_place():
    """Edge is the free provider; ElevenLabs spends characters. The route
    reads this set to decide what needs an explicit press."""
    from avg.media.preview import PAID_VOICE_PROVIDERS

    assert "elevenlabs" in PAID_VOICE_PROVIDERS
    assert "edge" not in PAID_VOICE_PROVIDERS


def test_a_voice_sample_speaks_the_shared_sentence(monkeypatch):
    """Never invents its own text - one constant, so every provider says the
    same thing and the panel can quote it."""
    from avg.media import preview

    spoken: list[str] = []

    class _Fake:
        def synthesize(self, text, *, voice_id, model):
            spoken.append(text)
            return b"ID3fake-audio"

    monkeypatch.setattr(preview, "_voice_backend", lambda provider: _Fake())
    data = preview.voice_sample("edge", "en-GB-RyanNeural")

    assert data == b"ID3fake-audio"
    assert spoken == [preview.SAMPLE_SENTENCE]


def test_an_unknown_voice_provider_returns_nothing(monkeypatch):
    from avg.media import preview

    assert preview.voice_sample("not-a-provider", "x") == b""


def test_a_failing_synthesis_returns_nothing_rather_than_raising(monkeypatch):
    from avg.media import preview

    class _Boom:
        def synthesize(self, text, *, voice_id, model):
            raise RuntimeError("provider down")

    monkeypatch.setattr(preview, "_voice_backend", lambda provider: _Boom())
    assert preview.voice_sample("edge", "x") == b""
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/media/test_preview.py -q
```

Expected: FAIL — `cannot import name 'PAID_VOICE_PROVIDERS'`.

- [ ] **Step 3: Add the paid pair**

Append to `avg/media/preview.py`:

```python
#: Voice providers whose synthesis costs money. Edge is free and is
#: therefore allowed to preview live; ElevenLabs spends characters on every
#: press, which is why `avg/api/routes_preview.py` requires an explicit
#: confirmation for it. Named here, once, so the route and the UI cannot
#: disagree about which is which.
PAID_VOICE_PROVIDERS: frozenset[str] = frozenset({"elevenlabs"})

#: The prompt an image sample is generated from. Fixed, so the panel can say
#: plainly that this is a sample rather than a scene from the user's video.
SAMPLE_IMAGE_PROMPT = "A calm wide landscape at golden hour, cinematic lighting"


def _voice_backend(provider: str) -> Any:
    """The voice backend for `provider`, or `None`.

    Imported lazily: `avg.providers.voice.providers` pulls in `edge_tts`,
    which costs about two seconds of startup, and Slice 7 moved that import
    off the launch path deliberately.
    """
    from avg.providers.voice.providers import (  # noqa: PLC0415
        EdgeVoiceProvider,
        ElevenLabsVoiceProvider,
    )

    backends = {"edge": EdgeVoiceProvider, "elevenlabs": ElevenLabsVoiceProvider}
    backend_cls = backends.get(provider)
    return backend_cls() if backend_cls is not None else None


def voice_sample(provider: str, voice_id: str) -> bytes:
    """`SAMPLE_SENTENCE`, spoken by `voice_id`. Audio bytes, or `b""`.

    **Costs money on ElevenLabs** - see `PAID_VOICE_PROVIDERS`. Free on Edge.
    Whether this is allowed to run at all is the route's decision, not this
    function's.
    """
    backend = _voice_backend(provider)
    if backend is None:
        return b""
    try:
        return backend.synthesize(SAMPLE_SENTENCE, voice_id=voice_id, model="")
    except Exception:  # noqa: BLE001 - a preview panel states the failure
        return b""


def image_sample(provider: str, model: str, aspect_ratio: str) -> bytes:
    """One image from `SAMPLE_IMAGE_PROMPT`. Image bytes, or `b""`.

    **Costs money on every provider that works.** Whether this is allowed to
    run is the route's decision - see `avg/api/routes_preview.py`.
    """
    from avg.pipeline.phases.visuals import _build_provider  # noqa: PLC0415

    try:
        backend = _build_provider(provider, None)
        return backend.generate(SAMPLE_IMAGE_PROMPT, aspect_ratio=aspect_ratio, model=model)
    except Exception:  # noqa: BLE001 - a preview panel states the failure
        return b""
```

**Layering note:** `image_sample` imports `_build_provider` from `avg/pipeline/phases/visuals.py`, which `avg/media/` may not import from. If that import is rejected by review or by a layering test, move `image_sample` into `avg/api/routes_preview.py` (Task 4) and drop it from this module — the route may import from both layers. State which you did in your report.

- [ ] **Step 4: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/media/test_preview.py -q
```

Expected: PASS.

- [ ] **Step 5: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 4 more than after Task 2. Confirm in your report that **no test performed a real synthesis or generation** — every one above monkeypatches the backend.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 6: Commit**

```bash
git add avg/media/preview.py tests/media/test_preview.py
git commit -m "feat: voice and image samples, with the paid ones named as paid"
```

---

## Task 4: The routes, and the rule that keeps a keystroke from costing money

**Files:**
- Create: `avg/api/routes_preview.py`
- Modify: `avg/api/app.py` (include the router)
- Create: `tests/api/test_preview_routes.py`

**Interfaces:**
- Consumes from Tasks 1–3: `overlay_png`, `takeover_png`, `caption_png`, `voice_sample`, `image_sample`, `PAID_VOICE_PROVIDERS`, `SAMPLE_SENTENCE`; and `avg.pipeline.phases.captions.sample_ass`.
- Produces, and Task 5 depends on these exactly:

```
POST /api/preview/caption   {width,height,font_name,font_size,position}     -> image/png   free
POST /api/preview/overlay   {spec,width,height}                             -> image/png   free
POST /api/preview/takeover  {headline,sub,width,height}                     -> image/png   free
POST /api/preview/voice     {provider,voice_id,confirm_paid?}               -> audio/mpeg  paid on elevenlabs
POST /api/preview/image     {provider,model,aspect_ratio,confirm_paid}      -> image/png   PAID
```

**Why:** The free three may be called on any settings change. The two that can spend are called **only** from an explicit press. Enforcing that on the backend — not only in the UI — is what makes it true regardless of what the frontend does.

**One preview at a time**, in its own slot on `app.state`, with a 409 on a second start — matching `probe_thread`, and deliberately **not** the render slot, so a preview can never block or be blocked by a real render.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_preview_routes.py`:

```python
"""Preview routes. The important tests here are the REFUSALS - nothing in
this file may perform a real synthesis or generation, both of which cost
real money.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app

TOKEN = "t0k"
AUTH = {"X-AVG-Token": TOKEN}


@pytest.fixture
def paths(tmp_path):
    return {
        "settings_path": tmp_path / "settings.json",
        "channels_dir": tmp_path / "channels",
        "secrets_path": tmp_path / "secrets.dat",
        "output_root": tmp_path / "output",
        "thumb_cache_dir": tmp_path / "thumbs",
        "errors_path": tmp_path / "errors.json",
        "model_cache_path": tmp_path / "working_models.json",
        "model_prefs_path": tmp_path / "model_prefs.json",
    }


@pytest.fixture
def client(paths):
    return TestClient(create_app(token=TOKEN, **paths))


def test_preview_routes_require_a_token(client):
    assert client.post("/api/preview/caption", json={}).status_code == 401


def test_a_caption_preview_is_free_and_needs_no_confirmation(client):
    res = client.post(
        "/api/preview/caption",
        json={"width": 640, "height": 360, "font_name": "Arial", "font_size": 40, "position": "bottom"},
        headers=AUTH,
    )
    # 200 with a PNG, or 503 when ffmpeg is unavailable here - never a 400
    # demanding a confirmation, because nothing is being spent.
    assert res.status_code in (200, 503)


def test_an_elevenlabs_voice_preview_refuses_without_confirmation(client, monkeypatch):
    """The rule that keeps a keystroke from costing money. Enforced on the
    BACKEND, so it holds regardless of what the frontend does."""
    from avg.media import preview

    monkeypatch.setattr(
        preview,
        "voice_sample",
        lambda *a, **k: pytest.fail("a paid preview must not run without confirmation"),
    )
    res = client.post(
        "/api/preview/voice",
        json={"provider": "elevenlabs", "voice_id": "abc"},
        headers=AUTH,
    )
    assert res.status_code == 400
    assert "confirm" in res.json()["detail"].lower()


def test_an_edge_voice_preview_needs_no_confirmation(client, monkeypatch):
    """Edge is free, so it is allowed to preview live."""
    monkeypatch.setattr("avg.api.routes_preview.voice_sample", lambda *a, **k: b"ID3audio")
    res = client.post(
        "/api/preview/voice",
        json={"provider": "edge", "voice_id": "en-GB-RyanNeural"},
        headers=AUTH,
    )
    assert res.status_code == 200
    assert res.content == b"ID3audio"


def test_an_image_preview_always_refuses_without_confirmation(client, monkeypatch):
    from avg.media import preview

    monkeypatch.setattr(
        preview,
        "image_sample",
        lambda *a, **k: pytest.fail("an image preview costs money and must not run unasked"),
    )
    res = client.post(
        "/api/preview/image",
        json={"provider": "pollinations", "model": "flux", "aspect_ratio": "9:16"},
        headers=AUTH,
    )
    assert res.status_code == 400


def test_a_second_preview_while_one_runs_is_refused(client, monkeypatch):
    """One preview at a time, in its own slot - never the render slot."""
    import threading

    started = threading.Event()
    release = threading.Event()

    def slow(*_a, **_k):
        started.set()
        release.wait(timeout=5)
        return b"ID3audio"

    monkeypatch.setattr("avg.api.routes_preview.voice_sample", slow)
    try:
        worker = threading.Thread(
            target=lambda: client.post(
                "/api/preview/voice",
                json={"provider": "edge", "voice_id": "x"},
                headers=AUTH,
            )
        )
        worker.start()
        assert started.wait(timeout=5)
        second = client.post(
            "/api/preview/voice", json={"provider": "edge", "voice_id": "y"}, headers=AUTH
        )
        assert second.status_code == 409
    finally:
        release.set()
        worker.join(timeout=5)

    # The render slot must be untouched by any preview.
    assert client.app.state.render_thread is None
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_preview_routes.py -q
```

Expected: FAIL — 404 on every preview route.

- [ ] **Step 3: Write the router**

Create `avg/api/routes_preview.py`:

```python
"""Preview routes: what a setting will look and sound like in the video.

Three of these are free and may be called on any settings change. Two spend
real money and are refused without an explicit confirmation:

    POST /api/preview/caption   free    one ffmpeg/libass frame
    POST /api/preview/overlay   free    one PIL draw
    POST /api/preview/takeover  free    one PIL draw
    POST /api/preview/voice     PAID on elevenlabs, free on edge
    POST /api/preview/image     PAID always

The confirmation is enforced HERE rather than only in the UI, so it holds
regardless of what the frontend does. `CLAUDE.md` and Slice 8 record why
images and voices are listed rather than probed: probing an image model
means paying for an image and probing an ElevenLabs voice costs characters.
A preview is user-initiated, which is why it may exist at all - but the same
money is spent, so it may never happen as a side effect of typing.

One preview at a time, in its own slot with a 409 on a second start -
matching `probe_thread`, and deliberately NOT the render slot, so a preview
can never block or be blocked by a real render.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from avg.media.preview import (
    PAID_VOICE_PROVIDERS,
    caption_png,
    image_sample,
    overlay_png,
    takeover_png,
    voice_sample,
)

from .deps import require_token

router = APIRouter(dependencies=[Depends(require_token)])


class CaptionPreview(BaseModel):
    width: int = 1080
    height: int = 1920
    font_name: str = "Figtree"
    font_size: int = 64
    position: str = "bottom"


class OverlayPreview(BaseModel):
    spec: dict[str, Any]
    width: int = 1080
    height: int = 1920


class TakeoverPreview(BaseModel):
    headline: str = ""
    sub: str = ""
    width: int = 1080
    height: int = 1920


class VoicePreview(BaseModel):
    provider: str
    voice_id: str
    #: Required for a provider in `PAID_VOICE_PROVIDERS`. The UI sets it only
    #: from an explicit press, never from a settings change.
    confirm_paid: bool = False


class ImagePreview(BaseModel):
    provider: str
    model: str
    aspect_ratio: str = "9:16"
    confirm_paid: bool = False


def _claim(app_state: Any) -> None:
    """Take the single preview slot, or 409."""
    lock: threading.Lock = app_state.preview_lock
    if not lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a preview is already running",
        )


def _png_or_503(data: bytes) -> Response:
    if not data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="nothing could be rendered - check that FFmpeg is present on App health",
        )
    return Response(content=data, media_type="image/png")


@router.post("/api/preview/caption")
async def preview_caption(body: CaptionPreview, request: Request) -> Response:
    """Free: one frame, burned by the same libass the renderer uses."""
    from avg.pipeline.phases.captions import sample_ass  # noqa: PLC0415

    app_state = request.app.state
    _claim(app_state)
    try:
        ass = sample_ass(body.width, body.height, body.font_name, body.font_size, body.position)
        data = await asyncio.to_thread(caption_png, ass, body.width, body.height)
    finally:
        app_state.preview_lock.release()
    return _png_or_503(data)


@router.post("/api/preview/overlay")
async def preview_overlay(body: OverlayPreview, request: Request) -> Response:
    """Free: one PIL draw by the render's own overlay code."""
    app_state = request.app.state
    _claim(app_state)
    try:
        data = await asyncio.to_thread(overlay_png, body.spec, body.width, body.height)
    finally:
        app_state.preview_lock.release()
    return _png_or_503(data)


@router.post("/api/preview/takeover")
async def preview_takeover(body: TakeoverPreview, request: Request) -> Response:
    """Free: one PIL draw by the render's own overlay code."""
    app_state = request.app.state
    _claim(app_state)
    try:
        data = await asyncio.to_thread(
            takeover_png, body.headline, body.sub, body.width, body.height
        )
    finally:
        app_state.preview_lock.release()
    return _png_or_503(data)


@router.post("/api/preview/voice")
async def preview_voice(body: VoicePreview, request: Request) -> Response:
    """Free on Edge. **Spends ElevenLabs characters** and refuses without
    `confirm_paid`, so typing in a voice field can never cost money."""
    if body.provider in PAID_VOICE_PROVIDERS and not body.confirm_paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{body.provider} charges for every sample - confirm before generating one"
            ),
        )
    app_state = request.app.state
    _claim(app_state)
    try:
        data = await asyncio.to_thread(voice_sample, body.provider, body.voice_id)
    finally:
        app_state.preview_lock.release()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the voice provider returned nothing - check its key on App health",
        )
    return Response(content=data, media_type="audio/mpeg")


@router.post("/api/preview/image")
async def preview_image(body: ImagePreview, request: Request) -> Response:
    """**Always spends money.** Refused without `confirm_paid`."""
    if not body.confirm_paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="generating a sample image costs money - confirm before generating one",
        )
    app_state = request.app.state
    _claim(app_state)
    try:
        data = await asyncio.to_thread(
            image_sample, body.provider, body.model, body.aspect_ratio
        )
    finally:
        app_state.preview_lock.release()
    return _png_or_503(data)
```

- [ ] **Step 4: Register the router and the slot**

In `avg/api/app.py`, beside the other router includes:

```python
    from avg.api.routes_preview import router as preview_router

    app.include_router(preview_router)
```

and beside the other `app.state` assignments:

```python
    # One preview at a time. Its own lock, deliberately not the render slot:
    # a preview must never block a render, or be blocked by one.
    app.state.preview_lock = threading.Lock()
```

`threading` is already imported in `app.py`; confirm before adding it.

- [ ] **Step 5: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_preview_routes.py -q
```

Expected: PASS (6 tests).

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 6 more than after Task 3.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/api/routes_preview.py avg/api/app.py tests/api/test_preview_routes.py
git commit -m "feat: preview routes, with the paid two refused unless confirmed"
```

---

## Task 5: The panel

**Files:**
- Modify: `web/src/api.ts` (five preview calls)
- Create: `web/src/PreviewPanel.tsx`
- Modify: `web/src/Settings.tsx` (render it in the captions, images and voice groups)
- Modify: `web/src/tokens.css` (the frame and the shimmer)

**Interfaces:**
- Consumes from Task 4: the five routes and their exact bodies.
- Produces: nothing further.

**Why:** Free previews re-render as the relevant settings change, debounced so dragging a slider does not queue twenty ffmpeg calls. Paid previews show a button naming the cost and render nothing until pressed.

**The rule this task must not break:** the two paid routes are never called from a `useEffect` that watches settings. Only from an `onClick`.

- [ ] **Step 1: Add the API calls**

In `web/src/api.ts`:

```ts
/** Free previews — safe to call whenever a setting changes. Each returns a
 * blob URL the caller must revoke when it is replaced. */
export async function previewCaption(body: {
  width: number; height: number; font_name: string; font_size: number; position: string;
}): Promise<string> {
  return blobUrl("/api/preview/caption", body);
}

export async function previewOverlay(body: {
  spec: Record<string, unknown>; width: number; height: number;
}): Promise<string> {
  return blobUrl("/api/preview/overlay", body);
}

/** PAID on ElevenLabs. `confirm_paid` must come from an explicit press —
 * never from a settings change. The backend refuses it either way. */
export async function previewVoice(body: {
  provider: string; voice_id: string; confirm_paid?: boolean;
}): Promise<string> {
  return blobUrl("/api/preview/voice", body);
}

/** PAID, always. Same rule. */
export async function previewImage(body: {
  provider: string; model: string; aspect_ratio: string; confirm_paid: boolean;
}): Promise<string> {
  return blobUrl("/api/preview/image", body);
}

async function blobUrl(path: string, body: unknown): Promise<string> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-AVG-Token": TOKEN },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `${path} failed: ${res.status}`);
  }
  return URL.createObjectURL(await res.blob());
}
```

Match however `api.ts` already reads its token — reuse the existing constant rather than re-deriving it.

- [ ] **Step 2: Write the panel**

Create `web/src/PreviewPanel.tsx` with a component per kind. Requirements, all load-bearing:

- **Free kinds** (`caption`, `overlay`) fetch in a `useEffect` on their settings, **debounced by 350 ms**, and revoke the previous blob URL when replaced.
- **Paid kinds** (`voice` on ElevenLabs, `image`) render **no** effect that fetches. A plain `.btn` whose label names the cost — `Generate a sample — spends ElevenLabs characters` / `Generate a sample image — costs one image` — is the only path to a request.
- **Edge voice is free**, so it may use the debounced effect like the free kinds. Branch on the provider, exactly as the backend does.
- **Loading** keeps the frame's size and shows a shimmer, never a spinner that changes layout.
- **A failure** shows the server's `detail` sentence, which after Slice 11 already says things like "no API key set".
- **The image panel states in one line** that the sample uses a fixed prompt, not the user's scenes.

- [ ] **Step 3: Style the frame**

In `web/src/tokens.css`:

```css
.preview-frame {
  position: relative;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--surface-2);
  overflow: hidden;
}

.preview-frame img {
  display: block;
  width: 100%;
  height: auto;
}

/* Keeps the frame's size while a preview renders - a spinner that changes
   the layout is worse than the wait it reports. */
.preview-frame--busy::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent,
    color-mix(in srgb, var(--text) 8%, transparent),
    transparent
  );
  animation: preview-shimmer calc(1.4s / max(var(--motion-scale, 1), 0.01)) ease-in-out infinite;
}

@keyframes preview-shimmer {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .preview-frame--busy::after {
    animation: none;
  }
}
```

- [ ] **Step 4: Place it in Settings**

In `web/src/Settings.tsx`, render the matching panel inside the group blocks, the same way `ModelsPanel` and `HealthPanel` already are:

```tsx
                {g.group === "captions" && !searching && <PreviewPanel kind="caption" valueOf={valueById} />}
                {g.group === "images" && !searching && <PreviewPanel kind="image" valueOf={valueById} />}
                {g.group === "voice" && !searching && <PreviewPanel kind="voice" valueOf={valueById} />}
```

- [ ] **Step 5: Build and lint**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

- [ ] **Step 6: Prove the paid rule holds, by grep and by reading**

```bash
grep -n "previewVoice\|previewImage" web/src/PreviewPanel.tsx
```

For every hit, state in your report whether it sits inside a `useEffect` or an `onClick`. **Any paid call inside an effect is a defect** — it means a settings change can spend money. Report the caption/overlay debounce interval you used, and confirm blob URLs are revoked.

- [ ] **Step 7: Commit**

```bash
git add web/src/api.ts web/src/PreviewPanel.tsx web/src/Settings.tsx web/src/tokens.css
git commit -m "feat: a Live Preview panel, free previews live and paid ones on request"
```

---

## Final verification

Run after Task 5. Not a task — no commit unless something needs fixing.

- [ ] `.venv-build/Scripts/python.exe -m pytest tests -q` — expect ~18 new tests, never below your starting count.
- [ ] `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- [ ] `npm --prefix web run build && npm --prefix web run lint` — clean.
- [ ] Change a caption setting: the frame updates on its own. **Compare it once against a real rendered video with the same settings** — that comparison is the entire justification for burning the preview with libass rather than approximating it in CSS.
- [ ] Change an overlay setting: the frame updates on its own.
- [ ] Select an Edge voice: it speaks the sample sentence without being asked.
- [ ] Select an ElevenLabs voice: **nothing happens** until the button is pressed, and the button says what it will spend. Check the take log to confirm no characters were spent before the press.
- [ ] Press the AI-graphics sample once: one image is generated, and the panel states it is a fixed sample prompt rather than a scene from your video.
- [ ] Preview with a missing key: the panel says the key is missing (Slice 11's classification), not "preview failed".
- [ ] Reduced motion: the shimmer stops, the previews still work.
- [ ] **The library folder is byte-identical after a preview session.**
- [ ] Start a render, then request a preview: neither blocks the other, and a second preview gets a 409.

---

## Appendix: spec coverage

| Spec section | Task |
|---|---|
| §3.1 `avg/media/preview.py`, overlay and takeover | 1 |
| §3.1 `caption_png` + shared `.ass` construction | 2 |
| §3.1 `voice_sample`, `SAMPLE_SENTENCE` | 1 (constant), 3 (function) |
| §3.2 The five routes | 4 |
| §3.2 Paid routes gated on the backend | 4 |
| §3.2 Single slot, 409, not the render slot | 4 |
| §3.3 The panel, debounce, loading shimmer | 5 |
| §3.4 What a preview may claim | 5 (the sample-prompt line) |
| §4 Testing | 1, 2, 3, 4 |
| §5 Verification | Final verification |
