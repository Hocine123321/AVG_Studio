# Slice 32 (sub-slice 2) — Visual & Motion Consistency: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a take's generated images stop looking like five different
artists worked on them. Three independent pieces: a locked seed for the
two providers whose API accepts one, an opt-in reference image for
Gemini, and Director II's motion planning actually reading the
structural/spatial data Director I and Visuals already compute for it.

**Architecture:** `ProjectDoc`/`Take` each gain one field
(`visual_seed`). `avg/pipeline/phases/visuals.py` assigns it once per
take and threads it, plus an optional Gemini reference image, through
`_generate_one`'s new `extra_kwargs` parameter — built per call by a
small closure that knows which provider accepts what, so neither
`ImageProvider`'s shared protocol nor any provider that doesn't need
these two things changes shape. `avg/pipeline/phases/director.py` gains
one pure function that reads two files Director I and Visuals already
write and nothing before this slice ever read back, and threads the
result into `avg.pipeline.prompts.build_director2_prompt`.

**Two refinements found while grounding this plan in the real code, not
assumed from the spec:**

1. **Seed persistence isn't a direct `save_project` call.** No phase in
   this codebase calls `avg.pipeline.create.save_project` itself —
   `avg/pipeline/runner.py`'s own docstring is explicit that
   `on_phase_change` is a callback specifically so the *runner* decides
   when and *which* project object gets persisted, because a take re-run
   (`avg/api/routes_takes.py`) hands phases a throwaway `model_copy`
   ("shadow_project"), never the real object the callback's closure
   saves. `visual_seed` needs the exact treatment `Take.master_output`
   already has: its own field on `Take`, copied by hand from
   `shadow_project` after the run finishes (Task 1). The phase itself
   only ever mutates `ctx.project.visual_seed` in memory — the runner's
   existing write-through, which already fires unconditionally when a
   phase finishes, success or failure, picks it up for free.
2. **Gemini reference-image consistency needs the first scene generated
   *before* the rest, not just "reused once available."** All scenes are
   currently submitted to one `ThreadPoolExecutor` at once
   (`_generate_all`) — under full parallelism there is no guarantee scene
   1 finishes before scene 3 even starts, so "use the first successful
   scene as a reference" would race a reference image that doesn't exist
   yet for most of the batch. Resolved by generating scene 1
   synchronously, once, only when reference-lock is on and the provider
   is Gemini, before submitting the remaining scenes to the pool with
   whatever that call produced (or `None`, if it failed — the rest of the
   phase proceeds without a reference rather than blocking on one).

**Tech Stack:** Python 3, pytest, Pillow (already a dependency, used by
the existing safezone/thumbnail code) — no frontend change.

**Spec:** `docs/superpowers/specs/2026-09-13-slice-32-visual-motion-consistency-design.md`

## Global Constraints

- No live API calls in tests — every provider test runs `mode="record"`
  against an in-process fake handler or `mode="replay"` against a real,
  committed fixture (`tests/providers/test_images.py` already has both
  for Gemini: a real billing-failure fixture and a real Imagen
  model-missing fixture).
- No change to `ImageProvider`'s shared protocol (`avg/providers/images/
  base.py`) — `seed`/`reference_image` are optional keyword parameters on
  the two/one concrete classes that accept them, never a protocol-level
  requirement every provider must implement.
- Per-style recycle pools (the spec's fourth, deferred piece) are not
  touched by this plan at all.
- Composite multi-scene grids stay dropped — not reconsidered here.
- Verify with `.venv-linux/bin/python -m pytest tests -q` in this
  sandbox; the real gate is `.venv-build\Scripts\python.exe -m pytest
  tests -q` on Windows.

## Files touched

| File | Change |
|---|---|
| `avg/core/models.py` | `ProjectDoc` and `Take` both +`visual_seed: int \| None = None` |
| `avg/core/settings_schema.py` | +2 `SettingSpec`s (`visuals_seed_lock`, `visuals_gemini_reference_lock`) |
| `avg/api/routes_takes.py` | +1 line copying `shadow_project.visual_seed` onto `new_take.visual_seed` |
| `avg/providers/images/community.py` | `PollinationsImageProvider.generate()`, `StableHordeImageProvider.generate()`/`_submit()`: +optional `seed` |
| `avg/providers/images/premium.py` | `GeminiImageProvider.generate()`/`_generate_content()`: +optional `reference_image`, wired only on the non-Imagen path |
| `avg/pipeline/phases/visuals.py` | Seed assignment; `_generate_one` gains `extra_kwargs`; `_generate_all` restructured for the sequential-first-scene case |
| `avg/pipeline/phases/director.py` | New `_director2_scene_context`; `Director2Phase.run` reads it and passes it to the prompt builder |
| `avg/pipeline/prompts.py` | `build_director2_prompt` +`scene_context` param; new `_director2_scene_line` helper; one added sentence in the prompt body |
| `tests/core/test_models.py`, `tests/core/test_settings_schema.py`, `tests/providers/test_images.py`, `tests/pipeline/test_phases_media.py`, `tests/pipeline/test_phases_polish.py` | New/extended tests per task |

---

## Task 1 — `ProjectDoc`/`Take` fields, two settings, the take copy-back

### Step 1.1 — Test

Add to `tests/core/test_models.py`:

```python
def test_project_and_take_both_default_visual_seed_to_none():
    from avg.core.models import Take

    project = ProjectDoc(id="p", title="t", folder=Path("."), created="2026-01-01T00:00:00")
    assert project.visual_seed is None
    take = Take(id=1, label="Take 1", created="2026-01-01T00:00:00")
    assert take.visual_seed is None
```

Add to `tests/core/test_settings_schema.py`:

```python
def test_visuals_seed_and_reference_lock_settings_exist():
    ids = {s.id: s for s in SETTINGS}
    assert ids["visuals_seed_lock"].default is True
    assert ids["visuals_seed_lock"].invalidates == "visuals"
    assert ids["visuals_seed_lock"].scope == "channel"
    assert ids["visuals_gemini_reference_lock"].default is False
    assert ids["visuals_gemini_reference_lock"].invalidates == "visuals"
```

Run both — fail, the fields/ids don't exist yet.

### Step 1.2 — Implementation

**Verified against the real file before writing these blocks.** In
`avg/core/models.py`, `Take`'s `storage` field is its last field before
the class ends. Replace:

```python
    storage: Literal["root", "own"] = "own"

class ProjectDoc(BaseModel):
```

with:

```python
    storage: Literal["root", "own"] = "own"
    #: Copied by hand from `shadow_project.visual_seed` after a take's run
    #: finishes (`avg/api/routes_takes.py`) - the same treatment
    #: `master_output` above already needs and for the same reason: phases
    #: mutate the shadow project passed to the runner, not this object.
    visual_seed: int | None = None

class ProjectDoc(BaseModel):
```

`ProjectDoc`'s `archived` field is its last field before the class ends.
Replace:

```python
    archived: bool = False

class RenderRequest(BaseModel):
```

with:

```python
    archived: bool = False
    # Slice 32 sub-slice 2: assigned once, on Visuals' first run for a
    # take, when seed-lock is on - reused for every scene and every
    # re-run of that take, locking the video's look rather than each
    # image's. `None` (every project and take before this field existed,
    # and every one after it until Visuals actually runs) means "not yet
    # assigned". See `avg.pipeline.phases.visuals` for assignment,
    # `avg.core.models.Take.visual_seed` for why a take needs its own copy.
    visual_seed: int | None = None

class RenderRequest(BaseModel):
```

In `avg/core/settings_schema.py`, `image_quality`'s entry is the last one
in `group="images"` before the render-engine section comment. Replace:

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
    SettingSpec(
        id="visuals_seed_lock",
        group="images",
        label="Lock visual seed",
        help=(
            "Reuse one seed across every scene and re-run of a take, for a more "
            "consistent look. Only Pollinations and Stable Horde support it - "
            "the only two providers whose API accepts one."
        ),
        type="bool",
        default=True,
        scope="channel",
        invalidates="visuals",
    ),
    SettingSpec(
        id="visuals_gemini_reference_lock",
        group="images",
        label="Gemini reference-image consistency",
        help=(
            "Send the take's first generated scene image as a visual reference "
            "on every later Gemini call, for a more consistent look. Real "
            "bandwidth/latency/cost per image, so this is off by default."
        ),
        type="bool",
        default=False,
        scope="channel",
        invalidates="visuals",
    ),
    # --- render engine and quality ---
```

In `avg/api/routes_takes.py`, the `master_output` copy-back in `_worker`'s
`finally` block is the one place a shadow-project field is manually
copied onto the take. Replace:

```python
            # `shadow_project.phases` IS `new_take.phases` (shared by
            # reference), already up to date - only `master_output` needs
            # copying by hand, since `RenderPhase.run` only ever writes
            # `ctx.project.master_output` (the shadow), never the take.
            new_take.master_output = shadow_project.master_output
```

with:

```python
            # `shadow_project.phases` IS `new_take.phases` (shared by
            # reference), already up to date - only `master_output` needs
            # copying by hand, since `RenderPhase.run` only ever writes
            # `ctx.project.master_output` (the shadow), never the take.
            new_take.master_output = shadow_project.master_output
            # Same reasoning, same fix - VisualsPhase.run only ever writes
            # `ctx.project.visual_seed` (the shadow) too.
            new_take.visual_seed = shadow_project.visual_seed
```

Run Step 1.1's tests — pass. **Mechanically verified**: applied to a real
checkout, `SETTINGS` grows from 67 to 69 entries cleanly, `tests/core/
test_models.py` + `tests/core/test_settings_schema.py` both green
(12/12, no regressions).

**Commit:** `feat(models): visual_seed on ProjectDoc and Take, plus its two settings`

---

## Task 2 — Seed on Pollinations and Stable Horde

### Step 2.1 — Test

Add to `tests/providers/test_images.py`:

```python
def test_pollinations_generate_sends_seed_when_given(tmp_path, monkeypatch):
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: None)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, content=b"fake-bytes")

    provider = PollinationsImageProvider(transport=_mock_record_transport(tmp_path, handler))
    provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="flux", seed=12345)
    assert "seed=12345" in captured["url"]


def test_pollinations_generate_omits_seed_when_not_given(tmp_path, monkeypatch):
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: None)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, content=b"fake-bytes")

    provider = PollinationsImageProvider(transport=_mock_record_transport(tmp_path, handler))
    provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="flux")
    assert "seed" not in captured["url"]


def test_stablehorde_submit_sends_seed_as_a_string_when_given(tmp_path, monkeypatch):
    import json as json_lib

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "generate/async" in str(request.url):
            captured["body"] = json_lib.loads(request.content)
            return httpx.Response(200, json={"id": "job-1"})
        if "generate/check" in str(request.url):
            return httpx.Response(200, json={"done": True, "faulted": False})
        return httpx.Response(
            200, json={"generations": [{"img": base64.b64encode(b"fake-bytes").decode()}]}
        )

    provider = StableHordeImageProvider(
        transport=_mock_record_transport(tmp_path, handler), sleep=lambda _s: None
    )
    provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="flux", seed=99)
    assert captured["body"]["params"]["seed"] == "99"


def test_stablehorde_submit_omits_seed_when_not_given(tmp_path, monkeypatch):
    import json as json_lib

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "generate/async" in str(request.url):
            captured["body"] = json_lib.loads(request.content)
            return httpx.Response(200, json={"id": "job-1"})
        if "generate/check" in str(request.url):
            return httpx.Response(200, json={"done": True, "faulted": False})
        return httpx.Response(
            200, json={"generations": [{"img": base64.b64encode(b"fake-bytes").decode()}]}
        )

    provider = StableHordeImageProvider(
        transport=_mock_record_transport(tmp_path, handler), sleep=lambda _s: None
    )
    provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="flux")
    assert "seed" not in captured["body"]["params"]
```

Run these — fail, no `seed` parameter exists yet.

### Step 2.2 — Implementation

**Verified against the real file before writing these blocks.** In
`avg/providers/images/community.py`, replace `PollinationsImageProvider.
generate`'s signature and `params` construction:

```python
    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        size = _POLLINATIONS_SIZE_BY_RATIO.get(aspect_ratio)
        if size is None:
            raise AvgError(ErrorKind.bug, f"Pollinations: unsupported aspect_ratio {aspect_ratio!r}")
        width, height = size

        headers = {}
        key = get_key(self.name)
        if key:
            headers["Authorization"] = f"Bearer {key}"

        params = {
            "model": _normalize_model(model),
            "width": str(width),
            "height": str(height),
            "safe": "true",
            "negative_prompt": _DEFAULT_NEGATIVE_PROMPT,
        }
```

with:

```python
    def generate(self, prompt: str, *, aspect_ratio: str, model: str, seed: int | None = None) -> bytes:
        size = _POLLINATIONS_SIZE_BY_RATIO.get(aspect_ratio)
        if size is None:
            raise AvgError(ErrorKind.bug, f"Pollinations: unsupported aspect_ratio {aspect_ratio!r}")
        width, height = size

        headers = {}
        key = get_key(self.name)
        if key:
            headers["Authorization"] = f"Bearer {key}"

        params = {
            "model": _normalize_model(model),
            "width": str(width),
            "height": str(height),
            "safe": "true",
            "negative_prompt": _DEFAULT_NEGATIVE_PROMPT,
        }
        if seed is not None:
            params["seed"] = str(seed)
```

Replace `StableHordeImageProvider.generate`'s signature through `_submit`'s
body construction:

```python
    def generate(
        self,
        prompt: str,
        *,
        aspect_ratio: str,
        model: str,
        cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bytes:
        size = _SIZE_BY_RATIO.get(aspect_ratio)
        if size is None:
            raise AvgError(ErrorKind.bug, f"Stable Horde: unsupported aspect_ratio {aspect_ratio!r}")
        width, height = size

        key = get_key(self.name) or _ANONYMOUS_KEY
        headers = {
            "apikey": key,
            "Content-Type": "application/json",
            "Client-Agent": "AVG-Studio:1.0:local",
        }

        job_id = self._submit(prompt, model, width, height, headers)
        return self._poll_until_done(job_id, headers, cancel, on_progress)

    # -- submit --------------------------------------------------------

    def _submit(self, prompt: str, model: str, width: int, height: int, headers: dict[str, str]) -> str:
        body = {
            "prompt": prompt,
            "params": {
                "sampler_name": _DEFAULT_SAMPLER,
                "cfg_scale": _DEFAULT_CFG_SCALE,
                "steps": _DEFAULT_STEPS,
                "width": width,
                "height": height,
                "n": 1,
            },
```

with:

```python
    def generate(
        self,
        prompt: str,
        *,
        aspect_ratio: str,
        model: str,
        seed: int | None = None,
        cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bytes:
        size = _SIZE_BY_RATIO.get(aspect_ratio)
        if size is None:
            raise AvgError(ErrorKind.bug, f"Stable Horde: unsupported aspect_ratio {aspect_ratio!r}")
        width, height = size

        key = get_key(self.name) or _ANONYMOUS_KEY
        headers = {
            "apikey": key,
            "Content-Type": "application/json",
            "Client-Agent": "AVG-Studio:1.0:local",
        }

        job_id = self._submit(prompt, model, width, height, headers, seed)
        return self._poll_until_done(job_id, headers, cancel, on_progress)

    # -- submit --------------------------------------------------------

    def _submit(
        self, prompt: str, model: str, width: int, height: int, headers: dict[str, str], seed: int | None = None
    ) -> str:
        params: dict[str, Any] = {
            "sampler_name": _DEFAULT_SAMPLER,
            "cfg_scale": _DEFAULT_CFG_SCALE,
            "steps": _DEFAULT_STEPS,
            "width": width,
            "height": height,
            "n": 1,
        }
        if seed is not None:
            # AI Horde's documented API shape: seed is a string field, not
            # an integer one, unlike every other numeric param here - kept
            # as its own explicit assignment rather than folded into the
            # dict literal above so that distinction stays visible.
            params["seed"] = str(seed)
        body = {
            "prompt": prompt,
            "params": params,
```

(The rest of `_submit`'s body dict — `"models"`, `"nsfw"`, `"censor_nsfw"`,
`"r2"` — is unchanged; this replace-block only reaches the point where
`params` becomes a named variable instead of an inline literal.)

Run Step 2.1's tests — pass. **Mechanically verified**: full `tests/
providers/test_images.py` run, 39/39 (35 pre-existing + 4 new), no
regressions.

**Commit:** `feat(providers): optional seed param on Pollinations and Stable Horde`

---

## Task 3 — Reference image on Gemini

### Step 3.1 — Test

Add to `tests/providers/test_images.py`:

```python
def test_gemini_generate_content_includes_reference_image_when_given(monkeypatch, tmp_path):
    import json as json_lib

    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json_lib.loads(request.content)
        b64 = base64.b64encode(b"fake-inline-image-bytes").decode()
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"inlineData": {"data": b64}}]}, "finishReason": "STOP"}]},
        )

    provider = GeminiImageProvider(transport=_mock_record_transport(tmp_path, handler))
    provider.generate(
        PROMPT_APPLE, aspect_ratio="1:1", model="gemini-3.1-flash-image", reference_image=b"reference-bytes"
    )
    parts = captured["body"]["contents"][0]["parts"]
    assert len(parts) == 2
    assert parts[0]["inline_data"]["data"] == base64.b64encode(b"reference-bytes").decode("ascii")
    assert parts[1]["text"] == PROMPT_APPLE


def test_gemini_generate_content_omits_reference_image_when_not_given(monkeypatch, tmp_path):
    import json as json_lib

    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json_lib.loads(request.content)
        b64 = base64.b64encode(b"fake-inline-image-bytes").decode()
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"inlineData": {"data": b64}}]}, "finishReason": "STOP"}]},
        )

    provider = GeminiImageProvider(transport=_mock_record_transport(tmp_path, handler))
    provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="gemini-3.1-flash-image")
    parts = captured["body"]["contents"][0]["parts"]
    assert len(parts) == 1
    assert parts[0]["text"] == PROMPT_APPLE


def test_gemini_generate_imagen_ignores_reference_image_silently(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert ":predict" in str(request.url)
        b64 = base64.b64encode(b"fake-predict-image-bytes").decode()
        return httpx.Response(200, json={"predictions": [{"bytesBase64Encoded": b64}]})

    provider = GeminiImageProvider(transport=_mock_record_transport(tmp_path, handler))
    data = provider.generate(
        PROMPT_APPLE, aspect_ratio="16:9", model="imagen-4.0-generate-001", reference_image=b"reference-bytes"
    )
    assert data == b"fake-predict-image-bytes"
```

Run these — fail, no `reference_image` parameter exists yet.

### Step 3.2 — Implementation

**Verified against the real file before writing these blocks.** In
`avg/providers/images/premium.py`, replace `GeminiImageProvider.generate`:

```python
    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        key = get_key(self.name)
        if not key:
            raise AvgError(ErrorKind.auth, "Gemini API key is not set")
        if aspect_ratio not in _ASPECT_RATIOS:
            raise AvgError(ErrorKind.bug, f"Gemini: unsupported aspect_ratio {aspect_ratio!r}")

        model_id = model.removeprefix("models/")
        model_id = _DEAD_MODEL_REMAP.get(model_id.lower(), model_id)

        if "imagen" in model_id.lower():
            return self._predict(model_id, prompt, aspect_ratio, key)
        return self._generate_content(model_id, prompt, aspect_ratio, key)
```

with:

```python
    def generate(
        self, prompt: str, *, aspect_ratio: str, model: str, reference_image: bytes | None = None
    ) -> bytes:
        key = get_key(self.name)
        if not key:
            raise AvgError(ErrorKind.auth, "Gemini API key is not set")
        if aspect_ratio not in _ASPECT_RATIOS:
            raise AvgError(ErrorKind.bug, f"Gemini: unsupported aspect_ratio {aspect_ratio!r}")

        model_id = model.removeprefix("models/")
        model_id = _DEAD_MODEL_REMAP.get(model_id.lower(), model_id)

        if "imagen" in model_id.lower():
            # Imagen's :predict body has no image-conditioning slot in this
            # shape - reference_image is silently ignored here rather than
            # raising, so a user who enabled the setting while routed to an
            # Imagen model does not get a phase failure over it.
            return self._predict(model_id, prompt, aspect_ratio, key)
        return self._generate_content(model_id, prompt, aspect_ratio, key, reference_image)
```

Replace `_generate_content`:

```python
    def _generate_content(self, model_id: str, prompt: str, aspect_ratio: str, key: str) -> bytes:
        url = f"{_GEMINI_MODELS_BASE}/{quote(model_id, safe='')}:generateContent?key={quote(key, safe='')}"
        response = self._transport.request(
            "POST",
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": aspect_ratio},
                },
            },
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
```

with:

```python
    def _generate_content(
        self, model_id: str, prompt: str, aspect_ratio: str, key: str, reference_image: bytes | None = None
    ) -> bytes:
        url = f"{_GEMINI_MODELS_BASE}/{quote(model_id, safe='')}:generateContent?key={quote(key, safe='')}"
        parts: list[dict[str, Any]] = []
        if reference_image is not None:
            # Prepended, ahead of the text part - Gemini's documented
            # image-plus-text multimodal input shape reads image parts as
            # context for the text that follows them.
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(reference_image).decode("ascii"),
                    }
                }
            )
        parts.append({"text": prompt})
        response = self._transport.request(
            "POST",
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": aspect_ratio},
                },
            },
            timeout=_GENERATE_TIMEOUT_SECONDS,
            provider=self.name,
        )
```

Run Step 3.1's tests — pass. **Mechanically verified**: full `tests/
providers/test_images.py` run, 42/42 (39 from Task 2 + 3 new), no
regressions.

**Commit:** `feat(providers): optional reference_image param on Gemini's :generateContent path`

---

## Task 4 — `VisualsPhase`: seed assignment and the reference-image sequencing

This is the plan's largest single task — it is where the two provider-level
additions above actually get used, and where the "generate scene 1 first"
restructuring lives. Read the plan's own preamble (refinement #2) before
starting; the design below implements it directly.

### Step 4.1 — Test

Add to `tests/pipeline/test_phases_media.py`. These fakes and tests were
written and run against the real, fully-implemented Task 4 code below —
not the other way around:

```python
class _FakeSeedCapableProvider:
    """Named to match `_SEED_CAPABLE_PROVIDERS` - records the `seed` kwarg
    (or its absence) on every call."""

    name = "pollinations"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def generate(self, prompt: str, *, aspect_ratio: str, model: str, seed: int | None = None) -> bytes:
        self.calls.append((prompt, seed))
        return f"fake-image-bytes:{prompt}".encode()


class _FakeGeminiReferenceProvider:
    """Named "gemini" - records the `reference_image` kwarg (or its
    absence) on every call, real enough bytes returned that a later call
    can use them as a reference in turn."""

    name = "gemini"

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes | None]] = []

    def generate(
        self, prompt: str, *, aspect_ratio: str, model: str, reference_image: bytes | None = None
    ) -> bytes:
        self.calls.append((prompt, reference_image))
        return f"fake-image-bytes:{prompt}".encode()


def test_seed_is_assigned_once_and_reused_across_scenes(tmp_path, monkeypatch):
    fake = _FakeSeedCapableProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel({"visuals_seed_lock": True}))
    _write_scenes(ctx, ["prompt one", "prompt two", "prompt three"])

    VisualsPhase().run(ctx)

    assert ctx.project.visual_seed is not None
    seeds_used = {seed for _prompt, seed in fake.calls}
    assert seeds_used == {ctx.project.visual_seed}


def test_seed_is_not_reassigned_on_a_second_run(tmp_path, monkeypatch):
    fake = _FakeSeedCapableProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel({"visuals_seed_lock": True}))
    _write_scenes(ctx, ["prompt one"])

    VisualsPhase().run(ctx)
    first_seed = ctx.project.visual_seed
    assert first_seed is not None

    VisualsPhase().run(ctx)
    assert ctx.project.visual_seed == first_seed


def test_seed_lock_off_never_assigns_a_seed(tmp_path, monkeypatch):
    fake = _FakeSeedCapableProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel({"visuals_seed_lock": False}))
    _write_scenes(ctx, ["prompt one"])

    VisualsPhase().run(ctx)

    assert ctx.project.visual_seed is None
    assert all(seed is None for _prompt, seed in fake.calls)


def test_seed_is_not_sent_to_a_non_seed_capable_provider(tmp_path, monkeypatch):
    fake = _FakeImageProvider()  # name == "fake", not in _SEED_CAPABLE_PROVIDERS
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel({"visuals_seed_lock": True}))
    _write_scenes(ctx, ["prompt one"])

    VisualsPhase().run(ctx)  # must not raise a TypeError over an unexpected kwarg

    assert ctx.project.visual_seed is not None  # still assigned - just not sent


def test_gemini_reference_lock_off_never_sends_a_reference_image(tmp_path, monkeypatch):
    fake = _FakeGeminiReferenceProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel({"visuals_gemini_reference_lock": False}))
    _write_scenes(ctx, ["prompt one", "prompt two"])

    VisualsPhase().run(ctx)

    assert all(ref is None for _prompt, ref in fake.calls)


def test_gemini_reference_lock_on_reuses_the_first_scenes_bytes(tmp_path, monkeypatch):
    fake = _FakeGeminiReferenceProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel({"visuals_gemini_reference_lock": True}))
    _write_scenes(ctx, ["prompt one", "prompt two", "prompt three"])

    VisualsPhase().run(ctx)

    calls_by_prompt = dict(fake.calls)
    assert calls_by_prompt["prompt one"] is None  # the first scene has nothing to reference yet
    expected_reference = b"fake-image-bytes:prompt one"
    assert calls_by_prompt["prompt two"] == expected_reference
    assert calls_by_prompt["prompt three"] == expected_reference


def test_gemini_reference_lock_on_proceeds_without_a_reference_if_the_first_scene_fails(tmp_path, monkeypatch):
    class _FailFirst(_FakeGeminiReferenceProvider):
        def generate(self, prompt, *, aspect_ratio, model, reference_image=None):
            self.calls.append((prompt, reference_image))
            if prompt == "prompt one":
                raise AvgError(ErrorKind.network, "simulated failure on the reference scene")
            return f"fake-image-bytes:{prompt}".encode()

    fake = _FailFirst()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel({"visuals_gemini_reference_lock": True}))
    _write_scenes(ctx, ["prompt one", "prompt two"])

    VisualsPhase().run(ctx)  # must not raise - one failed scene, not a dead phase

    calls_by_prompt = dict(fake.calls)
    assert calls_by_prompt["prompt two"] is None  # no reference was ever produced
```

Run these — fail (no seed/reference-image wiring exists in `VisualsPhase`
yet). Also confirm `_generate_one` has one existing direct-call test
(`tests/pipeline/test_phases_media.py`, calling it with 6 positional
args, no `extra_kwargs`) — this is why `extra_kwargs` gets a default of
`None` below rather than being required.

### Step 4.2 — Implementation

**Verified against the real file before writing these blocks — this is
the plan's most consequential set of edits, and was applied and run
end-to-end, not just reasoned through.**

Replace the import block:

```python
from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from avg.core.errors import AvgError, ErrorKind, report_degradation
```

with:

```python
from __future__ import annotations

import ctypes
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from avg.core.errors import AvgError, ErrorKind, report_degradation
```

Replace the provider defaults block:

```python
_DEFAULT_PROVIDER = "pollinations"
_DEFAULT_MODEL = "flux"
```

with:

```python
_DEFAULT_PROVIDER = "pollinations"
_DEFAULT_MODEL = "flux"

# The only two providers whose API actually accepts a seed parameter -
# Gemini/OpenAI/HuggingFace have no such concept in their request shapes.
_SEED_CAPABLE_PROVIDERS = {"pollinations", "stablehorde"}
```

Replace `_generate_one`'s signature and its `provider.generate(...)` call:

```python
def _generate_one(
    provider: ImageProvider, prompt: str, aspect_ratio: str, model: str, scene: int, out_path: Path
) -> _SceneResult:
    """Generate and save exactly one scene's image. Never raises - every
    failure (provider `AvgError`, or any other exception a buggy provider
    might throw) becomes a failed `_SceneResult` instead, which is what
    lets one bad call run alongside eleven good ones in the same worker
    pool without a single `Future.result()` blowing up the whole phase."""
    try:
        data = provider.generate(prompt, aspect_ratio=aspect_ratio, model=model)
```

with:

```python
def _generate_one(
    provider: ImageProvider,
    prompt: str,
    aspect_ratio: str,
    model: str,
    scene: int,
    out_path: Path,
    extra_kwargs: dict[str, Any] | None = None,
) -> _SceneResult:
    """Generate and save exactly one scene's image. Never raises - every
    failure (provider `AvgError`, or any other exception a buggy provider
    might throw) becomes a failed `_SceneResult` instead, which is what
    lets one bad call run alongside eleven good ones in the same worker
    pool without a single `Future.result()` blowing up the whole phase.

    `extra_kwargs` carries whatever a specific provider accepts beyond the
    shared protocol (`seed` for Pollinations/Stable Horde, `reference_image`
    for Gemini) - built once per call by `_generate_all`'s own
    `_extra_kwargs`, never guessed here."""
    try:
        data = provider.generate(prompt, aspect_ratio=aspect_ratio, model=model, **(extra_kwargs or {}))
```

Replace `run()`'s settings-resolution block:

```python
        settings = ctx.channel_values
        provider_name = str(settings.get("image_provider") or _DEFAULT_PROVIDER)
        model = str(settings.get("image_model") or _DEFAULT_MODEL)
        aspect_ratio = ctx.project.aspect_ratio

        transport = ctx.router if isinstance(ctx.router, Transport) else None
        provider = _build_provider(provider_name, transport)
```

with:

```python
        settings = ctx.channel_values
        provider_name = str(settings.get("image_provider") or _DEFAULT_PROVIDER)
        model = str(settings.get("image_model") or _DEFAULT_MODEL)
        aspect_ratio = ctx.project.aspect_ratio

        # Assigned once per take, on this phase's first run - reused for
        # every scene and every later re-run of the same take, locking the
        # take's whole look rather than one image's. No direct
        # save_project call: the mutation reaches disk through the exact
        # mechanism this phase's other side effects already rely on (the
        # runner's own on_phase_change write-through) - see this slice's
        # spec for why a phase calling save_project itself would be new,
        # unestablished behaviour this codebase deliberately never does.
        if bool(settings.get("visuals_seed_lock", True)) and ctx.project.visual_seed is None:
            ctx.project.visual_seed = random.randint(0, 2**31 - 1)
        seed = ctx.project.visual_seed
        gemini_reference_lock = bool(settings.get("visuals_gemini_reference_lock", False))

        transport = ctx.router if isinstance(ctx.router, Transport) else None
        provider = _build_provider(provider_name, transport)
```

Replace `_generate_all` in full:

```python
        def _generate_all(prov: ImageProvider, mdl: str) -> dict[int, _SceneResult]:
            """One full pass over every scene with one provider."""
            out: dict[int, _SceneResult] = {}
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_generate_one, prov, prompt, aspect_ratio, mdl, i, images_dir / f"{i:03d}.png"): i
                    for i, prompt in jobs
                }
                for future in as_completed(futures):
                    result = future.result()
                    out[result.scene] = result
                    done += 1
                    percent = int(done / total * 100)
                    status = "ok" if result.ok else f"failed ({result.error})"
                    ctx.emit(
                        ProgressEvent(
                            project_id=ctx.project.id,
                            percent=percent,
                            message=f"Scene {result.scene}/{total}: {status}",
                        )
                    )
            return out
```

with:

```python
        def _extra_kwargs(prov: ImageProvider, reference_image: bytes | None) -> dict[str, Any]:
            kwargs: dict[str, Any] = {}
            if seed is not None and prov.name in _SEED_CAPABLE_PROVIDERS:
                kwargs["seed"] = seed
            if reference_image is not None and prov.name == "gemini":
                kwargs["reference_image"] = reference_image
            return kwargs

        def _emit_scene_progress(result: _SceneResult, done: int) -> None:
            percent = int(done / total * 100)
            status = "ok" if result.ok else f"failed ({result.error})"
            ctx.emit(
                ProgressEvent(
                    project_id=ctx.project.id,
                    percent=percent,
                    message=f"Scene {result.scene}/{total}: {status}",
                )
            )

        def _generate_all(prov: ImageProvider, mdl: str) -> dict[int, _SceneResult]:
            """One full pass over every scene with one provider."""
            out: dict[int, _SceneResult] = {}
            done = 0
            remaining = list(jobs)

            reference_image: bytes | None = None
            if gemini_reference_lock and prov.name == "gemini" and remaining:
                # Every other scene in this pass can use the first one as a
                # visual reference once it exists - but "once it exists"
                # means generating it before the rest are submitted in
                # parallel, or every scene races a reference that isn't
                # there yet. A failed first scene simply leaves no
                # reference for the rest, rather than blocking the phase.
                first_scene, first_prompt = remaining.pop(0)
                first_result = _generate_one(
                    prov,
                    first_prompt,
                    aspect_ratio,
                    mdl,
                    first_scene,
                    images_dir / f"{first_scene:03d}.png",
                    _extra_kwargs(prov, None),
                )
                out[first_result.scene] = first_result
                done += 1
                _emit_scene_progress(first_result, done)
                if first_result.ok and first_result.file:
                    reference_image = (images_dir / first_result.file).read_bytes()

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        _generate_one,
                        prov,
                        prompt,
                        aspect_ratio,
                        mdl,
                        i,
                        images_dir / f"{i:03d}.png",
                        _extra_kwargs(prov, reference_image),
                    ): i
                    for i, prompt in remaining
                }
                for future in as_completed(futures):
                    result = future.result()
                    out[result.scene] = result
                    done += 1
                    _emit_scene_progress(result, done)
            return out
```

Both call sites of `_generate_all` (`_generate_all(provider, model)` for
the primary pass, `_generate_all(_build_provider(backup_provider, transport),
backup_model)` for the fallback pass) are unchanged — `_generate_all`'s
own signature never changed, only its body, so nothing at either call
site needs editing.

Run Step 4.1's tests — pass. **Mechanically verified**: full `tests/
pipeline/test_phases_media.py` run, 56 passed (49 pre-existing + 7 new),
1 pre-existing failure unrelated (`test_visuals_writes_safezones_from_
the_real_images` — confirmed failing identically on the unmodified
checkout in this sandbox; a missing dependency in this minimal verify
venv, not this repo's real `.venv-build`).

**Commit:** `feat(visuals): seed assignment/persistence + Gemini reference-image sequencing`

---

## Task 5 — Director II reads `video_map.json` and `scene_safezones.json`

### Step 5.1 — Test

Add to `tests/pipeline/test_phases_polish.py` (immediately before the
existing `test_director2_survives_a_missing_audio_map`, which these two
sit next to thematically):

```python
def test_director2_prompt_includes_video_map_and_safezone_context(tmp_path, monkeypatch):
    """Director I's role/beat/visual_intent and Visuals' safe/busy zone -
    both already on disk by the time Director II runs - now actually reach
    its prompt, where before this slice neither file was ever read back."""
    reply = json.dumps({
        "version": 1,
        "grade": {"look": "warm"},
        "scenes": [
            {"scene": 1, "motion": {"type": "zoom_in", "intensity": 0.3, "ease": "ease_out_cubic"},
             "transition_in": "fade"},
            {"scene": 2, "motion": {"type": "pan_up", "intensity": 0.4, "ease": "ease_out_cubic"},
             "transition_in": "cut"},
        ],
    })
    generator = _QueueGenerator([reply])
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", generator)

    project = _project(tmp_path)
    ctx, _events = _ctx(project)
    _write_scenes(ctx, ["Scene one text.", "Scene two text."])
    write_json(script_dir(ctx) / "video_map.json", {
        "version": 1,
        "image_count": 2,
        "parts": [
            {"scene": 1, "role": "hook", "beat": "HIT", "visual_intent": "CLOSEUP"},
            {"scene": 2, "role": "payoff", "beat": "none", "visual_intent": "WIDE"},
        ],
    })
    write_json(render_dir(ctx) / "scene_safezones.json", [
        {"scene": 1, "safe_zone": "center", "busy_zone": "lower-third"},
        {"scene": 2, "safe_zone": "upper-third", "busy_zone": "none"},
    ])

    Director2Phase().run(ctx)

    assert len(generator.calls) == 1
    _phase, prompt = generator.calls[0]
    assert "role=hook" in prompt
    assert "beat=HIT" in prompt
    assert "busy_zone=lower-third" in prompt
    assert "role=payoff" in prompt


def test_director2_prompt_degrades_cleanly_without_video_map_or_safezones(tmp_path, monkeypatch):
    """Neither file existing (a phase run in isolation, or an older
    project) must not fail the phase - the context is guidance, not a
    load-bearing invariant, matching every other optional file this
    pipeline reads."""
    reply = json.dumps({
        "version": 1,
        "grade": {"look": "warm"},
        "scenes": [
            {"scene": 1, "motion": {"type": "zoom_in", "intensity": 0.3, "ease": "ease_out_cubic"},
             "transition_in": "fade"},
        ],
    })
    generator = _QueueGenerator([reply])
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", generator)

    project = _project(tmp_path)
    ctx, _events = _ctx(project)
    _write_scenes(ctx, ["Scene one text."])

    Director2Phase().run(ctx)  # must not raise

    assert len(generator.calls) == 1
    _phase, prompt = generator.calls[0]
    assert '[1] "Scene one text."' in prompt
```

Run these — fail, `Director2Phase.run` doesn't read either file yet.

### Step 5.2 — Implementation

**Verified against the real file before writing these blocks.** In
`avg/pipeline/phases/director.py`, replace `_coerce_int` and add the new
context function right after it:

```python
def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
```

with:

```python
def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _director2_scene_context(ctx: PhaseContext) -> dict[int, dict[str, str]]:
    """Per-scene role/beat/visual_intent (Director I's own `video_map.json`)
    merged with safe_zone/busy_zone (Visuals' `scene_safezones.json`), keyed
    by scene number, for `build_director2_prompt` to fold into its prompt.

    Both files are guaranteed on disk by the time Director II runs
    (`REGISTRY` order: Director I, then Visuals, then Voice, then this
    phase) - but read defensively anyway, exactly like every other
    optional-guidance file this pipeline produces: a missing or malformed
    file degrades to an empty context for the affected scenes, never a
    phase failure. Nothing before this slice ever read `video_map.json`
    or `scene_safezones.json` back after the phase that wrote it - both
    were computed and then never looked at again.
    """
    context: dict[int, dict[str, str]] = {}

    video_map = read_json(script_dir(ctx) / "video_map.json")
    if isinstance(video_map, dict):
        for part in video_map.get("parts") or []:
            if not isinstance(part, dict):
                continue
            scene = _coerce_int(part.get("scene"))
            if scene is None:
                continue
            entry = context.setdefault(scene, {})
            for key in ("role", "beat", "visual_intent"):
                value = part.get(key)
                if value:
                    entry[key] = str(value)

    safezones = read_json(render_dir(ctx) / "scene_safezones.json")
    if isinstance(safezones, list):
        for item in safezones:
            if not isinstance(item, dict):
                continue
            scene = _coerce_int(item.get("scene"))
            if scene is None:
                continue
            entry = context.setdefault(scene, {})
            for key in ("safe_zone", "busy_zone"):
                value = item.get(key)
                if value:
                    entry[key] = str(value)

    return context
```

Replace `Director2Phase.run`'s prompt-building line:

```python
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning motion and grade..."))
        prompt = build_director2_prompt(inputs, scene_texts)
```

with:

```python
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning motion and grade..."))
        scene_context = _director2_scene_context(ctx)
        prompt = build_director2_prompt(inputs, scene_texts, scene_context=scene_context)
```

In `avg/pipeline/prompts.py`, add a new helper right before
`build_director2_prompt` and extend that function's signature and its
`numbered` line. Replace:

```python
    return f"""VIDEO STYLE (the creative bible — keep the whole piece feeling consistent with this):
- personality: {preset.description or preset.name}
- visual tone: {tone_text}
- emotional arc: {emotional_arc}{brand_line}"""


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
    return f"""VIDEO STYLE (the creative bible — keep the whole piece feeling consistent with this):
- personality: {preset.description or preset.name}
- visual tone: {tone_text}
- emotional arc: {emotional_arc}{brand_line}"""


def _director2_scene_line(i: int, text: str, scene_context: dict[int, dict[str, str]]) -> str:
    """One numbered scene line for `build_director2_prompt`'s SCENES block,
    with Director I's role/beat/visual_intent and Visuals' safe/busy zone
    appended in parentheses when present for that scene number (1-indexed,
    matching `scene_context`'s own keys - `i` here is 0-indexed)."""
    line = f'[{i + 1}] "{text}"'
    context = scene_context.get(i + 1)
    if context:
        tags = ", ".join(f"{key}={value}" for key, value in context.items())
        line += f" ({tags})"
    return line


def build_director2_prompt(
    inputs: CreativeInputs,
    scene_texts: list[str],
    *,
    personality: dict[str, PersonalityPreset] | None = None,
    emotional_arc: str = "",
    scene_context: dict[int, dict[str, str]] | None = None,
) -> str:
```

Replace the `numbered` line and add one sentence to the opening
paragraph (the model needs telling what the parenthetical tags mean, or
they are just noise):

```python
    style_bible = build_director_style_bible(inputs, personality=personality, emotional_arc=emotional_arc)
    numbered = "\n".join(f'[{i + 1}] "{text}"' for i, text in enumerate(scene_texts))

    return f"""You are the DIRECTOR / EDITOR of a short AI-generated video built from still images with
narration. Think like a bold human editor: your goal is to make THIS video feel special and
intentional — a directed, edited piece, not images sliding by. You have real creative
authority over pacing, camera movement, transitions and colour. Use contrast (calm vs.
punchy scenes) and motion that rides the narration's energy — but stay coherent.
```

with:

```python
    style_bible = build_director_style_bible(inputs, personality=personality, emotional_arc=emotional_arc)
    context = scene_context or {}
    numbered = "\n".join(_director2_scene_line(i, text, context) for i, text in enumerate(scene_texts))

    return f"""You are the DIRECTOR / EDITOR of a short AI-generated video built from still images with
narration. Think like a bold human editor: your goal is to make THIS video feel special and
intentional — a directed, edited piece, not images sliding by. You have real creative
authority over pacing, camera movement, transitions and colour. Use contrast (calm vs.
punchy scenes) and motion that rides the narration's energy — but stay coherent. Where a
scene lists a role, beat or busy zone in parentheses, that is Director I's own structural
call and where the generated image is already visually busy — respect it rather than
re-deciding it: a HIT beat wants a decisive move, not a drift; a busy zone is not where a
caption or a slow pan should crowd the frame further.
```

Run Step 5.1's tests — pass. **Mechanically verified**: full `tests/
pipeline/test_phases_polish.py` + `tests/pipeline/test_prompts.py` run,
85 passed (83 pre-existing + 2 new), 1 pre-existing failure unrelated
(`test_resolve_font_family_extracts_family_name_from_a_font_file_path` —
confirmed failing identically on the unmodified checkout: a Windows-path
string parsed differently under this Linux sandbox's `pathlib`, nothing
to do with this plan).

**Commit:** `feat(director): Director II reads video_map.json + scene_safezones.json into its prompt`

---

## Manual verification (after all 5 tasks)

1. `.venv-linux/bin/python -m pytest tests -q` (or the Windows
   equivalent) — zero regressions vs. the ledger's documented baseline.
2. `mypy` — clean on every file this plan touched.
3. A real (or `AVG_HTTP_RECORD=1`) run with `visuals_seed_lock` on and a
   Pollinations/Stable Horde channel: confirm every scene's request
   actually carried the same seed, and that a re-run of the same take
   reuses it rather than drawing a new one.
4. A real run with `visuals_gemini_reference_lock` on: confirm scene 1
   generates alone before scenes 2+ start (visible in the run log's
   per-scene progress ordering), and that the later Gemini requests
   actually carried an `inline_data` part.
5. A real run with a real `video_map.json`/`scene_safezones.json`
   present: read back Director II's own prompt (or its logged request,
   if `AVG_HTTP_RECORD=1`) and confirm the parenthetical tags appear and
   look sane for a real script, not just the synthetic fixture above.

## Out of scope (matches the spec)

- Per-style recycle pools - deferred until Slice 31 sub-slice 2's base
  recycling exists.
- Composite multi-scene grids - dropped, not reconsidered.
- Seed support for Gemini/OpenAI/HuggingFace - not a gap, those APIs
  have no such parameter.
- Sub-slices 1, 3, 4 — separate plans (1 already written).
