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

import pytest

from avg.config.channels import Channel
from avg.core.errors import AvgError, ErrorKind
from avg.core.events import ProgressEvent
from avg.core.models import ProjectDoc
from avg.pipeline.phase import CancellationToken, PhaseContext
from avg.pipeline.phases._common import read_json, script_dir, write_json
from avg.pipeline.phases.visuals import VisualsPhase
from avg.pipeline.phases.voice import VoicePhase

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
    ctx = PhaseContext(
        project=project,
        channel=channel or _channel(),
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
