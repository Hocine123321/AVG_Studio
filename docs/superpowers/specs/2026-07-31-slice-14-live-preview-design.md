# Slice 14 — Live Preview

**Goal:** show what a setting will actually look and sound like in the
finished video, using the code that will produce it — so choosing a caption
style, an overlay, a voice or an image model stops being a guess that costs a
whole run to check.

**Date:** 2026-07-31. Every entry point named below was read from the source
on that date.

Fourth slice of the 2026-07-31 backlog. It depends on Slice 12 for the
Settings surface it hangs off.

---

## 1. What the evidence shows

### 1.1 The renderer's own drawing code is reachable

`avg/media/overlays.py:151` draws one layer by calling

```python
image = od.render_overlay(spec, width, height, brand)   # PIL Image
image.save(png)
```

where `od` is the vendored `avg/media/vendor/overlay_design.py:518`
`render_overlay(spec, W, H, brand, progress=1.0)`. It takes a spec dict and
returns a PIL image, in-process, with no ffmpeg and no video.

So overlays and takeover cards can be previewed **by the exact function that
draws them in the real render**, at the cost of a PIL call.

### 1.2 Captions are libass, so an exact preview needs one ffmpeg frame

`avg/pipeline/phases/captions.py` writes `render/captions.ass` — "the exact
`.ass` shape the vendored engine expects" — and the engine burns it with
ffmpeg/libass (`avg/media/vendor/avg.py:3329`, `cmd_caption_burn`).

Caption appearance is therefore decided by libass, not by any Python this app
controls. A DOM or CSS approximation would be a **second implementation of
caption styling**, free to drift from what the renderer actually produces —
and a preview that lies is worse than no preview. An exact one is a single
ffmpeg invocation rendering one frame with the subtitle filter over a still.

### 1.3 Two of the four previews spend money

| Preview | Mechanism | Cost |
|---|---|---|
| Overlays, takeover cards | `render_overlay` → PIL | none |
| Captions | one-frame ffmpeg + libass | none |
| Voice — Edge | `EdgeVoiceProvider` synthesis | none; Edge is the free provider |
| Voice — ElevenLabs | synthesis | **characters, per press** |
| AI graphics | a real image generation | **money, per press** |

`CLAUDE.md` and Slice 8's design both record why images and voices are
*listed, never probed*: "probing an image model means paying for an image,
and probing an ElevenLabs voice costs characters". A preview is not a probe —
it is user-initiated — but the same money is spent, so the same care applies.

---

## 2. Decisions

1. **Free previews are live; paid previews need an explicit press** that
   states what it will spend. Chosen over all-manual and over all-live.
2. **Previews use the real rendering path**, never a lookalike. §1.2 is why.

---

## 3. Architecture

One new backend module, one new route group, one frontend panel. Nothing
here changes the phase registry, the settings catalogue's role, or what a
phase writes. **No preview ever writes into a project folder** — every
artifact lands in a temp directory and is served once.

### 3.1 `avg/media/preview.py` — the one place previews are produced

A new module in the media layer, beside `overlays.py` and `thumbnails.py`,
exposing four functions that each return bytes and never raise:

| Function | Returns | How |
|---|---|---|
| `overlay_png(spec, width, height, brand)` | PNG | `overlay_design.render_overlay`, §1.1 |
| `takeover_png(headline, sub, width, height, brand)` | PNG | the same, with the `chapters_takeover_cards` element |
| `caption_png(ass_text, width, height)` | PNG | one ffmpeg frame, subtitle filter over a flat background |
| `voice_sample(provider, voice_id)` | MP3 | the existing voice provider's synthesis |

`caption_png` builds its `.ass` through **the same header/style construction
`captions.py` already owns**, not a second copy — the module's docstring
records that its field values match the vendored engine's own
`make_ass_header`, and duplicating them is how a preview starts lying.

The sample sentence is fixed, in one constant:

> "Hello, this is my voice I'll be using in a new video! Do you like it?"

### 3.2 The routes, and which of them can spend

```
POST /api/preview/overlay    -> image/png      free
POST /api/preview/takeover   -> image/png      free
POST /api/preview/caption    -> image/png      free
POST /api/preview/voice      -> audio/mpeg     free on edge, PAID on elevenlabs
POST /api/preview/image      -> image/png      PAID
```

The three free routes may be called on any settings change. The two that can
spend are called **only** from an explicit press, and the frontend never
issues them from a `useEffect` that watches settings — a rule stated here
because it is the one that keeps a keystroke from costing money.

`POST /api/preview/voice` against `edge` is free and is therefore allowed to
be live; against `elevenlabs` it requires the explicit press like any other
paid preview. The route knows which provider it was handed, so the
distinction is enforced on the backend, not only in the UI.

Every preview runs on a worker thread (`asyncio.to_thread`) — ffmpeg and
synthesis are both too slow for the event loop, the same reason
`refresh_live_list` already uses one.

**One preview at a time**, in its own single slot on `app.state`, with a 409
on a second start — matching `probe_thread` and deliberately not the render
slot, so a preview can never block or be blocked by a real render.

### 3.3 The panel

Live Preview appears in Settings, beside the settings it previews: the
captions group shows a caption frame, the images group an AI-graphics frame,
the voice group a player.

- **Free previews** re-render as the relevant settings change, debounced, so
  dragging a slider does not queue twenty ffmpeg calls.
- **Paid previews** show the last result (or an empty frame) plus a button
  naming the cost: *"Generate a sample — spends ElevenLabs characters"*,
  *"Generate a sample image — costs one image"*. Nothing renders until it is
  pressed.
- **The loading state** is the subtle one the request asks for: the frame
  keeps its size and shows a slow shimmer rather than a spinner that changes
  the layout. It is scaled by `motion_scale` (Slice 12) and disabled under
  `prefers-reduced-motion`, like every other animation in the app.
- A failed preview states the reason, using Slice 11's classified errors — a
  voice preview that 401s says the key is missing, not "preview failed".

### 3.4 What a preview is allowed to claim

A caption or overlay preview is produced by the render's own code and is
therefore exact. A voice sample is the real voice. An AI-graphics sample is a
real generation from the configured model — but of a fixed sample prompt,
not of the user's scenes, so the panel says so in one line rather than
letting the user infer that their video's images will look like this one.

---

## 4. Testing

No test may make a live provider call: `tests/conftest.py` forces
`AVG_REPLAY_FIXTURES=1`, and an image or ElevenLabs preview costs real money.

- **The free previews** produce a non-empty PNG for a representative spec,
  and return an honest failure rather than raising when ffmpeg is missing.
- **`caption_png` uses the shared `.ass` construction** — asserted by
  building a preview and a real caption file from the same settings and
  comparing the style header, so a second copy of the styling cannot appear
  without a test failing. This is the §1.2 guard.
- **The paid routes refuse to run automatically**: the voice route rejects
  `elevenlabs` unless the request carries the explicit-confirmation flag, and
  a test asserts a plain request does not synthesise.
- **The single slot**: a second preview while one runs gets a 409, and the
  render slot is untouched by any preview.
- **Nothing lands in a project folder** — a preview run leaves the library
  byte-identical.

Target: **769 tests still passing**, mypy no worse than its `Found 688
errors in 22 files` baseline.

---

## 5. Verification

- `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 769.
- `npm --prefix web run build && npm --prefix web run lint` — clean.
- Change a caption setting: the caption frame updates on its own, and the
  frame matches what a real render produces for the same settings — checked
  once against an actual rendered video, because that comparison is the
  entire justification for §1.2.
- Change an overlay setting: the overlay frame updates on its own.
- Select an Edge voice: it speaks the sample sentence without being asked.
- Select an ElevenLabs voice: **nothing happens** until the button is
  pressed, and the button says what it will spend. Confirm against the take
  log that no characters were spent before the press.
- Press the AI-graphics sample once: one image is generated, and the panel
  states that it is a sample prompt rather than a scene from the video.
- Preview with a provider whose key is missing: the panel says the key is
  missing (Slice 11's classification), not "preview failed".
- Reduced motion: the shimmer is gone, the previews still work.
- The library folder is byte-identical after a preview session.

---

## 6. Out of scope

- Previewing a whole scene, or motion — this previews stills and a voice
  sample. A moving preview is the device-frame player, still open from
  Slice 6C.
- The Smart model picker (Slice 15), which will hang off the same panel.
- Parallel pipelines (Slice 16).
- Caching previews between sessions. A preview is cheap enough to redo, and
  a stale cached one is exactly the lie §1.2 exists to prevent.
