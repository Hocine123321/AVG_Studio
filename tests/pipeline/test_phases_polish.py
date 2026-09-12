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
from avg.core.settings_schema import resolve_channel_settings
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


def test_draw_overlay_assets_inherits_accent_colors_from_the_app_wide_defaults(tmp_path, monkeypatch):
    """Slice 19 Task 5: `_draw_overlay_assets` reads `ctx.channel_values`,
    not `ctx.channel.settings` directly - so `overlay_accent1`/`accent2`/
    `highlight` set only in the app-wide defaults document (never owned by
    the channel) still reach `write_layers`."""
    from avg.pipeline.phases.director import _draw_overlay_assets

    monkeypatch.setattr("avg.media.vendor.avg._target_size", lambda ar, se: (1080, 1920))
    captured: list[tuple] = []
    monkeypatch.setattr(
        "avg.media.overlays.write_layers",
        lambda *args: captured.append(args) or 1,
    )

    project = _project(tmp_path)
    channel = _channel({})  # owns none of the three overlay colors
    ctx, _ = _ctx(project, channel=channel)
    ctx.channel_values = resolve_channel_settings(
        channel.settings,
        {"overlay_accent1": "#e08a4e", "overlay_accent2": "#aebf92", "overlay_highlight": "#ffffff"},
    )
    rdir = render_dir(ctx)
    rdir.mkdir(parents=True, exist_ok=True)

    _draw_overlay_assets(ctx, rdir, {"overlays": []})

    accent1, accent2, highlight = captured[0][6], captured[0][7], captured[0][8]
    assert (accent1, accent2, highlight) == ("#e08a4e", "#aebf92", "#ffffff")


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
    """Regression: brand_preset's SettingSpec defaults to 'modern_dark',
    not blank - _ctx(project)'s default channel therefore always resolves
    a real preset id (the plan's own test assumed an unreachable "no
    brand_preset set" state). Exercising the actual empty-value branch
    means setting channel_values['brand_preset'] to '' directly, which is
    the one way that branch is ever reached - a raw dict channel_values
    built by hand rather than through resolve_channel_settings, or a
    future SettingSpec default change."""
    from avg.pipeline.phases.director import _draw_overlay_assets

    monkeypatch.setattr("avg.media.vendor.avg._target_size", lambda ar, se: (1080, 1920))
    captured: list[tuple] = []
    monkeypatch.setattr("avg.media.overlays.write_layers", lambda *args: captured.append(args) or 1)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    ctx.channel_values["brand_preset"] = ""
    rdir = render_dir(ctx)
    rdir.mkdir(parents=True, exist_ok=True)

    _draw_overlay_assets(ctx, rdir, {"overlays": []})

    assert captured[0][9] is None



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


# ═══════════════════════════════════════════════════════════════════════
# captions.py module-level: _group_cues, _animated_text, _ass_color_tag
# ═══════════════════════════════════════════════════════════════════════

def test_group_cues_keeps_each_cues_own_words():
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
    # word 2 starts exactly when word 1 ends (both fixture words are
    # contiguous, 0.5s), so the bare "\t(500,500," substring legitimately
    # matches BOTH word 1's snap-back-to-base AND word 2's snap-to-accent
    # - correct, expected behaviour for back-to-back words, not a bug.
    # "\r\t(500,500," is what actually isolates word 2's own start
    # transform, matching this test's own intent ("second word's start").
    assert body.count(r"\r\t(500,500,") == 1  # second word's start, in ms


def test_ass_color_tag_strips_the_leading_alpha_byte():
    from avg.pipeline.phases.captions import _ass_color_tag

    assert _ass_color_tag("&H00FFFFFF") == r"\c&HFFFFFF&"
    assert _ass_color_tag("&H0000FFFF") == r"\c&H00FFFF&"


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
    # This test is specifically about the pop style's exact override
    # shape - captions_animation_style must be requested explicitly, not
    # relied on as an (incorrect, pre-fix) default.
    ctx, _ = _ctx(project, channel=_channel({"font_family": "Impact", "captions_animation_style": "pop"}))
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


def test_captions_font_family_inherits_from_the_app_wide_defaults(tmp_path, monkeypatch):
    """Slice 19 Task 5: `resolve_font_family` (`_common.py`) reads
    `ctx.channel_values`, not `ctx.channel.settings.get(...)` directly - so
    a channel that owns no `font_family` at all still uses whatever the
    app-wide defaults document supplies, not the hardcoded Arial fallback
    the previous test (`..._defaults_to_arial_when_unset`) exercises when
    NEITHER the channel nor the defaults have an opinion."""
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    channel = _channel({})  # owns no font_family
    ctx, _ = _ctx(project, channel=channel)
    ctx.channel_values = resolve_channel_settings(channel.settings, {"font_family": "Bebas Neue"})
    _write_audio(ctx)
    _write_scenes(ctx, ["A single simple scene of narration text."])

    ScreenTextPhase().run(ctx)
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")
    assert "Style: Default,Bebas Neue," in content


def test_captions_font_size_and_position_inherit_from_the_app_wide_defaults(tmp_path, monkeypatch):
    """Same migration, the other two captions fields
    (`captions_font_size`/`captions_position`) read in `captions.py` itself
    rather than `_common.py`."""
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    channel = _channel({})  # owns neither key
    ctx, _ = _ctx(project, channel=channel)
    ctx.channel_values = resolve_channel_settings(
        channel.settings, {"captions_font_size": 72, "captions_position": "top"}
    )
    _write_audio(ctx)
    _write_scenes(ctx, ["A single simple scene of narration text."])

    ScreenTextPhase().run(ctx)
    content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")
    assert "Style: Default,Arial,72," in content
    # `position="top"` maps to ASS alignment 8 (`_ALIGNMENT_BY_POSITION`) -
    # the Style line's Alignment field, right after the fixed style values.
    style_line = next(line for line in content.splitlines() if line.startswith("Style: Default,"))
    assert style_line.split(",")[18] == "8"


def test_resolve_font_family_extracts_family_name_from_a_font_file_path():
    ctx_channel = _channel({"font_family": r"C:\Windows\Fonts\Montserrat-Bold.ttf"})
    project = ProjectDoc(id="p", title="t", folder=Path("."), created="2026-07-26T00:00:00")
    ctx = PhaseContext(
        project=project,
        channel=ctx_channel,
        channel_values=resolve_channel_settings(ctx_channel.settings, {}),
        router=None,
        emit=lambda e: None,
        cancel_token=CancellationToken(),
    )
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


def test_the_transcript_cache_is_written_in_milliseconds(tmp_path):
    """This phase works in SECONDS; the vendored burn pass works in
    MILLISECONDS (`cue[0]['start']/1000` at `avg/media/vendor/avg.py:3663`).
    Handing it seconds would stack the whole track's captions inside the
    first few frames."""
    from avg.pipeline.phases.captions import seed_transcript_cache

    audio = tmp_path / "audio" / "optimized_voice.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"stable bytes standing in for an mp3")

    path = seed_transcript_cache(tmp_path, audio, "estimated", [{"text": "Hi", "start": 1.5, "end": 2.0}])

    assert path is not None
    assert json.loads(path.read_text(encoding="utf-8")) == [{"text": "Hi", "start": 1500, "end": 2000}]


def test_the_transcript_cache_key_is_the_vendors_own(tmp_path):
    """A key we invented would never be read. The burn pass looks for
    exactly `transcript_<_seg_cache_key("transcript", 0, provider,
    audio)>.json` under render/_cache (`avg.py:3557`)."""
    from avg.media.vendor.avg import _seg_cache_key
    from avg.pipeline.phases.captions import seed_transcript_cache

    audio = tmp_path / "audio" / "optimized_voice.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"stable bytes")

    path = seed_transcript_cache(tmp_path, audio, "assemblyai", [{"text": "Hi", "start": 0.0, "end": 0.5}])

    assert path is not None
    assert path.name == f"transcript_{_seg_cache_key('transcript', 0, 'assemblyai', str(audio))}.json"
    assert path.parent == tmp_path / "render" / "_cache"


def test_screen_text_seeds_the_cache_and_records_the_provider(tmp_path, monkeypatch):
    """The render must pass the SAME provider string back, because it is part
    of the cache key - so the phase records which one produced the words
    rather than leaving Render to guess."""
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_audio(ctx)
    _write_scenes(ctx, ["Scene one text.", "Scene two text."])

    ScreenTextPhase().run(ctx)

    assert read_json(render_dir(ctx) / "_transcript_source.json") == {"provider": "estimated"}
    cached = list((Path(project.folder) / "render" / "_cache").glob("transcript_*.json"))
    assert len(cached) == 1
    words = json.loads(cached[0].read_text(encoding="utf-8"))
    assert words and all(isinstance(w["start"], int) and isinstance(w["end"], int) for w in words)


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


def test_the_sample_ass_resolves_a_blank_font_and_zero_size_like_the_real_phase(tmp_path, monkeypatch):
    """Slice 14 review, Important 1: the shipped defaults are
    `font_family=""`/`captions_font_size=0` (`avg.core.settings_schema`).
    Before this fix, the preview sent those raw values straight to libass's
    `Style:` line, which drew nothing - a blank frame on a fresh install,
    the exact lie the libass-not-CSS decision exists to prevent. `sample_ass`
    must resolve a blank/zero value exactly the way `ScreenTextPhase.run`
    does, so the two `Style:` lines match field-for-field."""
    from avg.pipeline.phases.captions import _ass_header, sample_ass

    # What the preview now produces for the blank/zero defaults.
    preview_text = sample_ass(1080, 1920, "", 0, "bottom")
    preview_style = next(line for line in preview_text.splitlines() if line.startswith("Style:"))
    assert preview_style == "Style: Default,Arial,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3.00,0,2,10,10,40,1"

    # What the real phase produces for the same blank/zero settings.
    monkeypatch.setattr(captions_module, "has_key", lambda provider: False)
    _patch_audio_duration(monkeypatch)
    project = _project(tmp_path)
    ctx, _ = _ctx(project, channel=_channel({"font_family": "", "captions_font_size": 0}))
    _write_audio(ctx)
    _write_scenes(ctx, ["A single simple scene of narration text."])
    ScreenTextPhase().run(ctx)
    real_content = (render_dir(ctx) / "captions.ass").read_text(encoding="utf-8")
    real_style = next(line for line in real_content.splitlines() if line.startswith("Style:"))

    assert preview_style == real_style
    # And it matches `_ass_header` built from the resolved values directly -
    # the same construction both paths share.
    assert preview_text.startswith(_ass_header(1080, 1920, "Arial", 60, "bottom"))


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
    calls = _QueueGenerator([direction_reply])

    def _flaky(*args, **kwargs):
        # args[1] is the phase key (generate_text(prompt, phase, *, config)) -
        # let the real director2 call through, fail only the explainer one.
        if args and args[1] == "director2_explainer":
            raise RuntimeError("provider unavailable")
        return calls(*args, **kwargs)

    monkeypatch.setattr(director_module, "generate_text", _flaky)

    project = _project(tmp_path)
    channel = _channel({"explainer_enabled": True})
    ctx, _ = _ctx(project, channel=channel)
    _write_scenes(ctx, [f"Scene {i}." for i in range(1, 7)])

    director_module.Director2Phase().run(ctx)  # must not raise

    card_plan = read_json(render_dir(ctx) / "card_plan.json")
    assert card_plan["cards"] == []


def test_build_card_plan_takeover_cards_disabled_returns_nothing():
    from avg.pipeline.phases.director import _build_card_plan

    scenes = [{"text": "So ((why stripes?)) nobody asks."}]
    plan = _build_card_plan(
        scenes, takeover_cards_enabled=False, explainer_cards=[{"element": "explainer", "scene": 1, "text": "x", "accent": "accent1"}]
    )
    assert plan == {"cards": []}


def test_build_card_plan_merges_explainer_cards_on_untouched_scenes():
    from avg.pipeline.phases.director import _build_card_plan

    # 6 scenes so cap = max(1, 6 // 3) = 2 comfortably fits both candidate
    # cards - this test is about merge/dedup, not the cap (that's
    # test_build_card_plan_cap_applies_to_the_merged_set, below); a
    # smaller fixture would let the cap silently eat the second card and
    # test the wrong thing.
    scenes = [{"text": "So ((why stripes?)) nobody asks."}, {"text": "Plain scene, no marker."}] + [
        {"text": "Plain."}
    ] * 4
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
