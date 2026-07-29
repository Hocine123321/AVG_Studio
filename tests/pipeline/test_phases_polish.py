"""Tests for the two "polish" phases wired into ids 5-6 of
`avg.pipeline.registry.REGISTRY`: `avg.pipeline.phases.director.
Director2Phase` and `avg.pipeline.phases.captions.ScreenTextPhase`.

No live API calls anywhere in this file:
  - Director II's text-model call is mocked the same way
    `tests/pipeline/test_phases_text.py` mocks every AI-writing phase -
    monkeypatching the phase module's own imported `generate_text` name,
    never driving the router/transport layer underneath it.
  - Captions' AssemblyAI integration is mocked at TWO levels, mirroring
    `tests/providers/test_images.py`'s dual pattern for Stable Horde (the
    one other polling-based provider in this codebase): a low-level test
    drives `_assemblyai_poll` against `httpx.MockTransport` (proves the
    upload/submit/poll wire shape is correct without ever touching the
    network), and the phase-level tests monkeypatch
    `_transcribe_with_assemblyai` wholesale (the same "patch the phase
    module's own seam" convention `avg.pipeline.phases.visuals`/`.voice`
    established for their own providers).
  - `avg.pipeline.phases._common.audio_duration_seconds` is always
    monkeypatched to a fixed value - `pydub` is not installed in this
    project's lean test venv (`.venv-build`; see
    `tests/pipeline/test_phases_media.py`'s identical note), and no test
    here needs a real mp3 to prove any of this phase's behaviour.

Four invariants this file exists to prove (the task brief's own words):
  1. `direction_plan.json`/`card_plan.json`/`captions.ass` match the real,
     working legacy project's schemas field-for-field (read directly off
     `D:\\AI_Video_Gen\\output\\...\\render\\`, not reinvented).
  2. Captions are produced with NO AssemblyAI key configured at all.
  3. The channel's `font_family` setting reaches the `.ass` file's
     `Style:` line - the one thing captions and takeover cards must never
     drift on again (this repo's `global-font` memory note).
  4. A caption cue is suppressed when it overlaps a takeover card's
     window - a card owns the whole screen while it is up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from avg.config.channels import Channel
from avg.core.errors import AvgError, ErrorKind
from avg.core.events import ProgressEvent
from avg.core.models import ProjectDoc
from avg.pipeline.phase import CancellationToken, PhaseContext
from avg.pipeline.phases import captions as captions_module
from avg.pipeline.phases._common import read_json, render_dir, resolve_font_family, script_dir, write_json
from avg.pipeline.phases.captions import ScreenTextPhase
from avg.pipeline.phases.director import Director2Phase
from avg.providers.text.router import TextResult

# ── shared test scaffolding (mirrors test_phases_text.py/test_phases_media.py) ──


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


def _write_scenes(ctx: PhaseContext, texts: list[str]) -> None:
    write_json(
        script_dir(ctx) / "scenes.json",
        [{"index": i + 1, "text": t, "image_prompt": "", "label": "Scene"} for i, t in enumerate(texts)],
    )


class _QueueGenerator:
    """A fake `generate_text(prompt, phase, *, config)` replaying canned
    replies in call order, and recording every `(phase, prompt)` it saw -
    identical to `test_phases_text.py`'s own fixture."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, prompt: str, phase: str, *, config: Any) -> TextResult:
        self.calls.append((phase, prompt))
        if not self._replies:
            raise AssertionError("fake generator ran out of canned replies")
        text = self._replies.pop(0)
        return TextResult(text=text, provider="fake", model="fake")


# ═══════════════════════════════════════════════════════════════════════
# Director2Phase
# ═══════════════════════════════════════════════════════════════════════


def test_edit_plan_derives_from_the_card_plan():
    """One owner for a card's text. card_plan.json decides what a card says;
    edit_plan.json is the same information in the shape cmd_overlay_render
    reads (`plan["overlays"]`, each entry carrying element/scene/duration/
    transition_in). A second table would let the two disagree about a card
    the user is looking at."""
    from avg.pipeline.phases.director import build_edit_plan

    plan = build_edit_plan(
        {"cards": [{"element": "focused", "scene": 2, "text": "Why stripes?", "accent": "#e08a4e"}]},
        {"scenes": [{"scene": 2, "transition_in": "fade"}]},
    )

    assert plan["overlays"] == [
        {
            "element": "focused",
            "scene": 2,
            "text": "Why stripes?",
            "accent": "#e08a4e",
            "duration": 2.5,
            "transition_in": "fade",
            "transition_out": "fade",
            "ride_motion": True,
        }
    ]


def test_edit_plan_prefers_a_cards_own_resolved_window():
    """Once Screen Text has added start/end, the card's real length is
    known and must win over the default - otherwise a card timed to two
    words sits on screen for the full default."""
    from avg.pipeline.phases.director import build_edit_plan

    plan = build_edit_plan(
        {"cards": [{"element": "focused", "scene": 1, "text": "Why?", "start": 4.0, "end": 5.5}]},
        {},
    )

    assert plan["overlays"][0]["duration"] == 1.5


def test_director2_writes_scene_timings_and_edit_plan(tmp_path, monkeypatch):
    """The vendored timing pass is REUSED rather than reimplemented: it is
    what Render itself would otherwise run, and two timing implementations
    would put images and captions on different clocks."""
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
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _events = _ctx(project)
    _write_scenes(ctx, ["Scene one ((why?)) text.", "Scene two text."])
    write_json(script_dir(ctx) / "timeline.json", [
        {"type": "scene", "scene": 1},
        {"type": "scene", "scene": 2},
    ])
    write_json(project.folder / "audio" / "scene_audio_map.json", {
        "total_duration": 10.0,
        "scenes": [
            {"scene": 1, "start": 0.0, "end": 6.0, "duration": 6.0},
            {"scene": 2, "start": 6.0, "end": 10.0, "duration": 4.0},
        ],
    })

    Director2Phase().run(ctx)

    timings = read_json(render_dir(ctx) / "scene_timings.json")
    assert [t["scene"] for t in timings if t["type"] == "scene"] == [1, 2]
    assert timings[0]["duration"] == 6.0
    assert read_json(render_dir(ctx) / "edit_plan.json")["overlays"][0]["scene"] == 1


def test_director2_survives_a_missing_audio_map(tmp_path, monkeypatch):
    """No audio map means no exact windows, and the render recomputes them
    blind - which is what happened before the file existed at all. Worth a
    degradation, not a failed phase whose real output is already on disk."""
    reply = json.dumps({"version": 1, "grade": {"look": "warm"}, "scenes": [
        {"scene": 1, "motion": {"type": "zoom_in", "intensity": 0.3, "ease": "ease_out_cubic"},
         "transition_in": "fade"},
    ]})
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _events = _ctx(project)
    _write_scenes(ctx, ["Scene one text."])

    Director2Phase().run(ctx)  # must NOT raise

    assert not (render_dir(ctx) / "scene_timings.json").exists()
    assert (render_dir(ctx) / "edit_plan.json").is_file()


def test_director2_inputs_ready_false_without_scenes_json(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    assert Director2Phase().inputs_ready(ctx) is False


def test_director2_inputs_ready_true_with_scenes_json(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one text.", "Scene two text."])
    assert Director2Phase().inputs_ready(ctx) is True


def test_director2_raises_when_scenes_json_is_missing(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    with pytest.raises(AvgError) as excinfo:
        Director2Phase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug


def test_director2_writes_direction_plan_matching_the_real_schema(tmp_path, monkeypatch):
    """Field-for-field against the real project's `direction_plan.json`
    (this slice's task brief) - `version`, top-level `grade.look`, and each
    scene's `motion.{type,intensity,ease}`/`transition_in`/`grade`."""
    reply = json.dumps({
        "version": 1,
        "grade": {"look": "cinematic"},
        "scenes": [
            {"scene": 1, "motion": {"type": "zoom_out", "intensity": 0.3, "ease": "ease_out_cubic"},
             "transition_in": "fade"},
            {"scene": 2, "motion": {"type": "pan_up", "intensity": 0.4, "ease": "ease_out_cubic"},
             "transition_in": "cut"},
        ],
    })
    fake = _QueueGenerator([reply])
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", fake)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    _write_scenes(ctx, ["Scene one text.", "Scene two text."])
    Director2Phase().run(ctx)

    plan = read_json(render_dir(ctx) / "direction_plan.json")
    assert plan == {
        "version": 1,
        "grade": {"look": "cinematic"},
        "scenes": [
            {"scene": 1, "motion": {"type": "zoom_out", "intensity": 0.3, "ease": "ease_out_cubic"},
             "transition_in": "fade", "grade": {"look": "cinematic"}},
            {"scene": 2, "motion": {"type": "pan_up", "intensity": 0.4, "ease": "ease_out_cubic"},
             "transition_in": "cut", "grade": {"look": "cinematic"}},
        ],
    }
    assert fake.calls[0][0] == "director2"
    assert any(isinstance(e, ProgressEvent) for e in events)


def test_director2_degrades_unknown_vocabulary_to_safe_defaults(tmp_path, monkeypatch):
    reply = json.dumps({
        "grade": {"look": "not-a-real-look"},
        "scenes": [
            {"scene": 1, "motion": {"type": "sideways", "intensity": 5.0, "ease": "bounce"},
             "transition_in": "explode"},
        ],
    })
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one text."])
    Director2Phase().run(ctx)

    plan = read_json(render_dir(ctx) / "direction_plan.json")
    assert plan["grade"] == {"look": "none"}
    assert plan["scenes"][0]["motion"] == {"type": "zoom_in", "intensity": 1.0, "ease": "ease_out_cubic"}
    assert plan["scenes"][0]["transition_in"] == "cut"


def test_director2_scene_naming_an_unknown_scene_number_is_dropped(tmp_path, monkeypatch):
    reply = json.dumps({"grade": {"look": "warm"}, "scenes": [{"scene": 99, "motion": {}, "transition_in": "cut"}]})
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one.", "Scene two."])
    Director2Phase().run(ctx)

    plan = read_json(render_dir(ctx) / "direction_plan.json")
    # Nothing survived (scene 99 doesn't exist) - degrades to one safe
    # default entry per existing scene, matching Director I's own leniency.
    assert [s["scene"] for s in plan["scenes"]] == [1, 2]


def test_director2_writes_focused_cards_from_question_markers(tmp_path, monkeypatch):
    reply = json.dumps({"grade": {"look": "none"}, "scenes": [{"scene": 1, "motion": {}, "transition_in": "cut"}]})
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["((How did a society function in total darkness?)) It just did."])
    Director2Phase().run(ctx)

    card_plan = read_json(render_dir(ctx) / "card_plan.json")
    assert card_plan == {
        "cards": [
            {"element": "focused", "scene": 1, "text": "How did a society function in total darkness?",
             "accent": "accent1"},
        ]
    }


def test_director2_card_plan_is_empty_without_any_question_markers(tmp_path, monkeypatch):
    reply = json.dumps({"grade": {"look": "none"}, "scenes": [{"scene": 1, "motion": {}, "transition_in": "cut"}]})
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["A perfectly ordinary declarative scene."])
    Director2Phase().run(ctx)

    assert read_json(render_dir(ctx) / "card_plan.json") == {"cards": []}


def test_director2_caps_card_frequency_to_roughly_one_per_three_scenes(tmp_path, monkeypatch):
    reply = json.dumps({
        "grade": {"look": "none"},
        "scenes": [{"scene": i + 1, "motion": {}, "transition_in": "cut"} for i in range(6)],
    })
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    # 6 scenes, every one carrying a question marker - the frequency cap
    # (roughly 1 per 3 scenes) must still cut this down, not pass all 6.
    _write_scenes(ctx, [f"((Question number {i}?)) filler." for i in range(6)])
    Director2Phase().run(ctx)

    card_plan = read_json(render_dir(ctx) / "card_plan.json")
    assert len(card_plan["cards"]) == 2  # floor(6/3)


# ═══════════════════════════════════════════════════════════════════════
# ScreenTextPhase
# ═══════════════════════════════════════════════════════════════════════

_FIXED_DURATION_SECONDS = 12.0


def _patch_audio_duration(monkeypatch, seconds: float = _FIXED_DURATION_SECONDS) -> None:
    monkeypatch.setattr(captions_module, "audio_duration_seconds", lambda path: seconds)


def _write_audio(ctx: PhaseContext) -> None:
    (Path(ctx.project.folder) / "audio" / "optimized_voice.mp3").write_bytes(b"fake-mp3-bytes")


def test_captions_inputs_ready_false_without_audio(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    assert ScreenTextPhase().inputs_ready(ctx) is False


def test_captions_inputs_ready_true_with_audio(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_audio(ctx)
    assert ScreenTextPhase().inputs_ready(ctx) is True


def test_captions_raises_when_audio_is_missing(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    with pytest.raises(AvgError) as excinfo:
        ScreenTextPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug


def test_captions_raises_when_scenes_json_is_missing(tmp_path, monkeypatch):
    _patch_audio_duration(monkeypatch)
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_audio(ctx)
    with pytest.raises(AvgError) as excinfo:
        ScreenTextPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug


def test_captions_produced_with_no_assemblyai_key(tmp_path, monkeypatch):
    """The named invariant: a user without an AssemblyAI key still gets
    usable, correctly-shaped captions - never an empty/missing file."""
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path, aspect_ratio="16:9")
    ctx, events = _ctx(project)
    _write_audio(ctx)
    _write_scenes(ctx, ["In nineteen eighty seven a wall fell", "Golf hotel india juliet kilo lima"])

    ScreenTextPhase().run(ctx)

    ass_path = render_dir(ctx) / "captions.ass"
    assert ass_path.exists()
    content = ass_path.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "Dialogue: 0," in content
    assert any(isinstance(e, ProgressEvent) for e in events)


def test_captions_ass_header_matches_the_real_schema(tmp_path, monkeypatch):
    """Field-for-field against the real project's `captions.ass` header
    (this slice's task brief): the exact `[Script Info]`/`[V4+ Styles]`/
    `[Events]` block shape, `PlayResX`/`PlayResY` from the project's own
    aspect ratio, and the per-cue pop-in override kept verbatim."""
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path, aspect_ratio="16:9", short_edge=1080)
    ctx, _ = _ctx(project, channel=_channel({"font_family": "Impact"}))
    _write_audio(ctx)
    _write_scenes(ctx, ["In nineteen eighty seven everything changed forever"])

    ScreenTextPhase().run(ctx)
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")

    assert "[Script Info]\nTitle: AVG Captions\nScriptType: v4.00+\n" in content
    assert "PlayResX: 1920\nPlayResY: 1080\nScaledBorderAndShadow: yes\n" in content
    assert (
        "Style: Default,Impact,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,3.00,0,2,10,10,40,1" in content
    )
    assert (
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text" in content
    )
    # The pop-in feel (this slice's task brief: "keep it, it is the app's
    # caption feel") must reach every emitted cue verbatim.
    assert r"{\fscx65\fscy65\t(0,90,0.7,\fscx108\fscy108)\t(90,190,\fscx100\fscy100)}" in content


def test_captions_font_family_reaches_the_ass_style_line(tmp_path, monkeypatch):
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, _ = _ctx(project, channel=_channel({"font_family": "Bebas Neue"}))
    _write_audio(ctx)
    _write_scenes(ctx, ["A single simple scene of narration text."])

    ScreenTextPhase().run(ctx)
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")
    assert "Style: Default,Bebas Neue," in content


def test_captions_font_family_defaults_to_arial_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)  # no font_family set
    _write_audio(ctx)
    _write_scenes(ctx, ["A single simple scene of narration text."])

    ScreenTextPhase().run(ctx)
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")
    assert "Style: Default,Arial," in content


def test_resolve_font_family_extracts_family_name_from_a_font_file_path():
    ctx_channel = _channel({"font_family": r"C:\Windows\Fonts\Montserrat-Bold.ttf"})
    project = ProjectDoc(id="p", title="t", folder=Path("."), created="2026-07-26T00:00:00")
    ctx = PhaseContext(project=project, channel=ctx_channel, router=None, emit=lambda e: None, cancel_token=CancellationToken())
    assert resolve_font_family(ctx) == "Montserrat-Bold"


def test_captions_suppressed_during_a_takeover_card_window(tmp_path, monkeypatch):
    """The other named invariant: a caption cue overlapping a planned
    takeover card's window must never appear - the card owns the screen."""
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_audio(ctx)
    # Exactly 6 words per scene (this phase's cue size) so each scene maps
    # to exactly one caption cue, with no cross-scene cue to complicate
    # the assertion.
    scene1 = "Alpha bravo charlie delta echo foxtrot"
    scene2 = "Golf hotel india juliet kilo lima"
    _write_scenes(ctx, [scene1, scene2])

    write_json(render_dir(ctx) / "card_plan.json", {
        "cards": [{"element": "focused", "scene": 1, "text": "irrelevant", "accent": "accent1"}]
    })

    ScreenTextPhase().run(ctx)

    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")
    dialogue_lines = [ln for ln in content.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue_lines) == 1
    assert "Golf" in dialogue_lines[0]
    assert "Alpha" not in content  # scene 1's cue was suppressed entirely

    card_plan = read_json(render_dir(ctx) / "card_plan.json")
    card = card_plan["cards"][0]
    assert "start" in card and "end" in card
    assert card["start"] == 0.0
    assert card["end"] > 0.0


def test_captions_with_assemblyai_key_uses_real_transcription(tmp_path, monkeypatch):
    monkeypatch.setattr(captions_module, "has_key", lambda provider: True)
    monkeypatch.setattr(captions_module, "get_key", lambda provider: "aai-fake-key")
    _patch_audio_duration(monkeypatch)

    calls: list[tuple[bytes, str]] = []

    def fake_transcribe(audio_bytes: bytes, api_key: str) -> list[dict]:
        calls.append((audio_bytes, api_key))
        return [
            {"text": "Real", "start": 0.0, "end": 0.3},
            {"text": "transcript", "start": 0.3, "end": 0.9},
        ]

    monkeypatch.setattr(captions_module, "_transcribe_with_assemblyai", fake_transcribe)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    _write_audio(ctx)
    _write_scenes(ctx, ["Some narration text goes here."])

    ScreenTextPhase().run(ctx)

    assert calls == [(b"fake-mp3-bytes", "aai-fake-key")]
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")
    assert "Real transcript" in content
    assert any("AssemblyAI" in e.message for e in events if isinstance(e, ProgressEvent))


def test_captions_degrades_to_estimated_timing_when_assemblyai_call_fails(tmp_path, monkeypatch):
    """A configured key that fails at call time (network error, expired
    key, timeout, ...) must still produce usable captions, never raise."""
    monkeypatch.setattr(captions_module, "has_key", lambda provider: True)
    monkeypatch.setattr(captions_module, "get_key", lambda provider: "aai-fake-key")

    def failing_transcribe(audio_bytes: bytes, api_key: str) -> list[dict]:
        raise captions_module._AssemblyAIError("simulated network failure")

    monkeypatch.setattr(captions_module, "_transcribe_with_assemblyai", failing_transcribe)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    _write_audio(ctx)
    _write_scenes(ctx, ["Some narration text goes here regardless."])

    ScreenTextPhase().run(ctx)  # must not raise

    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")
    assert "Dialogue: 0," in content
    assert any("estimated timing" in e.message for e in events if isinstance(e, ProgressEvent))


# ── _assemblyai_poll: wire-shape test against a local MockTransport ──────
# (mirrors tests/providers/test_images.py's Stable Horde poll-loop tests -
# the one other polling-based provider in this codebase, tested the same
# way "deliberately", per that provider's own module docstring)


def test_assemblyai_poll_uploads_submits_and_polls_until_completed(monkeypatch):
    poll_hits = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/upload"):
            assert request.content == b"fake-audio-bytes"
            return httpx.Response(200, json={"upload_url": "https://cdn.example/audio123"})
        if path.endswith("/transcript"):
            assert json.loads(request.content)["audio_url"] == "https://cdn.example/audio123"
            return httpx.Response(200, json={"id": "tid1", "status": "queued"})
        if path.endswith("/transcript/tid1"):
            poll_hits["count"] += 1
            if poll_hits["count"] == 1:
                return httpx.Response(200, json={"status": "processing"})
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "words": [
                        {"text": "Hello", "start": 0, "end": 400},
                        {"text": "world", "start": 400, "end": 800},
                    ],
                },
            )
        raise AssertionError(f"unexpected request to {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps: list[float] = []
    words = captions_module._assemblyai_poll(
        b"fake-audio-bytes", "key123", client=client, sleep=sleeps.append
    )

    assert words == [
        {"text": "Hello", "start": 0.0, "end": 0.4},
        {"text": "world", "start": 0.4, "end": 0.8},
    ]
    assert sleeps == [captions_module._ASSEMBLYAI_POLL_INTERVAL_SECONDS]
    assert poll_hits["count"] == 2


def test_assemblyai_poll_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"upload_url": "https://cdn.example/audio123"})
        if request.url.path.endswith("/transcript"):
            return httpx.Response(200, json={"id": "tid1", "status": "queued"})
        return httpx.Response(200, json={"status": "error", "error": "bad audio format"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(captions_module._AssemblyAIError):
        captions_module._assemblyai_poll(b"bytes", "key", client=client, sleep=lambda s: None)


def test_assemblyai_poll_raises_on_upload_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(captions_module._AssemblyAIError):
        captions_module._assemblyai_poll(b"bytes", "key", client=client, sleep=lambda s: None)


def test_assemblyai_poll_times_out_when_never_completed(monkeypatch):
    monkeypatch.setattr(captions_module, "_ASSEMBLYAI_MAX_POLLS", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"upload_url": "https://cdn.example/a"})
        if request.url.path.endswith("/transcript"):
            return httpx.Response(200, json={"id": "tid1", "status": "queued"})
        return httpx.Response(200, json={"status": "processing"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(captions_module._AssemblyAIError):
        captions_module._assemblyai_poll(b"bytes", "key", client=client, sleep=lambda s: None)
