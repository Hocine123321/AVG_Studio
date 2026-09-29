"""Tests for the two media-producing phases wired into ids 3-4 of
`avg.pipeline.registry.REGISTRY`: `avg.pipeline.phases.visuals.VisualsPhase`
and `avg.pipeline.phases.voice.VoicePhase`.

No live API calls, and - since `pydub` is not installed in this project's
lean test venv (`.venv-build`; it is only a *runtime* dependency of the
`media` extra, installed by the app itself - see `pyproject.toml` and
`CLAUDE.md`'s "Python dependencies" section) - no real audio processing
either. Every provider is a hand-written fake monkeypatched directly onto
each phase module's own `_build_provider`, mirroring
`tests/pipeline/test_phases_text.py`'s established pattern of patching a
phase module's imported name rather than driving the provider/transport
layer underneath it. `voice.py`'s own `_assemble_with_gaps` (the one place
it touches `pydub`) and the vendored `remove_silences_core` are likewise
monkeypatched, never invoked for real.

Three invariants this file exists to prove (the task brief's own words):
  1. the image worker pool is actually capped by the RAM profile, not a
     hardcoded constant that happens to read `2` today;
  2. one scene's image failing does not take the other scenes down with
     it, and is still recorded, not silently dropped;
  3. a `[pause]` marker never reaches a voice provider as literal text, and
     produces a real, non-zero silence gap instead.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import shutil

import pytest

from avg.config.channels import Channel
from avg.core.errors import AvgError, ErrorKind
from avg.core.events import ProgressEvent
from avg.core.models import ProjectDoc
from avg.core.settings_schema import resolve_channel_settings
from avg.pipeline.phase import CancellationToken, PhaseContext
from avg.pipeline.phases._common import read_json, script_dir, write_json
from avg.pipeline.phases.visuals import VisualsPhase
from avg.pipeline.phases.voice import VoicePhase, _AGGRESSIVENESS_PARAMS, _clean_segment, _removed_pct

# ── shared test scaffolding (mirrors test_phases_text.py) ──────────────────


def _project(tmp_path: Path, **overrides: Any) -> ProjectDoc:
    defaults: dict[str, Any] = dict(
        id="proj1",
        title="The Antikythera Mechanism",
        folder=tmp_path,
        created="2026-07-26T00:00:00",
    )
    defaults.update(overrides)
    doc = ProjectDoc(**defaults)
    for sub in ("script", "images", "audio", "render"):
        (doc.folder / sub).mkdir(parents=True, exist_ok=True)
    return doc


def _channel(settings: dict[str, Any] | None = None) -> Channel:
    return Channel(id="ch", label="Ch", settings=settings or {})


def _ctx(project: ProjectDoc, *, channel: Channel | None = None, router: Any = None) -> tuple[PhaseContext, list[Any]]:
    events: list[Any] = []
    resolved_channel = channel or _channel()
    ctx = PhaseContext(
        project=project,
        channel=resolved_channel,
        channel_values=resolve_channel_settings(resolved_channel.settings, {}),
        router=router,
        emit=events.append,
        cancel_token=CancellationToken(),
    )
    return ctx, events


def _write_scenes(ctx: PhaseContext, prompts: list[str]) -> None:
    write_json(
        script_dir(ctx) / "scenes.json",
        [
            {"index": i + 1, "text": f"scene {i + 1} text", "image_prompt": p, "label": "Scene"}
            for i, p in enumerate(prompts)
        ],
    )


# ═══════════════════════════════════════════════════════════════════════
# VisualsPhase
# ═══════════════════════════════════════════════════════════════════════


class _FakeImageProvider:
    """Records every `generate()` call; raises `AvgError` for any prompt in
    `fail_prompts`, otherwise returns deterministic fake bytes."""

    name = "fake"

    def __init__(self, fail_prompts: frozenset[str] = frozenset()) -> None:
        self.fail_prompts = fail_prompts
        self.calls: list[tuple[str, str, str]] = []

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        self.calls.append((prompt, aspect_ratio, model))
        if prompt in self.fail_prompts:
            raise AvgError(ErrorKind.network, f"simulated provider failure for {prompt!r}")
        return f"fake-image-bytes:{prompt}".encode()


def test_scene_audio_map_spans_the_whole_track():
    """`compute_scene_timings` DROPS every scene that has no entry in this
    file and raises when that is all of them
    (`avg/media/vendor/avg.py:2009`), and `render_project` falls back to
    dividing the track equally. One entry per scene, contiguous, last end
    equal to the audio duration."""
    from avg.pipeline.phases.voice import scene_audio_map

    doc = scene_audio_map(["a much longer scene line", "short"], 12.0)

    assert doc["total_duration"] == 12.0
    assert [s["scene"] for s in doc["scenes"]] == [1, 2]
    assert doc["scenes"][0]["start"] == 0.0
    assert doc["scenes"][-1]["end"] == 12.0
    assert doc["scenes"][0]["end"] == doc["scenes"][1]["start"]
    # Longer text, longer window - the split is proportional.
    assert doc["scenes"][0]["duration"] > doc["scenes"][1]["duration"]


def test_voice_writes_the_scene_audio_map(tmp_path, monkeypatch):
    """Measured from the FINISHED audio, after dead-air removal has
    shortened it - a map built from the pre-trim segments would run late by
    however much air was cut."""
    fake = _FakeVoiceProvider()
    _patch_voice_collaborators(monkeypatch, fake)
    monkeypatch.setattr("avg.pipeline.phases.voice.audio_duration_seconds", lambda path: 10.0)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "Scene one text. Scene two text.")
    write_json(
        script_dir(ctx) / "scenes.json",
        [{"index": 1, "text": "Scene one text."}, {"index": 2, "text": "Scene two text."}],
    )

    VoicePhase().run(ctx)

    doc = read_json(project.folder / "audio" / "scene_audio_map.json")
    assert doc["total_duration"] == 10.0
    assert [s["scene"] for s in doc["scenes"]] == [1, 2]
    assert doc["scenes"][-1]["end"] == 10.0


def test_voice_writes_no_map_without_scenes_json(tmp_path, monkeypatch):
    """Voice reads narration.txt, so it can run with no scenes.json at all
    (a legacy project, a hand-edited folder). An empty or invented map
    would be worse than none: compute_scene_timings would then drop the
    scenes it does not mention."""
    fake = _FakeVoiceProvider()
    _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "Just narration, no scene list.")

    VoicePhase().run(ctx)

    assert not (project.folder / "audio" / "scene_audio_map.json").exists()


class _FakePngProvider:
    """Returns REAL png bytes, so the safezone analysis can open them.

    `_FakeImageProvider` above returns a marker string, which is all the
    generation tests need. Safezone analysis actually decodes each image
    with Pillow, so it needs a file that is genuinely an image.
    """

    name = "fake-png"

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (16, 32), (30, 30, 30)).save(buffer, format="PNG")
        return buffer.getvalue()


def test_index_entries_keep_position_meaning_scene():
    """Position n in index.json IS scene n+1.

    The vendored engine builds `{i + 1: img for i, img in enumerate(images)}`
    (`avg/media/vendor/avg.py:2236`). Omitting a failed scene's entry
    therefore shifts EVERY later image one scene earlier against narration
    that still plays for the missing one - the whole video drifts out of
    sync from the first failure onwards.
    """
    from avg.pipeline.phases.visuals import _SceneResult, index_entries

    results = {
        1: _SceneResult(scene=1, ok=True, file="001.png"),
        2: _SceneResult(scene=2, ok=False, error="rate limited"),
        3: _SceneResult(scene=3, ok=True, file="003.png"),
    }

    assert index_entries(results, 3) == ["images/001.png", "images/001.png", "images/003.png"]


def test_a_failed_first_scene_borrows_the_next_image():
    """There is no previous image to repeat, so the fill looks forward.
    Anything else writes a path that does not exist and turns a partial
    render into a failed one."""
    from avg.pipeline.phases.visuals import _SceneResult, index_entries

    results = {
        1: _SceneResult(scene=1, ok=False, error="safety filter"),
        2: _SceneResult(scene=2, ok=True, file="002.png"),
    }

    assert index_entries(results, 2) == ["images/002.png", "images/002.png"]


def test_a_scene_with_no_result_at_all_is_filled_like_a_failed_one():
    """A scene the pool never reported on is indistinguishable from one
    that failed, as far as the render is concerned: there is no image."""
    from avg.pipeline.phases.visuals import _SceneResult, index_entries

    results = {1: _SceneResult(scene=1, ok=True, file="001.png")}

    assert index_entries(results, 3) == ["images/001.png", "images/001.png", "images/001.png"]


def test_visuals_writes_render_index_json(tmp_path, monkeypatch):
    """The file the render engine actually reads. Without it
    `render_project` exits 1 (`avg/media/vendor/avg.py:2228`) and no render
    this app starts can succeed."""
    fake = _FakeImageProvider(fail_prompts=frozenset({"prompt two"}))
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project)
    _write_scenes(ctx, ["prompt one", "prompt two", "prompt three"])

    VisualsPhase().run(ctx)

    index = read_json(project.folder / "render" / "index.json")
    assert index == ["images/001.png", "images/001.png", "images/003.png"]


def test_visuals_writes_safezones_from_the_real_images(tmp_path, monkeypatch):
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: _FakePngProvider())

    project = _project(tmp_path)
    ctx, _events = _ctx(project)
    _write_scenes(ctx, ["prompt one", "prompt two"])

    VisualsPhase().run(ctx)

    safezones = read_json(project.folder / "render" / "scene_safezones.json")
    assert [entry["scene"] for entry in safezones] == [1, 2]
    assert "safe_zone" in safezones[0]


def test_a_safezone_failure_does_not_fail_the_phase(tmp_path, monkeypatch):
    """It costs overlay placement quality, never the video. The fake
    provider's bytes are not a decodable image, which is exactly the
    situation a corrupt download produces."""
    fake = _FakeImageProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _events = _ctx(project)
    _write_scenes(ctx, ["prompt one"])

    VisualsPhase().run(ctx)  # must NOT raise

    assert (project.folder / "render" / "index.json").is_file()


def test_visuals_inputs_ready_false_without_scenes_json(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    assert VisualsPhase().inputs_ready(ctx) is False


def test_visuals_inputs_ready_true_with_scenes_json(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["a prompt"])
    assert VisualsPhase().inputs_ready(ctx) is True


def test_visuals_raises_when_scenes_json_missing(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    with pytest.raises(AvgError) as excinfo:
        VisualsPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug


def test_visuals_generates_one_image_per_scene_and_writes_manifest(tmp_path, monkeypatch):
    fake = _FakeImageProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, events = _ctx(project, channel=_channel({"image_provider": "pollinations", "image_model": "flux"}))
    _write_scenes(ctx, ["prompt one", "prompt two", "prompt three"])

    VisualsPhase().run(ctx)

    images_dir = project.folder / "images"
    assert (images_dir / "001.png").read_bytes() == b"fake-image-bytes:prompt one"
    assert (images_dir / "002.png").read_bytes() == b"fake-image-bytes:prompt two"
    assert (images_dir / "003.png").read_bytes() == b"fake-image-bytes:prompt three"

    manifest = read_json(images_dir / "manifest.json")
    assert manifest["provider"] == "pollinations"
    assert manifest["model"] == "flux"
    assert manifest["scenes"] == [
        {"scene": 1, "status": "ok", "file": "001.png", "error": ""},
        {"scene": 2, "status": "ok", "file": "002.png", "error": ""},
        {"scene": 3, "status": "ok", "file": "003.png", "error": ""},
    ]

    # Every call reached the fake provider with the project's aspect ratio.
    assert all(ar == project.aspect_ratio for _, ar, _ in fake.calls)
    assert any(isinstance(e, ProgressEvent) and e.percent == 100 for e in events)


def test_visuals_inherits_image_provider_from_the_app_wide_defaults(tmp_path, monkeypatch):
    """Slice 19 Task 5: the phase reads `ctx.channel_values`, not
    `ctx.channel.settings.get(...)` directly - so a channel that owns
    neither `image_provider` nor `image_model` still uses whatever the
    app-wide defaults document supplies, not the phase's own hardcoded
    `_DEFAULT_PROVIDER`/`_DEFAULT_MODEL` fallback."""
    fake = _FakeImageProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    channel = _channel({})  # owns neither key
    ctx, _ = _ctx(project, channel=channel)
    ctx.channel_values = resolve_channel_settings(
        channel.settings, {"image_provider": "pollinations", "image_model": "flux"}
    )
    _write_scenes(ctx, ["prompt one"])

    VisualsPhase().run(ctx)

    manifest = read_json(project.folder / "images" / "manifest.json")
    assert manifest["provider"] == "pollinations"
    assert manifest["model"] == "flux"


def test_visuals_one_image_failing_does_not_kill_the_phase(tmp_path, monkeypatch):
    """The task's named invariant: scene 2 of 3 fails outright, but scenes
    1 and 3 still land on disk, the phase does not raise, and the failure
    is recorded (not silently dropped)."""
    fake = _FakeImageProvider(fail_prompts=frozenset({"prompt two"}))
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    _write_scenes(ctx, ["prompt one", "prompt two", "prompt three"])

    VisualsPhase().run(ctx)  # must NOT raise

    images_dir = project.folder / "images"
    assert (images_dir / "001.png").exists()
    assert (images_dir / "003.png").exists()
    assert not (images_dir / "002.png").exists()

    manifest = read_json(images_dir / "manifest.json")
    by_scene = {s["scene"]: s for s in manifest["scenes"]}
    assert by_scene[1]["status"] == "ok"
    assert by_scene[3]["status"] == "ok"
    assert by_scene[2]["status"] == "failed"
    assert by_scene[2]["file"] is None
    assert "simulated provider failure" in by_scene[2]["error"]

    # The final "Visuals complete" progress event still fires, counting only
    # the scenes that actually succeeded (a per-scene 100% event also lands
    # once the last scene's future completes, regardless of that scene's own
    # outcome - this checks the phase-level summary specifically).
    summary = [e for e in events if isinstance(e, ProgressEvent) and "Visuals complete" in e.message]
    assert summary and "2/3" in summary[0].message


def test_visuals_raises_when_every_scene_fails(tmp_path, monkeypatch):
    fake = _FakeImageProvider(fail_prompts=frozenset({"prompt one", "prompt two"}))
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["prompt one", "prompt two"])

    with pytest.raises(AvgError) as excinfo:
        VisualsPhase().run(ctx)
    # `_FakeImageProvider` raises `ErrorKind.network` for both scenes, and
    # that classification - not a bare `bug` - is now the honest answer for
    # why the phase failed; see `_dominant_kind`.
    assert excinfo.value.kind == ErrorKind.network
    assert "simulated provider failure" in excinfo.value.message
    assert not (project.folder / "images" / "manifest.json").exists()


def test_visuals_worker_cap_is_two_at_or_below_eight_gb(tmp_path, monkeypatch):
    """The task's other named invariant: this machine's own real profile
    (7.9GB) must land on the 2-worker path, not a theoretical branch."""
    captured: list[int | None] = []

    class _SpyExecutor(ThreadPoolExecutor):
        def __init__(self, max_workers=None, *args: Any, **kwargs: Any) -> None:
            captured.append(max_workers)
            super().__init__(max_workers=max_workers, *args, **kwargs)

    monkeypatch.setattr("avg.pipeline.phases.visuals.ThreadPoolExecutor", _SpyExecutor)
    monkeypatch.setattr("avg.pipeline.phases.visuals._total_ram_gb", lambda: 7.9)
    fake = _FakeImageProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, [f"prompt {i}" for i in range(5)])

    VisualsPhase().run(ctx)

    assert captured == [2]


def test_visuals_worker_cap_is_four_above_eight_gb(tmp_path, monkeypatch):
    captured: list[int | None] = []

    class _SpyExecutor(ThreadPoolExecutor):
        def __init__(self, max_workers=None, *args: Any, **kwargs: Any) -> None:
            captured.append(max_workers)
            super().__init__(max_workers=max_workers, *args, **kwargs)

    monkeypatch.setattr("avg.pipeline.phases.visuals.ThreadPoolExecutor", _SpyExecutor)
    monkeypatch.setattr("avg.pipeline.phases.visuals._total_ram_gb", lambda: 16.0)
    fake = _FakeImageProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, [f"prompt {i}" for i in range(5)])

    VisualsPhase().run(ctx)

    assert captured == [4]


def test_visuals_worker_cap_never_exceeds_the_scene_count(tmp_path, monkeypatch):
    """Above the RAM ceiling the cap is 4, but two scenes should never spin
    up four workers - the cap is a ceiling, not a target."""
    captured: list[int | None] = []

    class _SpyExecutor(ThreadPoolExecutor):
        def __init__(self, max_workers=None, *args: Any, **kwargs: Any) -> None:
            captured.append(max_workers)
            super().__init__(max_workers=max_workers, *args, **kwargs)

    monkeypatch.setattr("avg.pipeline.phases.visuals.ThreadPoolExecutor", _SpyExecutor)
    monkeypatch.setattr("avg.pipeline.phases.visuals._total_ram_gb", lambda: 16.0)
    fake = _FakeImageProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["prompt one", "prompt two"])

    VisualsPhase().run(ctx)

    assert captured == [2]


def test_visuals_falls_back_to_scene_text_when_image_prompt_is_blank(tmp_path, monkeypatch):
    fake = _FakeImageProvider()
    monkeypatch.setattr("avg.pipeline.phases.visuals._build_provider", lambda name, transport: fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    write_json(
        script_dir(ctx) / "scenes.json",
        [{"index": 1, "text": "the real narration text", "image_prompt": "", "label": "Scene"}],
    )

    VisualsPhase().run(ctx)

    assert fake.calls[0][0] == "the real narration text"


def _tiny_valid_png_bytes() -> bytes:
    """A real, 1x1 PNG - `Image.open(...).verify()` must accept it, unlike
    an arbitrary byte string."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return buf.getvalue()


def test_visuals_phase_recycles_instead_of_generating(tmp_path, monkeypatch):
    from avg.pipeline.phases import visuals as visuals_module

    pool_image = tmp_path / "pool_sunset.jpg"
    pool_image.write_bytes(_tiny_valid_png_bytes())

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["[RECYCLE #1]"])
    write_json(
        script_dir(ctx) / "recycle_pool.json",
        [{"index": 1, "file": str(pool_image), "description": "A warm sunset."}],
    )

    called = []
    monkeypatch.setattr(visuals_module, "_generate_one", lambda *a, **k: called.append(1) or None)

    visuals_module.VisualsPhase().run(ctx)

    assert not called  # no provider call at all
    out = project.folder / "images" / "001.png"
    assert out.is_file()
    assert out.read_bytes() == pool_image.read_bytes()
    manifest = read_json(project.folder / "images" / "manifest.json")
    assert manifest["scenes"][0]["status"] == "ok"


def test_visuals_phase_falls_back_to_generation_on_out_of_range_pick(tmp_path, monkeypatch):
    from avg.pipeline.phases import visuals as visuals_module

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["[RECYCLE #99]"])
    write_json(
        script_dir(ctx) / "recycle_pool.json",
        [{"index": 1, "file": str(tmp_path / "sunset.jpg"), "description": "A warm sunset."}],
    )

    captured_prompts = []

    def _fake_generate_one(provider, prompt, aspect_ratio, model, scene, out_path, extra_kwargs=None):
        captured_prompts.append(prompt)
        out_path.write_bytes(b"generated")
        from avg.pipeline.phases.visuals import _SceneResult

        return _SceneResult(scene=scene, ok=True, file=out_path.name)

    monkeypatch.setattr(visuals_module, "_generate_one", _fake_generate_one)

    visuals_module.VisualsPhase().run(ctx)

    # index 99 doesn't exist in a 1-entry pool - no *specific* entry to
    # name, so the fallback is the pool's first entry's description, not
    # a blank/generic prompt.
    assert captured_prompts == ["A warm sunset."]


def test_visuals_phase_falls_back_when_the_pool_file_is_not_a_real_image(tmp_path, monkeypatch):
    from avg.pipeline.phases import visuals as visuals_module

    bad_image = tmp_path / "corrupt.jpg"
    bad_image.write_bytes(b"not actually an image")

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["[RECYCLE #1]"])
    write_json(
        script_dir(ctx) / "recycle_pool.json",
        [{"index": 1, "file": str(bad_image), "description": "A warm sunset."}],
    )

    def _fake_generate_one(provider, prompt, aspect_ratio, model, scene, out_path, extra_kwargs=None):
        out_path.write_bytes(b"generated")
        from avg.pipeline.phases.visuals import _SceneResult

        return _SceneResult(scene=scene, ok=True, file=out_path.name)

    monkeypatch.setattr(visuals_module, "_generate_one", _fake_generate_one)

    visuals_module.VisualsPhase().run(ctx)

    out = project.folder / "images" / "001.png"
    assert out.read_bytes() == b"generated"  # fell back, didn't copy the corrupt file


def test_visuals_phase_a_scene_with_no_recycle_marker_is_unaffected(tmp_path, monkeypatch):
    from avg.pipeline.phases import visuals as visuals_module

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["A regular prompt."])
    # No recycle_pool.json at all - matches "recycling was off" exactly.

    def _fake_generate_one(provider, prompt, aspect_ratio, model, scene, out_path, extra_kwargs=None):
        out_path.write_bytes(b"generated")
        from avg.pipeline.phases.visuals import _SceneResult

        return _SceneResult(scene=scene, ok=True, file=out_path.name)

    monkeypatch.setattr(visuals_module, "_generate_one", _fake_generate_one)

    visuals_module.VisualsPhase().run(ctx)

    out = project.folder / "images" / "001.png"
    assert out.read_bytes() == b"generated"


# ═══════════════════════════════════════════════════════════════════════
# VoicePhase
# ═══════════════════════════════════════════════════════════════════════


class _FakeVoiceProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        self.calls.append((text, voice_id, model))
        return f"fake-audio:{text}".encode()

    def list_voices(self) -> list[dict[str, str]]:
        return []


def _write_narration(ctx: PhaseContext, text: str) -> None:
    (script_dir(ctx) / "narration.txt").write_text(text, encoding="utf-8")


def _patch_audio_pipeline(monkeypatch):
    """Stands in for the two `pydub`-touching calls this phase makes
    (`remove_silences_core`, `_assemble_with_gaps`) - `pydub` is not
    installed in this test venv (see module docstring), so every real audio
    operation this phase performs is replaced with a recording fake."""
    remove_silences_calls: list[tuple[Any, ...]] = []

    def fake_remove_silences_core(inp, out, thresh, min_sil, keep, beat_map, timeline, scene_map, pause_map="", pause_target_ms=0):
        remove_silences_calls.append((inp, out, thresh, min_sil, keep, beat_map, timeline, scene_map, pause_map, pause_target_ms))
        Path(out).write_bytes(Path(inp).read_bytes())
        # Matches avg.media.vendor.avg.remove_silences_core's real return
        # shape. removed_dur=0.0 keeps every pre-Slice-21 test's assertion
        # that remove_silences_core is called exactly once per segment
        # true - 0% removed never exceeds any cap, so nothing here ever
        # triggers Slice 21's retry.
        return {"no_speech": False, "original_dur": 1.0, "cleaned_dur": 1.0, "removed_dur": 0.0, "chunks": 1, "pauses_kept": 0}

    monkeypatch.setattr("avg.pipeline.phases.voice.remove_silences_core", fake_remove_silences_core)

    assemble_calls: list[tuple[int, int, str]] = []

    def fake_assemble(segment_paths, gap_ms, out_path):
        assemble_calls.append((len(segment_paths), gap_ms, out_path.name))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"assembled-audio")

    monkeypatch.setattr("avg.pipeline.phases.voice._assemble_with_gaps", fake_assemble)

    return remove_silences_calls, assemble_calls


def _patch_voice_collaborators(monkeypatch, provider: _FakeVoiceProvider):
    """`_patch_audio_pipeline` plus wiring `provider` in as `_build_provider`'s
    result - the combination most tests below want."""
    monkeypatch.setattr("avg.pipeline.phases.voice._build_provider", lambda name, transport: provider)
    return _patch_audio_pipeline(monkeypatch)


def test_voice_inputs_ready_false_without_narration(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    assert VoicePhase().inputs_ready(ctx) is False


def test_voice_inputs_ready_true_with_narration(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "Some narration.")
    assert VoicePhase().inputs_ready(ctx) is True


def test_voice_raises_when_narration_missing(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    with pytest.raises(AvgError) as excinfo:
        VoicePhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug


def test_voice_raises_when_narration_is_empty(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "   ")
    with pytest.raises(AvgError) as excinfo:
        VoicePhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug


def test_pause_marker_text_never_reaches_the_provider(tmp_path, monkeypatch):
    """The task's named invariant: [pause] must become real silence, never
    literal spoken text."""
    fake = _FakeVoiceProvider()
    _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "First half of the story. [pause] Second half of the story.")

    VoicePhase().run(ctx)

    assert len(fake.calls) == 2
    for text, _voice_id, _model in fake.calls:
        assert "[pause]" not in text.lower()
        assert "pause" not in text.lower()
    assert fake.calls[0][0] == "First half of the story."
    assert fake.calls[1][0] == "Second half of the story."


def test_pause_markers_produce_a_real_nonzero_silence_gap(tmp_path, monkeypatch):
    fake = _FakeVoiceProvider()
    _remove_calls, assemble_calls = _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "First part. [pause] Second part.")

    VoicePhase().run(ctx)

    # _assemble_with_gaps is called twice: once for raw_voice.mp3, once for
    # optimized_voice.mp3 - both must carry the SAME real (non-zero) gap,
    # covering both segments.
    assert len(assemble_calls) == 2
    names = {name for _n, _gap, name in assemble_calls}
    assert names == {"raw_voice.mp3", "optimized_voice.mp3"}
    for segment_count, gap_ms, _name in assemble_calls:
        assert segment_count == 2
        assert gap_ms > 0  # a REAL gap, not the marker text and not zero

    assert (project.folder / "audio" / "raw_voice.mp3").exists()
    assert (project.folder / "audio" / "optimized_voice.mp3").exists()


def test_voice_removes_dead_air_via_vendored_remove_silences_core_once_per_segment(tmp_path, monkeypatch):
    fake = _FakeVoiceProvider()
    remove_calls, _assemble_calls = _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "One. [pause] Two. [pause] Three.")

    VoicePhase().run(ctx)

    assert len(remove_calls) == 3  # one call per synthesized segment, never the whole file at once
    for _inp, _out, thresh, min_sil, keep, beat_map, timeline, scene_map, pause_map, pause_target_ms in remove_calls:
        assert thresh == -40.0
        assert min_sil == 400
        assert keep == 100
        # No beat_map/timeline/scene_map exist yet at this point in the
        # pipeline (Director II and Captions haven't run) - the vendored
        # function's own "absent" spelling is "".
        assert beat_map == timeline == scene_map == ""


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
        removed = 2.0 if thresh == -40.0 else 8.0  # less aggressive → less removed
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


def test_a_malformed_max_removable_pct_falls_back_rather_than_crashing(tmp_path, monkeypatch):
    """A hand-edited or otherwise corrupted `settings.json` must not take
    out the whole Voice phase over one bad value in
    `voice_max_removable_silence_pct` - falls back to the 40.0 default,
    the same defensiveness `kb_intensity` already has in
    `avg.pipeline.phases.render._render_kwargs`.
    """
    fake = _FakeVoiceProvider()
    _remove_calls, _assemble_calls = _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    channel = _channel({"voice_max_removable_silence_pct": "not a number"})
    ctx, _events = _ctx(project, channel=channel)
    _write_narration(ctx, "One single segment, no pause markers here.")

    VoicePhase().run(ctx)  # must not raise

    assert (project.folder / "audio" / "optimized_voice.mp3").exists()


def test_voice_no_pause_marker_produces_a_single_segment_and_zero_gap(tmp_path, monkeypatch):
    fake = _FakeVoiceProvider()
    _remove_calls, assemble_calls = _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "Just one continuous sentence with no pause at all.")

    VoicePhase().run(ctx)

    assert len(fake.calls) == 1
    for segment_count, gap_ms, _name in assemble_calls:
        assert segment_count == 1
        assert gap_ms == 0


def test_voice_dynamic_pauses_disabled_synthesizes_one_flat_segment(tmp_path, monkeypatch):
    """With dynamic pauses off, the [pause] marker must still never reach
    the provider as text, but no deliberate gap is inserted either (mirrors
    the legacy app's own disabled-branch behaviour)."""
    fake = _FakeVoiceProvider()
    _remove_calls, assemble_calls = _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project, channel=_channel({"dynamic_pauses_enabled": False}))
    _write_narration(ctx, "First part. [pause] Second part.")

    VoicePhase().run(ctx)

    assert len(fake.calls) == 1
    assert "pause" not in fake.calls[0][0].lower()
    assert fake.calls[0][0] == "First part. Second part."
    for segment_count, gap_ms, _name in assemble_calls:
        assert segment_count == 1
        assert gap_ms == 0


def test_voice_uses_configured_provider_voice_and_model(tmp_path, monkeypatch):
    fake = _FakeVoiceProvider()
    _patch_audio_pipeline(monkeypatch)
    captured_provider_args: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        "avg.pipeline.phases.voice._build_provider",
        lambda name, transport: (captured_provider_args.append((name, transport)), fake)[1],
    )

    project = _project(tmp_path)
    ctx, _ = _ctx(
        project,
        channel=_channel({"voice_provider": "edge", "voice_id": "en-US-GuyNeural", "voice_model_id": "unused"}),
    )
    _write_narration(ctx, "No pauses here at all.")

    VoicePhase().run(ctx)

    assert captured_provider_args[0][0] == "edge"
    assert fake.calls[0][1] == "en-US-GuyNeural"


def test_voice_inherits_provider_from_the_app_wide_defaults(tmp_path, monkeypatch):
    """Slice 19 Task 5: same migration as visuals - a channel that owns no
    `voice_provider` still uses the app-wide defaults document's value,
    read through `ctx.channel_values`, not the phase's own hardcoded
    `_DEFAULT_PROVIDER` fallback."""
    fake = _FakeVoiceProvider()
    _patch_audio_pipeline(monkeypatch)
    captured_provider_args: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        "avg.pipeline.phases.voice._build_provider",
        lambda name, transport: (captured_provider_args.append((name, transport)), fake)[1],
    )

    project = _project(tmp_path)
    channel = _channel({})  # owns no voice_provider
    ctx, _ = _ctx(project, channel=channel)
    ctx.channel_values = resolve_channel_settings(channel.settings, {"voice_provider": "edge"})
    _write_narration(ctx, "No pauses here at all.")

    VoicePhase().run(ctx)

    assert captured_provider_args[0][0] == "edge"


def test_voice_emits_completion_progress_event(tmp_path, monkeypatch):
    fake = _FakeVoiceProvider()
    _patch_voice_collaborators(monkeypatch, fake)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    _write_narration(ctx, "A short narration.")

    VoicePhase().run(ctx)

    assert any(isinstance(e, ProgressEvent) and e.percent == 100 for e in events)


def test_a_scene_failure_keeps_the_error_kind_not_just_the_words():
    """`_SceneResult.error` alone cannot tell an auth failure from a
    timeout, and the phase-level error is built from these."""
    from avg.core.errors import AvgError, ErrorKind
    from avg.pipeline.phases.visuals import _generate_one

    class _Boom:
        name = "stablehorde"

        def generate(self, *_a, **_k):
            raise AvgError(ErrorKind.auth, "stablehorde request failed with HTTP 403")

    result = _generate_one(_Boom(), "a prompt", "9:16", "flux", 1, Path("unused.png"))

    assert result.ok is False
    assert result.kind is ErrorKind.auth
    assert "403" in result.error


def test_the_dominant_kind_is_the_most_common_one():
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _SceneResult, _dominant_kind

    results = {
        1: _SceneResult(scene=1, ok=False, error="a", kind=ErrorKind.auth),
        2: _SceneResult(scene=2, ok=False, error="b", kind=ErrorKind.auth),
        3: _SceneResult(scene=3, ok=False, error="c", kind=ErrorKind.timeout),
    }
    assert _dominant_kind(results) is ErrorKind.auth


def test_a_tie_breaks_by_the_taxonomys_own_priority_order():
    """`_CHECK_ORDER` in `avg/providers/errors.py` already encodes which
    classification is the more actionable claim. No second ordering is
    invented here."""
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _SceneResult, _dominant_kind

    results = {
        1: _SceneResult(scene=1, ok=False, error="a", kind=ErrorKind.timeout),
        2: _SceneResult(scene=2, ok=False, error="b", kind=ErrorKind.auth),
    }
    # auth precedes timeout in _CHECK_ORDER.
    assert _dominant_kind(results) is ErrorKind.auth


def test_an_unclassified_failure_falls_back_to_bug():
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _SceneResult, _dominant_kind

    results = {1: _SceneResult(scene=1, ok=False, error="something odd", kind=None)}
    assert _dominant_kind(results) is ErrorKind.bug


def test_an_auth_failure_falls_back_to_the_backup_once():
    """POLICY says `auth -> switch_key` and `quota -> switch_provider`, so a
    different provider could plausibly help. The backup is tried exactly
    once - never chained - matching the text router's own rule."""
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _should_fall_back

    assert _should_fall_back(ErrorKind.auth) is True
    assert _should_fall_back(ErrorKind.quota) is True
    assert _should_fall_back(ErrorKind.model_missing) is True


def test_a_failure_a_second_provider_would_repeat_never_falls_back():
    """`POLICY` marks these `on_exhausted="fail"` - a different provider
    fails identically, so trying one spends money for nothing."""
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _should_fall_back

    assert _should_fall_back(ErrorKind.content_filter) is False
    assert _should_fall_back(ErrorKind.bug) is False
    assert _should_fall_back(ErrorKind.dependency) is False


# ── the cross-provider fallback actually wired into run() ──────────────────
#
# `_should_fall_back`'s own unit tests above only prove the four-line
# `POLICY` lookup. Nothing until here exercises the behaviour Slice 11 §3.2
# is named for: that `run()` actually calls the backup when every scene
# fails on the primary, that it does so exactly once - never chained into a
# third pass - and that the raised error and the written manifest both name
# the provider that actually produced (or failed to produce) the images.


def _fallback_settings(**overrides: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "image_provider": "primary",
        "image_model": "primary-model",
        "image_provider_backup": "backup",
        "image_model_backup": "backup-model",
        "image_on_failure": "fall_back",
    }
    settings.update(overrides)
    return settings


def test_the_backup_runs_once_when_the_primary_fails_every_scene(tmp_path, monkeypatch):
    primary = _FakeImageProvider(fail_prompts=frozenset({"prompt one", "prompt two"}))
    backup = _FakeImageProvider()
    providers = {"primary": primary, "backup": backup}
    monkeypatch.setattr(
        "avg.pipeline.phases.visuals._build_provider",
        lambda name, transport: providers[name],
    )

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel(_fallback_settings()))
    _write_scenes(ctx, ["prompt one", "prompt two"])

    VisualsPhase().run(ctx)  # must NOT raise - the backup produced everything

    # Every scene was attempted on both providers - one full pass each,
    # never a third.
    assert len(primary.calls) == 2
    assert len(backup.calls) == 2

    images_dir = project.folder / "images"
    assert (images_dir / "001.png").exists()
    assert (images_dir / "002.png").exists()

    manifest = read_json(images_dir / "manifest.json")
    assert manifest["provider"] == "backup"
    assert manifest["model"] == "backup-model"
    assert all(s["status"] == "ok" for s in manifest["scenes"])


def test_the_backup_is_never_chained_when_it_also_fails(tmp_path, monkeypatch):
    primary = _FakeImageProvider(fail_prompts=frozenset({"prompt one", "prompt two"}))
    backup = _FakeImageProvider(fail_prompts=frozenset({"prompt one", "prompt two"}))
    providers = {"primary": primary, "backup": backup}
    monkeypatch.setattr(
        "avg.pipeline.phases.visuals._build_provider",
        lambda name, transport: providers[name],
    )

    project = _project(tmp_path)
    ctx, _events = _ctx(project, channel=_channel(_fallback_settings()))
    _write_scenes(ctx, ["prompt one", "prompt two"])

    with pytest.raises(AvgError) as excinfo:
        VisualsPhase().run(ctx)

    # Exactly two passes total - one per provider - never a third attempt
    # on either one. This is the money-safety property: a bug that re-tried
    # the backup, or bounced back to the primary, would double the spend on
    # a run that was always going to fail.
    assert len(primary.calls) == 2
    assert len(backup.calls) == 2

    # The raised detail names the provider that produced the FINAL result
    # (the backup) and still records that the primary failed too, so the
    # take log does not lose the first failure's reason just because a
    # second one happened.
    assert "backup" in excinfo.value.message
    assert "primary" in excinfo.value.message
    assert not (project.folder / "images" / "manifest.json").exists()


def test_the_fallback_is_skipped_when_the_backup_names_the_same_provider(tmp_path, monkeypatch):
    """A backup configured identically to the primary would spend a whole
    second pass to fail (or succeed) the same way. The guard treats that
    configuration the same as no backup at all."""
    primary = _FakeImageProvider(fail_prompts=frozenset({"prompt one"}))
    monkeypatch.setattr(
        "avg.pipeline.phases.visuals._build_provider",
        lambda name, transport: primary,
    )

    project = _project(tmp_path)
    settings = _fallback_settings(image_provider_backup="primary", image_model_backup="primary-model")
    ctx, _events = _ctx(project, channel=_channel(settings))
    _write_scenes(ctx, ["prompt one"])

    with pytest.raises(AvgError):
        VisualsPhase().run(ctx)

    # Only the one pass over the one scene - no second attempt at all.
    assert len(primary.calls) == 1


def test_visuals_module_does_not_require_pillow_at_import_time():
    """Real, reproduced regression: `tools/dev_serve_linux.py` (and
    `build_flatpak.sh`'s "Launch from source") installs `.[dev]` only,
    deliberately never `.[dev,media]` - see that venv's own setup
    comment. `GET /api/settings` calls `suggest_output_root()`
    (`avg/config/settings.py`), which imports `avg.config.legacy_import`,
    which imports `avg.pipeline.registry` at module level - which imports
    every phase, including this one. A module-level `from PIL import
    Image` here therefore breaks that entire request path with a bare
    500 and no fallback, confirmed by actually building a fresh `.[dev]`-
    only venv and hitting the real route, not simulated from a guess.

    `Image` is only ever used inside `_is_valid_image` (pool-image
    validity, real-render-only) - it must be a lazy, function-local
    import, not a module-level one, so importing this module (therefore
    the whole registry, therefore the settings route) never requires
    Pillow to be installed at all.

    Run as a SUBPROCESS, deliberately, not via monkeypatching this
    process's own `sys.modules` in place: an earlier version of this
    test blocked "PIL" and force-reimported this module directly in the
    pytest process, and pytest's own monkeypatch teardown order left a
    SECOND, distinct module object behind in `sys.modules` after the
    test finished - a later test's `monkeypatch.setattr("avg.pipeline.
    phases.visuals._build_provider", ...)` then patched that stale
    duplicate instead of the one `avg.pipeline.registry.REGISTRY`
    actually holds a `VisualsPhase` reference into, so the real call
    reached a live (unpatched) provider path. A subprocess has no shared
    `sys.modules` with the test runner at all, so this class of leak is
    structurally impossible here.
    """
    import subprocess
    import sys

    script = (
        "import sys; sys.modules['PIL'] = None; "
        "import avg.pipeline.phases.visuals"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


from avg.config import provider_health


class _FakeQuotaFailingProvider:
    """Like `_FakeImageProvider` but raises a quota-classified AvgError,
    to exercise the cooldown-detail path in the fallback message."""

    name = "primary"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        self.calls.append((prompt, aspect_ratio, model))
        raise AvgError(ErrorKind.quota, "simulated quota exhaustion")


def test_fallback_message_includes_cooldown_detail_on_quota_failure(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    provider_health.record_cooldown(
        "primary", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1)
    )

    primary = _FakeQuotaFailingProvider()
    backup = _FakeImageProvider()
    providers = {"primary": primary, "backup": backup}
    monkeypatch.setattr(
        "avg.pipeline.phases.visuals._build_provider",
        lambda name, transport: providers[name],
    )

    project = _project(tmp_path)
    ctx, events = _ctx(project, channel=_channel(_fallback_settings()))
    _write_scenes(ctx, ["prompt one", "prompt two"])

    VisualsPhase().run(ctx)

    messages = [e.message for e in events if isinstance(e, ProgressEvent)]
    fallback_message = next(m for m in messages if m.startswith("Falling back to"))
    assert "quota" in fallback_message


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


class _FakeTimingVoiceProvider(_FakeVoiceProvider):
    """`_FakeVoiceProvider` plus `synthesize_with_timing` - one word per
    segment, spanning a fixed 0.0-1.0s window in that segment's own
    pre-clean timeline, so the offset math after concatenation is easy to
    hand-verify."""

    def synthesize_with_timing(self, text, *, voice_id, model):
        self.calls.append((text, voice_id, model))
        return f"fake-audio:{text}".encode(), [{"text": text.split()[0], "start": 0.0, "end": 1.0}]


class _FakePartialTimingVoiceProvider(_FakeVoiceProvider):
    """Has `synthesize_with_timing`, but its second call returns no words -
    the "whole-run, not partial" case."""

    def synthesize_with_timing(self, text, *, voice_id, model):
        self.calls.append((text, voice_id, model))
        words = None if len(self.calls) == 2 else [{"text": "word", "start": 0.0, "end": 1.0}]
        return f"fake-audio:{text}".encode(), words


def test_voice_writes_word_timings_when_the_provider_supports_it(tmp_path, monkeypatch):
    fake = _FakeTimingVoiceProvider()
    _patch_voice_collaborators(monkeypatch, fake)
    monkeypatch.setattr("avg.pipeline.phases.voice.audio_duration_seconds", lambda path: 10.0)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "First half. [pause] Second half.")

    VoicePhase().run(ctx)

    timings = read_json(script_dir(ctx).parent / "audio" / "word_timings.json")
    assert timings is not None
    words = timings["words"]
    assert [w["text"] for w in words] == ["First", "Second"]
    assert words[0]["start"] == pytest.approx(0.0)
    assert words[0]["end"] == pytest.approx(1.0)
    assert words[1]["start"] > 10.0  # strictly after segment 1's own duration


def test_voice_does_not_write_word_timings_without_provider_support(tmp_path, monkeypatch):
    fake = _FakeVoiceProvider()  # no synthesize_with_timing
    _patch_voice_collaborators(monkeypatch, fake)
    monkeypatch.setattr("avg.pipeline.phases.voice.audio_duration_seconds", lambda path: 10.0)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "Some narration.")

    VoicePhase().run(ctx)

    assert not (script_dir(ctx).parent / "audio" / "word_timings.json").exists()


def test_voice_writes_no_word_timings_when_any_segment_lacks_them(tmp_path, monkeypatch):
    fake = _FakePartialTimingVoiceProvider()
    _patch_voice_collaborators(monkeypatch, fake)
    monkeypatch.setattr("avg.pipeline.phases.voice.audio_duration_seconds", lambda path: 10.0)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "First half. [pause] Second half.")

    VoicePhase().run(ctx)  # must not raise - a missing timing degrades, not a phase failure

    assert not (script_dir(ctx).parent / "audio" / "word_timings.json").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs real ffmpeg")
def test_clean_segment_really_remaps_word_timings_through_removed_silence(tmp_path):
    """Unmocked: real pydub, real ffmpeg, the real vendored
    `remove_silences_core`. 0.5s tone + 1.0s silence + 0.5s tone; the
    second word sits after the gap, so it must shift earlier by exactly
    what was actually removed."""
    from pydub import AudioSegment
    from pydub.generators import Sine

    from avg.pipeline.phases.voice import _clean_segment

    raw_path = tmp_path / "raw.mp3"
    clean_path = tmp_path / "clean.mp3"
    scene_map_path = tmp_path / "word_map.json"
    tone = Sine(440).to_audio_segment(duration=500).apply_gain(-10)
    (tone + AudioSegment.silent(duration=1000) + tone).export(raw_path, format="mp3")

    result = _clean_segment(
        None,
        segment_index=1,
        raw_path=raw_path,
        clean_path=clean_path,
        aggressiveness="medium",
        max_removable_pct=100.0,
        word_timings=[
            {"text": "hello", "start": 0.1, "end": 0.4},
            {"text": "world", "start": 1.6, "end": 1.9},
        ],
        scene_map_path=scene_map_path,
    )

    assert result is not None
    assert result[0]["start"] == pytest.approx(0.1, abs=0.05)  # before the gap: unchanged
    assert result[1]["start"] < 1.6 - 0.5  # after the gap: shifted earlier by the removed silence
    assert result[1]["end"] - result[1]["start"] == pytest.approx(0.3, abs=0.05)  # duration preserved
