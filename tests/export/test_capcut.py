"""Tests for `avg/export/capcut.py` (Slice 5, Task 5): the CapCut export
folder assembled from a take's own artifacts.

Every fixture is built directly on `tmp_path` (mirroring `tests/api/
test_routes_takes.py`'s own `_seed_take1`/`_add_second_take` convention) -
nothing here touches `D:\\AI_Video_Gen\\output`, and nothing generates
anything: nothing except a `render/scene_timings.json` (as
`avg.pipeline.phases.render` already writes it via the vendored engine),
a real `render/captions.ass` (in the exact shape `avg.pipeline.phases.
captions._dialogue_line`/`_ass_header` produce), and a real `script/
scenes.json` (as `avg.pipeline.phases.writing` writes it).
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from avg.core.models import ProjectDoc, Take
from avg.core.takes import take_folder
from avg.export.capcut import export_take, parse_ass_cues, write_srt

# Matches `avg.pipeline.phases.captions._ass_header`/`_dialogue_line` field-
# for-field, including the pop-in override every real cue carries.
_POP_IN = r"{\fscx65\fscy65\t(0,90,0.7,\fscx108\fscy108)\t(90,190,\fscx100\fscy100)}"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_ass(cues: list[tuple[str, str, str]]) -> str:
    """`cues` as `(ass_start, ass_end, text)` -> a full, real `.ass` file
    (header + one `Dialogue:` line per cue), matching the pop-in override
    every real cue in this codebase gets."""
    header = (
        "[Script Info]\nTitle: AVG Captions\nScriptType: v4.00+\n"
        "PlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,3.00,0,2,10,10,40,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for start, end, text in cues:
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{_POP_IN}{text}")
    return "\n".join(lines) + "\n"


def _project(tmp_path: Path) -> ProjectDoc:
    return ProjectDoc(
        id="proj1",
        title="A Topic",
        folder=tmp_path / "proj1",
        created=datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc),
    )


def _seed_full_take(project: ProjectDoc, take_id: int = 2, scene_count: int = 3) -> Take:
    """A take with every artifact `export_take` reads: real images, a real
    narration file, a real `.ass` (converted from real cue text), a real
    `scene_timings.json` (contiguous scene windows, matching the pipeline's
    own "spans the whole audio duration exactly" invariant), a real
    `scenes.json`, and a `master_output.mp4` - "has a finished render"."""
    folder = take_folder(project, take_id)

    scene_texts = [f"This is scene number {i} of the story." for i in range(1, scene_count + 1)]
    for i in range(1, scene_count + 1):
        _write(folder / "images" / f"{i:03d}.png", f"fake-png-bytes-{i}")

    _write(folder / "audio" / "optimized_voice.mp3", "fake-mp3-bytes")

    scene_timings = []
    cursor = 0.0
    for i, text in enumerate(scene_texts, start=1):
        dur = 3.0 + i  # varied, deliberately not uniform
        scene_timings.append(
            {"type": "scene", "scene": i, "start": round(cursor, 3), "end": round(cursor + dur, 3), "duration": dur}
        )
        cursor += dur
    total_duration = cursor

    import json

    _write(folder / "render" / "scene_timings.json", json.dumps(scene_timings, indent=2))
    _write(
        folder / "script" / "scenes.json",
        json.dumps(
            [{"index": i, "text": t, "image_prompt": "x", "label": ""} for i, t in enumerate(scene_texts, start=1)],
            indent=2,
        ),
    )

    ass_cues = [
        ("0:00:00.00", "0:00:03.00", "This is scene"),
        ("0:00:03.00", "0:00:06.00", "number one of the story."),
        ("0:00:06.00", "0:00:09.00", "This is scene number two"),
    ]
    _write(folder / "render" / "captions.ass", _make_ass(ass_cues))

    output = folder / "master_output.mp4"
    _write(output, "fake-mp4-bytes")

    take = Take(
        id=take_id,
        label=f"Take {take_id}",
        created=project.created,
        phases={},
        master_output=output,
    )
    project.takes.append(take)
    return take


# ---------------------------------------------------------------------------
# The folder contains every named file
# ---------------------------------------------------------------------------


def test_export_creates_every_named_file(tmp_path):
    project = _project(tmp_path)
    _seed_full_take(project, take_id=2)

    dest = export_take(project, 2)

    assert dest == project.folder / "exports" / "take-2"
    assert sorted(p.name for p in (dest / "images").glob("*.png")) == ["001.png", "002.png", "003.png"]
    assert (dest / "narration.mp3").is_file()
    assert (dest / "captions.srt").is_file()
    assert (dest / "timing.csv").is_file()
    assert (dest / "master_output.mp4").is_file()
    assert (dest / "how-to-import.txt").is_file()

    # Real copies, not empty placeholders.
    assert (dest / "narration.mp3").read_text(encoding="utf-8") == "fake-mp3-bytes"
    assert (dest / "master_output.mp4").read_text(encoding="utf-8") == "fake-mp4-bytes"
    assert "images/" in (dest / "how-to-import.txt").read_text(encoding="utf-8")
    assert "narration.mp3" in (dest / "how-to-import.txt").read_text(encoding="utf-8")


def test_exports_the_selected_take_not_the_current_one(tmp_path):
    """Two takes exist; the project's CURRENT take is take 1, but the
    caller asks to export take 2 - `export_take` must honour the id it was
    given, not `project.current_take_id`."""
    project = _project(tmp_path)
    take1_folder = take_folder(project, 1)
    _write(take1_folder / "images" / "001.png", "take-1-image")
    take1 = Take(id=1, label="Take 1", created=project.created, phases={}, master_output=take1_folder / "master_output.mp4")
    _write(take1.master_output, "take-1-video")
    project.takes.append(take1)
    project.current_take_id = 1

    _seed_full_take(project, take_id=2)

    dest = export_take(project, 2)

    assert dest.name == "take-2"
    assert (dest / "master_output.mp4").read_text(encoding="utf-8") == "fake-mp4-bytes"
    # The image copied in is take 2's own, not take 1's (the current take).
    assert (dest / "images" / "001.png").read_text(encoding="utf-8") == "fake-png-bytes-1"


# ---------------------------------------------------------------------------
# timing.csv
# ---------------------------------------------------------------------------


def test_timing_csv_has_one_row_per_image_with_durations_summing_to_audio_length(tmp_path):
    project = _project(tmp_path)
    _seed_full_take(project, take_id=2, scene_count=4)
    dest = export_take(project, 2)

    with (dest / "timing.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    image_count = len(list((dest / "images").glob("*.png")))
    assert len(rows) == image_count == 4
    assert [r["image"] for r in rows] == ["001.png", "002.png", "003.png", "004.png"]

    # Every row carries the line of narration spoken over it.
    assert rows[0]["text"] == "This is scene number 1 of the story."

    total_duration = sum(float(r["duration"]) for r in rows)
    # The scene_timings fixture is contiguous and spans exactly this total
    # (scene i is 3+i seconds, i=1..4) - the same "the timings already span
    # the whole audio" invariant the real render phase guarantees.
    expected_total = sum(3.0 + i for i in range(1, 5))
    assert abs(total_duration - expected_total) < 0.1

    # M:SS.mmm, not raw seconds.
    assert rows[0]["start"] == "0:00.000"
    assert rows[1]["start"] == "0:04.000"


def test_timing_csv_lines_up_with_images_actually_present_not_stale_scene_timings(tmp_path):
    """`scene_timings.json` claims 3 scenes, but only 2 images actually
    exist in the take's folder (e.g. a partially-cleaned take) - the CSV
    must have exactly 2 rows (one per real image), not 3."""
    project = _project(tmp_path)
    take = _seed_full_take(project, take_id=2, scene_count=3)
    (take_folder(project, 2) / "images" / "003.png").unlink()

    dest = export_take(project, 2)

    with (dest / "timing.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 2
    assert [r["image"] for r in rows] == ["001.png", "002.png"]


def test_timing_csv_blanks_a_row_whose_scene_has_no_timing_entry(tmp_path):
    """An image exists but `scene_timings.json` has no entry for its scene
    number - the row is blank for that image, not silently borrowed from a
    neighbouring scene."""
    import json

    project = _project(tmp_path)
    _seed_full_take(project, take_id=2, scene_count=3)
    st_path = take_folder(project, 2) / "render" / "scene_timings.json"
    data = json.loads(st_path.read_text(encoding="utf-8"))
    data = [e for e in data if e["scene"] != 2]  # drop scene 2's timing entirely
    st_path.write_text(json.dumps(data), encoding="utf-8")

    dest = export_take(project, 2)
    with (dest / "timing.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    row2 = next(r for r in rows if r["image"] == "002.png")
    assert row2["start"] == row2["end"] == row2["duration"] == ""


# ---------------------------------------------------------------------------
# captions.srt
# ---------------------------------------------------------------------------


def _parse_srt_blocks(text: str) -> list[str]:
    """Minimal, dependency-free `.srt` block splitter for test assertions
    only - not a general parser, just enough to count cues and inspect
    each one's text."""
    return [b for b in text.strip().split("\n\n") if b.strip()]


def test_srt_parses_and_cue_count_matches_caption_count(tmp_path):
    project = _project(tmp_path)
    _seed_full_take(project, take_id=2)
    dest = export_take(project, 2)

    srt_text = (dest / "captions.srt").read_text(encoding="utf-8")
    blocks = _parse_srt_blocks(srt_text)
    assert len(blocks) == 3  # matches the 3 Dialogue lines in the fixture .ass

    for i, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        assert lines[0] == str(i)
        assert "-->" in lines[1]


def test_ass_override_tags_do_not_leak_into_srt_text(tmp_path):
    project = _project(tmp_path)
    _seed_full_take(project, take_id=2)
    dest = export_take(project, 2)

    srt_text = (dest / "captions.srt").read_text(encoding="utf-8")
    assert "\\fscx" not in srt_text
    assert "{" not in srt_text
    assert "}" not in srt_text
    assert "This is scene" in srt_text
    assert "number one of the story." in srt_text


def test_parse_ass_cues_strips_pop_in_override(tmp_path):
    """Unit-level check on the conversion helper itself, independent of the
    whole export pipeline."""
    ass_text = _make_ass([("0:00:00.00", "0:00:02.50", "hello world")])
    cues = parse_ass_cues(ass_text)
    assert cues == [(0.0, 2.5, "hello world")]


def test_write_srt_formats_standard_timecodes(tmp_path):
    path = tmp_path / "out.srt"
    write_srt([(0.0, 1.234, "one"), (61.5, 62.0, "two")], path)
    text = path.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,234" in text
    assert "00:01:01,500 --> 00:01:02,000" in text


# ---------------------------------------------------------------------------
# A take with no render
# ---------------------------------------------------------------------------


def test_export_a_take_with_no_render_fails_clearly_not_partially(tmp_path):
    project = _project(tmp_path)
    folder = take_folder(project, 2)
    _write(folder / "images" / "001.png", "fake-png")
    take = Take(id=2, label="Take 2", created=project.created, phases={}, master_output=None)
    project.takes.append(take)

    with pytest.raises(ValueError, match="no finished render"):
        export_take(project, 2)

    assert not (project.folder / "exports").exists()


def test_export_a_take_whose_master_output_file_is_missing_fails_clearly(tmp_path):
    """`master_output` is SET but the file itself was deleted out-of-band -
    still "no render to export", not a copy error partway through."""
    project = _project(tmp_path)
    folder = take_folder(project, 2)
    missing_output = folder / "master_output.mp4"  # never written
    take = Take(id=2, label="Take 2", created=project.created, phases={}, master_output=missing_output)
    project.takes.append(take)

    with pytest.raises(ValueError, match="no finished render"):
        export_take(project, 2)

    assert not (project.folder / "exports").exists()


def test_export_an_unknown_take_id_raises(tmp_path):
    project = _project(tmp_path)
    _seed_full_take(project, take_id=2)

    with pytest.raises(ValueError, match="no take 99"):
        export_take(project, 99)


def test_export_a_take_with_no_images_fails_clearly(tmp_path):
    project = _project(tmp_path)
    folder = take_folder(project, 2)
    output = folder / "master_output.mp4"
    _write(output, "fake-mp4")
    take = Take(id=2, label="Take 2", created=project.created, phases={}, master_output=output)
    project.takes.append(take)

    with pytest.raises(ValueError, match="no images"):
        export_take(project, 2)

    assert not (project.folder / "exports").exists()


def test_reexporting_the_same_take_replaces_the_old_folder(tmp_path):
    """A second export of the same take must not leave stale files from a
    previous export (e.g. an image removed between the two runs)."""
    project = _project(tmp_path)
    _seed_full_take(project, take_id=2, scene_count=3)
    dest = export_take(project, 2)
    assert len(list((dest / "images").glob("*.png"))) == 3

    (take_folder(project, 2) / "images" / "003.png").unlink()
    dest2 = export_take(project, 2)
    assert dest2 == dest
    assert sorted(p.name for p in (dest2 / "images").glob("*.png")) == ["001.png", "002.png"]


# ---------------------------------------------------------------------------
# os.startfile
# ---------------------------------------------------------------------------


def test_export_does_not_open_explorer_under_pytest(tmp_path, monkeypatch):
    """`PYTEST_CURRENT_TEST` is set by pytest itself for the duration of
    every test - `export_take` must see it and skip `os.startfile` entirely,
    never even calling it wrapped in a try/except."""
    calls = []
    # os.startfile doesn't exist on non-Windows Pythons; add it if absent so
    # the spy can be installed uniformly regardless of platform.
    if not hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(os, "startfile", lambda *a, **k: calls.append(a), raising=False)
    assert "PYTEST_CURRENT_TEST" in os.environ

    project = _project(tmp_path)
    _seed_full_take(project, take_id=2)
    export_take(project, 2)

    assert calls == []
