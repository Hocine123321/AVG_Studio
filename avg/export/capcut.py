"""CapCut export (Slice 5, Task 5) - the other half of why takes exist.

AVG produces the raw material; CapCut is where the user actually finishes a
video. CapCut has no project-interchange format (no XML/EDL/AAF the way
Resolve or Premiere would have) - it only ever imports images, an audio
file, and an `.srt`. So there is nothing to "export to" in the usual sense;
the whole opportunity is a cleanly organised folder plus a sheet that saves
the one manual step CapCut can't do for you: setting each image's duration.

`export_take` assembles exactly this shape from **the selected take** (not
`project`'s current take - see `avg.api.routes_takes`'s own "current vs
selected" distinction, which this module deliberately does not collapse):

    <project>/exports/take-<id>/
      images/            001.png, 002.png, ... - copied straight from the
                         take's own `images/`, in filename order
      narration.mp3      the take's `audio/optimized_voice.mp3`
      captions.srt       the take's `render/captions.ass`, converted (see
                         `parse_ass_cues`/`write_srt` below)
      timing.csv         image, start, end, duration, text - one row per
                         image actually present, not per `scene_timings.json`
                         entry (see `_write_timing_csv`)
      master_output.mp4  the take's finished render, copied verbatim
      how-to-import.txt  a short plain-English note

Nothing here is generated - no model call, no pipeline phase, no cost. This
module only reads what phases already wrote and rearranges it.

**A take with no render exports nothing.** `export_take` raises `ValueError`
before creating so much as an empty folder if the take has no
`master_output` (or the file no longer exists on disk) - a half-complete
export folder that LOOKS finished but has no video would be worse than a
clear "nothing to export yet" error.

**Timings come from `render/scene_timings.json` - it is not recomputed.**
The render phase (`avg.pipeline.phases.render`, via the vendored engine)
already writes this file with each scene's `start`/`end`/`duration` in
seconds; this module only reads it. `timing.csv`'s rows are built by
iterating the images actually copied and looking up each one's own scene
number in that file - never the other way around - so a mismatch between
what `scene_timings.json` claims and what images actually exist shows up as
an empty timing row for that image, not a wrong one silently borrowed from
a neighbour.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
from contextlib import suppress
from pathlib import Path

from avg.core.models import ProjectDoc
from avg.core.takes import take_folder
from avg.pipeline.phases._shared import read_json, strip_delivery_markers

_HOW_TO_IMPORT = """How to import this into CapCut

1. Import the images/ folder as media. CapCut lays them out in filename
   order (001.png, 002.png, ...).
2. Drop narration.mp3 onto the audio track.
3. Import captions.srt as subtitles.
4. Open timing.csv and set each image's duration from it. The sheet lists
   the exact start, end and duration (in seconds) for every image, right
   next to the line of narration spoken over it, so nothing needs to be
   scrubbed for.

master_output.mp4 is the reference cut - the finished video this take
produced, in case you want to compare against it while you edit.
"""

# One dialogue line's fixed fields, per `avg.pipeline.phases.captions.
# _dialogue_line`: "Dialogue: <layer>,<start>,<end>,<style>,<name>,
# <marginL>,<marginR>,<marginV>,<effect>,<text>". Text is the LAST field and
# may itself contain commas, so it is captured greedily rather than split on
# every comma in the line.
_DIALOGUE_RE = re.compile(
    r"^Dialogue:\s*[^,]*,([^,]*),([^,]*),[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,(.*)$"
)

# Any `{...}` block - the pop-in override tag every cue carries
# (`captions.py`'s `_POP_IN_OVERRIDE`) and any other ASS override this repo
# ever writes. A literal brace in spoken narration is not a realistic case
# this codebase produces (captions text is either an AssemblyAI transcript
# or our own script text), so a plain non-greedy strip is enough - it is not
# trying to be a general ASS parser.
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")


def _parse_ass_time(value: str) -> float:
    """`H:MM:SS.CS` (as written by `avg.pipeline.phases.captions._ass_time`)
    to seconds. `H` may be more than one digit; CS is centiseconds."""
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _clean_ass_text(raw: str) -> str:
    """Strip override tags and unescape what `_ass_escape` escaped, leaving
    plain subtitle text - never the literal `{\\fscx65...}` markup."""
    text = _ASS_TAG_RE.sub("", raw)
    text = text.replace(r"\N", "\n").replace(r"\n", "\n")
    text = text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    return text.strip()


def parse_ass_cues(ass_text: str) -> list[tuple[float, float, str]]:
    """Every `Dialogue:` line in `ass_text` to `(start_seconds, end_seconds,
    clean_text)`, in file order. Lines that don't match the expected shape
    (or whose text is empty once tags are stripped) are skipped rather than
    raising - a caption file this codebase itself wrote should always match,
    but a stray malformed line must not take the whole export down."""
    cues: list[tuple[float, float, str]] = []
    for line in ass_text.splitlines():
        line = line.strip()
        if not line.startswith("Dialogue:"):
            continue
        m = _DIALOGUE_RE.match(line)
        if not m:
            continue
        text = _clean_ass_text(m.group(3))
        if not text:
            continue
        cues.append((_parse_ass_time(m.group(1)), _parse_ass_time(m.group(2)), text))
    return cues


def _srt_time(seconds: float) -> str:
    """`HH:MM:SS,mmm` - the one time format `.srt` accepts. Rounds to the
    nearest millisecond via integer math (never float arithmetic on the
    seconds themselves) so a value like 59.9997s carries into the next
    second/minute/hour correctly instead of printing an invalid `60` field."""
    total_ms = round(max(0.0, seconds) * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> None:
    """Write `cues` (as returned by `parse_ass_cues`) as a standard `.srt`:
    1-based index, `start --> end` timecode, text, blank line between
    entries. An empty `cues` list writes an empty file - a caption-less take
    is not this function's problem to flag (see `export_take`, which decides
    what's fatal)."""
    blocks = [
        f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n"
        for i, (start, end, text) in enumerate(cues, start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def _mmss(seconds: float) -> str:
    """`M:SS.mmm` for `timing.csv` - minutes unpadded, seconds zero-padded
    to 2 digits with 3 decimal places. Built from integer milliseconds (like
    `_srt_time`) so rounding never produces an out-of-range seconds field."""
    total_ms = round(max(0.0, seconds) * 1000)
    minutes, rem_ms = divmod(total_ms, 60_000)
    return f"{minutes}:{rem_ms / 1000:06.3f}"


def _scene_number(image_stem: str) -> int | None:
    """`"007"` -> `7`; anything that isn't a plain integer stem (never
    expected from `avg.pipeline.phases.visuals`'s own `{i:03d}.png` naming,
    but a defensive fallback rather than a crash) -> `None`."""
    try:
        return int(image_stem)
    except ValueError:
        return None


def _write_timing_csv(
    image_files: list[Path], scene_timings: list, scenes: list, path: Path
) -> None:
    """One row per file in `image_files` (the images actually copied into
    this export, in filename order) - never per `scene_timings.json` entry.
    A `scene_timings.json` that is stale, short, or has gaps (a scene that
    was skipped, or a take whose images were touched out of band) shows up
    as a blank start/end/duration for that one image, not a silently
    borrowed neighbour's timing and not a fatal error for the whole export."""
    timings_by_scene = {
        entry["scene"]: entry
        for entry in scene_timings
        if isinstance(entry, dict) and entry.get("type") == "scene" and isinstance(entry.get("scene"), int)
    }
    text_by_scene = {
        scene["index"]: strip_delivery_markers(str(scene.get("text") or ""))
        for scene in scenes
        if isinstance(scene, dict) and isinstance(scene.get("index"), int)
    }

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["image", "start", "end", "duration", "text"])
        for image_path in image_files:
            scene_num = _scene_number(image_path.stem)
            entry = timings_by_scene.get(scene_num) if scene_num is not None else None
            if entry is not None:
                start = float(entry.get("start") or 0.0)
                end = float(entry.get("end") or 0.0)
                duration = float(entry.get("duration") if entry.get("duration") is not None else end - start)
                start_s, end_s, duration_s = _mmss(start), _mmss(end), f"{duration:.3f}"
            else:
                start_s = end_s = duration_s = ""
            text = text_by_scene.get(scene_num, "") if scene_num is not None else ""
            writer.writerow([image_path.name, start_s, end_s, duration_s, text])


def export_take(project: ProjectDoc, take_id: int) -> Path:
    """Assemble `<project>/exports/take-<take_id>/` from that take's own
    folder (`avg.core.takes.take_folder`) and return its path.

    Raises `ValueError` (never a partial folder - see module docstring) if:
    `take_id` names no take on `project`; the take has no `master_output`
    yet, or the file no longer exists on disk; or the take has no images to
    export. Any other failure partway through (disk full, a missing
    narration file, anything) also removes the partial folder before
    re-raising - a caller must never find a folder that looks complete but
    is missing a file this function was supposed to write.

    Opens the destination folder in Explorer (`os.startfile`) once the
    export succeeds - skipped, silently, under pytest (`PYTEST_CURRENT_TEST`
    is set by pytest itself for the duration of every test), so the test
    suite never pops 40 Explorer windows.
    """
    take = next((t for t in project.takes if t.id == take_id), None)
    if take is None:
        raise ValueError(f"no take {take_id} on this project")

    if take.master_output is None or not Path(take.master_output).is_file():
        raise ValueError(f"take {take_id} has no finished render yet - nothing to export")

    source = take_folder(project, take_id)

    # Validate everything BEFORE creating a directory. Creating first and
    # cleaning up on failure leaves an empty `exports/` behind when the very
    # first check fails, which reads as "an export happened" to anyone looking
    # at the folder. Same no-half-states rule `clone_take` follows.
    image_files = sorted((source / "images").glob("*.png")) if (source / "images").is_dir() else []
    if not image_files:
        raise ValueError(f"take {take_id} has no images to export")

    audio_src = source / "audio" / "optimized_voice.mp3"
    if not audio_src.is_file():
        raise ValueError(f"take {take_id} has no narration audio to export")

    dest = Path(project.folder) / "exports" / f"take-{take_id}"
    if dest.exists():
        shutil.rmtree(dest)

    try:
        dest.mkdir(parents=True)

        images_dest = dest / "images"
        images_dest.mkdir()
        for image_path in image_files:
            shutil.copy2(image_path, images_dest / image_path.name)

        shutil.copy2(audio_src, dest / "narration.mp3")

        ass_path = source / "render" / "captions.ass"
        cues = parse_ass_cues(ass_path.read_text(encoding="utf-8")) if ass_path.is_file() else []
        write_srt(cues, dest / "captions.srt")

        scene_timings = read_json(source / "render" / "scene_timings.json")
        if not isinstance(scene_timings, list):
            scene_timings = []
        scenes = read_json(source / "script" / "scenes.json")
        if not isinstance(scenes, list):
            scenes = []
        _write_timing_csv(image_files, scene_timings, scenes, dest / "timing.csv")

        shutil.copy2(Path(take.master_output), dest / "master_output.mp4")

        (dest / "how-to-import.txt").write_text(_HOW_TO_IMPORT, encoding="utf-8")
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    if "PYTEST_CURRENT_TEST" not in os.environ:
        with suppress(Exception):
            os.startfile(dest)  # type: ignore[attr-defined]  # Windows-only, by design

    return dest
