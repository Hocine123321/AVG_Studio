"""Tests for the three real text-writing phases wired into ids 0-2 of
`avg.pipeline.registry.REGISTRY`: `avg.pipeline.phases.writing`, `.script`,
and `.director1`.

No live API calls: `avg.providers.text.router.generate` is mocked directly
at each phase module's own imported name (`generate_text`) rather than by
driving `avg.providers.transport.Transport` - simpler here since a phase's
only contact with the router is "send a prompt, get text back", exactly
what `tests/providers/test_router.py`'s own docstring says this task
should prefer.

The load-bearing test in this file is
`test_script_phase_rejects_a_reworded_scene_as_not_verbatim`: it proves the
verbatim-partition check in `avg.pipeline.phases.writing.ScriptPhase.run`
actually rejects a PARAPHRASED scene, not just a dropped one - a check that
only ever compares scene *count* would pass a reworded scene right through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from avg.config.channels import Channel
from avg.core.errors import AvgError, ErrorKind
from avg.core.events import ProgressEvent
from avg.core.models import ProjectDoc
from avg.pipeline.phase import CancellationToken, PhaseContext
from avg.pipeline.phases.writing import BriefPhase
from avg.pipeline.phases.director import Director1Phase
from avg.pipeline.phases.writing import ScriptPhase
from avg.pipeline.phases._common import read_json, script_dir, write_json
from avg.providers.text.router import TextResult

NARRATION = (
    "This tiny invention quietly changed how ancient sailors found their way. [pause] "
    "Nobody expected a bronze box of gears to predict the sky decades in advance."
)
SCENE_BLOCKS = (
    "@@SCENE@@\n"
    "This tiny invention quietly changed how ancient sailors found their way. [pause]\n"
    "@@IMAGE@@\nA weathered bronze mechanism glowing under lantern light\n"
    "@@LABEL@@\nHook\n"
    "@@SCENE@@\n"
    "Nobody expected a bronze box of gears to predict the sky decades in advance.\n"
    "@@IMAGE@@\nAncient gears turning in golden light\n"
    "@@LABEL@@\nReveal\n"
)


# ── shared test scaffolding ──────────────────────────────────────────────

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


class _QueueGenerator:
    """A fake `generate_text(prompt, phase, *, config)` replaying canned
    replies in call order, and recording every `(phase, prompt)` it saw."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, prompt: str, phase: str, *, config: Any) -> TextResult:
        self.calls.append((phase, prompt))
        if not self._replies:
            raise AssertionError("fake generator ran out of canned replies")
        text = self._replies.pop(0)
        return TextResult(text=text, provider="fake", model="fake")


# ── BriefPhase ───────────────────────────────────────────────────────────

def test_brief_phase_writes_brief_json_from_a_clean_json_reply(tmp_path, monkeypatch):
    reply = json.dumps({
        "emotional_arc": "hook to payoff", "tone": "curious and warm",
        "angle": "the machine that shouldn't have existed yet", "notes": "keep it tight",
    })
    fake = _QueueGenerator([reply])
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", fake)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    BriefPhase().run(ctx)

    brief = read_json(script_dir(ctx) / "brief.json")
    assert brief == {
        "emotional_arc": "hook to payoff",
        "tone": "curious and warm",
        "angle": "the machine that shouldn't have existed yet",
        "notes": "keep it tight",
    }
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "brief"
    assert any(isinstance(e, ProgressEvent) for e in events)


def test_brief_phase_unwraps_markdown_fence_and_surrounding_prose(tmp_path, monkeypatch):
    reply = (
        "Sure, here's the brief:\n```json\n"
        + json.dumps({"emotional_arc": "hook to payoff", "tone": "urgent", "angle": "an angle"})
        + "\n```\nHope that helps!"
    )
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    BriefPhase().run(ctx)

    brief = read_json(script_dir(ctx) / "brief.json")
    assert brief["emotional_arc"] == "hook to payoff"


def test_brief_phase_raises_avg_error_on_unparseable_reply(tmp_path, monkeypatch):
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator(["not json at all, sorry"]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    with pytest.raises(AvgError) as excinfo:
        BriefPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug
    assert not (script_dir(ctx) / "brief.json").exists()


def test_brief_phase_raises_avg_error_when_emotional_arc_is_missing(tmp_path, monkeypatch):
    reply = json.dumps({"tone": "curious", "angle": "an angle"})
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    with pytest.raises(AvgError) as excinfo:
        BriefPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug
    assert "emotional_arc" in excinfo.value.message


def test_brief_phase_inputs_ready_is_always_true(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    assert BriefPhase().inputs_ready(ctx) is True


# ── ScriptPhase ──────────────────────────────────────────────────────────

def test_script_phase_writes_narration_and_a_verbatim_scene_partition(tmp_path, monkeypatch):
    fake = _QueueGenerator([NARRATION, SCENE_BLOCKS])
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", fake)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    ScriptPhase().run(ctx)

    narration_path = script_dir(ctx) / "narration.txt"
    assert narration_path.read_text(encoding="utf-8").strip() == NARRATION

    scenes = read_json(script_dir(ctx) / "scenes.json")
    assert [s["index"] for s in scenes] == [1, 2]
    assert all(set(s) == {"index", "text", "image_prompt", "label"} for s in scenes)

    # The invariant: concatenating scene texts in order (whitespace
    # normalised only) reproduces the narration exactly.
    reconstructed = " ".join(" ".join(s["text"].split()) for s in scenes)
    assert " ".join(reconstructed.split()) == " ".join(NARRATION.split())

    assert fake.calls[0][0] == "script" and fake.calls[1][0] == "script"
    assert any(isinstance(e, ProgressEvent) for e in events)


def test_script_phase_preserves_pause_markers_through_segmentation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, SCENE_BLOCKS])
    )
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    ScriptPhase().run(ctx)

    scenes = read_json(script_dir(ctx) / "scenes.json")
    assert any("[pause]" in s["text"] for s in scenes)


def test_script_phase_rejects_a_reworded_scene_as_not_verbatim(tmp_path, monkeypatch):
    """The invariant must catch a PARAPHRASE, not just a dropped word count.
    Scene 1 here is reworded ("This small invention slowly altered..."
    instead of "This tiny invention quietly changed...") while scene 2 is
    untouched and the overall word count is unchanged - a check that only
    compared word counts or scene counts would let this through."""
    reworded_blocks = (
        "@@SCENE@@\n"
        "This small invention slowly altered how ancient sailors found their way. [pause]\n"
        "@@IMAGE@@\nA weathered bronze mechanism glowing under lantern light\n"
        "@@LABEL@@\nHook\n"
        "@@SCENE@@\n"
        "Nobody expected a bronze box of gears to predict the sky decades in advance.\n"
        "@@IMAGE@@\nAncient gears turning in golden light\n"
        "@@LABEL@@\nReveal\n"
    )
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, reworded_blocks])
    )
    project = _project(tmp_path)
    ctx, _ = _ctx(project)

    with pytest.raises(AvgError) as excinfo:
        ScriptPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug
    assert "verbatim" in excinfo.value.message
    # Nothing partial gets written - a rejected pass 2 must not leave a
    # narration.txt/scenes.json pair a later phase could mistake for real.
    assert not (script_dir(ctx) / "narration.txt").exists()
    assert not (script_dir(ctx) / "scenes.json").exists()


def test_script_phase_rejects_a_dropped_scene(tmp_path, monkeypatch):
    dropped_blocks = (
        "@@SCENE@@\n"
        "This tiny invention quietly changed how ancient sailors found their way. [pause]\n"
        "@@IMAGE@@\nA weathered bronze mechanism glowing under lantern light\n"
        "@@LABEL@@\nHook\n"
    )
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, dropped_blocks])
    )
    project = _project(tmp_path)
    ctx, _ = _ctx(project)

    with pytest.raises(AvgError) as excinfo:
        ScriptPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug


def test_script_phase_raises_when_pass_one_produces_no_narration(tmp_path, monkeypatch):
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", _QueueGenerator(["   "]))
    project = _project(tmp_path)
    ctx, _ = _ctx(project)

    with pytest.raises(AvgError) as excinfo:
        ScriptPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug
    assert "pass 1" in excinfo.value.message


def test_script_phase_raises_when_pass_two_has_no_parseable_scenes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.generate_text",
        _QueueGenerator([NARRATION, "Sorry, I can't format that as scenes."]),
    )
    project = _project(tmp_path)
    ctx, _ = _ctx(project)

    with pytest.raises(AvgError) as excinfo:
        ScriptPhase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug
    assert "pass 2" in excinfo.value.message


def test_script_phase_injects_brief_context_when_brief_json_exists(tmp_path, monkeypatch):
    (script_dir_path := tmp_path / "script").mkdir(parents=True, exist_ok=True)
    (script_dir_path / "brief.json").write_text(
        json.dumps({"emotional_arc": "hook to payoff", "tone": "curious", "angle": "a fresh angle"}),
        encoding="utf-8",
    )
    fake = _QueueGenerator([NARRATION, SCENE_BLOCKS])
    monkeypatch.setattr("avg.pipeline.phases.writing.generate_text", fake)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    ScriptPhase().run(ctx)

    narration_prompt = fake.calls[0][1]
    assert "CREATIVE DIRECTION" in narration_prompt
    assert "hook to payoff" in narration_prompt


def test_script_phase_runs_without_a_brief_present(tmp_path, monkeypatch):
    """The brief is optional enrichment, not a hard dependency - a project
    with no brief.json at all must still produce a script."""
    monkeypatch.setattr(
        "avg.pipeline.phases.writing.generate_text", _QueueGenerator([NARRATION, SCENE_BLOCKS])
    )
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    assert not (script_dir(ctx) / "brief.json").exists()
    ScriptPhase().run(ctx)
    assert (script_dir(ctx) / "scenes.json").exists()


def test_script_phase_inputs_ready_is_always_true(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    assert ScriptPhase().inputs_ready(ctx) is True


# ── Director1Phase ───────────────────────────────────────────────────────

def _write_scenes(ctx: PhaseContext, texts: list[str]) -> None:
    write_json(
        script_dir(ctx) / "scenes.json",
        [{"index": i + 1, "text": t, "image_prompt": "", "label": "Scene"} for i, t in enumerate(texts)],
    )


def test_director1_inputs_ready_false_without_scenes_json(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    assert Director1Phase().inputs_ready(ctx) is False


def test_director1_inputs_ready_true_with_scenes_json(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one text.", "Scene two text."])
    assert Director1Phase().inputs_ready(ctx) is True


def test_director1_raises_when_scenes_json_is_missing(tmp_path):
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    with pytest.raises(AvgError) as excinfo:
        Director1Phase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug


def test_director1_writes_video_map_from_a_clean_reply(tmp_path, monkeypatch):
    reply = json.dumps({
        "image_count": 2,
        "scenes": [
            {"scene": 1, "role": "hook", "beat": "HIT", "visual_intent": "POP"},
            {"scene": 2, "role": "reveal", "beat": "none", "visual_intent": "BACKGROUND"},
        ],
        "zoom_words": [{"scene": 1, "word": "bronze"}],
        "notes": "one image per scene",
    })
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    _write_scenes(ctx, ["Scene one text.", "Scene two text."])
    Director1Phase().run(ctx)

    video_map = read_json(script_dir(ctx) / "video_map.json")
    assert video_map["image_count"] == 2
    assert video_map["scenes"] == [
        {"scene": 1, "role": "hook", "beat": "HIT", "visual_intent": "POP"},
        {"scene": 2, "role": "reveal", "beat": "none", "visual_intent": "BACKGROUND"},
    ]
    assert video_map["zoom_words"] == [{"scene": 1, "word": "bronze"}]
    assert video_map["notes"] == "one image per scene"
    assert any(isinstance(e, ProgressEvent) for e in events)


def test_director1_raises_avg_error_on_unparseable_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "avg.pipeline.phases.director.generate_text", _QueueGenerator(["not json, sorry"])
    )
    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one text.", "Scene two text."])

    with pytest.raises(AvgError) as excinfo:
        Director1Phase().run(ctx)
    assert excinfo.value.kind == ErrorKind.bug
    assert not (script_dir(ctx) / "video_map.json").exists()


def test_director1_missing_image_count_degrades_to_scene_count(tmp_path, monkeypatch):
    reply = json.dumps({"scenes": [], "notes": ""})
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one.", "Scene two.", "Scene three."])
    Director1Phase().run(ctx)

    video_map = read_json(script_dir(ctx) / "video_map.json")
    assert video_map["image_count"] == 3


def test_director1_clamps_image_count_to_sane_bounds(tmp_path, monkeypatch):
    reply = json.dumps({"image_count": 99999, "scenes": [], "notes": ""})
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one.", "Scene two."])
    Director1Phase().run(ctx)

    video_map = read_json(script_dir(ctx) / "video_map.json")
    assert video_map["image_count"] == 160


def test_director1_degrades_invalid_scene_fields_instead_of_raising(tmp_path, monkeypatch):
    reply = json.dumps({
        "image_count": 2,
        "scenes": [
            {"scene": 1, "role": "not-a-real-role", "beat": "weird", "visual_intent": "sideways"},
            {"scene": 2, "role": "reveal", "beat": "hit", "visual_intent": "background"},
        ],
        "zoom_words": [{"scene": 0, "word": "dropped-invalid-scene"}, {"scene": 1, "word": "kept"}],
        "notes": "",
    })
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one.", "Scene two."])
    Director1Phase().run(ctx)

    video_map = read_json(script_dir(ctx) / "video_map.json")
    assert video_map["scenes"][0] == {"scene": 1, "role": "", "beat": "none", "visual_intent": "BACKGROUND"}
    # lower/mixed-case beat/visual_intent that ARE recognised once
    # normalised (case-insensitively) still come through correctly.
    assert video_map["scenes"][1] == {"scene": 2, "role": "reveal", "beat": "HIT", "visual_intent": "BACKGROUND"}
    assert video_map["zoom_words"] == [{"scene": 1, "word": "kept"}]


def test_director1_all_scenes_invalid_degrades_to_default_per_scene(tmp_path, monkeypatch):
    reply = json.dumps({"image_count": 2, "scenes": [{"role": "hook"}, {"scene": 0}], "notes": ""})
    monkeypatch.setattr("avg.pipeline.phases.director.generate_text", _QueueGenerator([reply]))

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_scenes(ctx, ["Scene one.", "Scene two."])
    Director1Phase().run(ctx)

    video_map = read_json(script_dir(ctx) / "video_map.json")
    assert video_map["scenes"] == [
        {"scene": 1, "role": "", "beat": "none", "visual_intent": "BACKGROUND"},
        {"scene": 2, "role": "", "beat": "none", "visual_intent": "BACKGROUND"},
    ]
