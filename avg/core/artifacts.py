"""What each phase leaves on disk, and how to hand one file to the UI.

The Studio panel showed the word "Done." for six of the eight phases while
every one of them had written a real file. This is the table that fixes
that: artifact key -> relative path, label, and how to render it.

**Paths come from `avg.core.takes.PHASE_ARTIFACTS`, not from a second
table.** That map is what `clone_take` copies into a new take; a display
table that drifted from it would show a file in one take that silently
vanishes in the next. A test asserts every entry here is one that gets
copied.

**A key is never a path.** The route takes `key`, looks it up here, and a
miss is a 404 before any filesystem call. This is the surface that serves
files off a user's disk to a browser, so traversal is prevented by there
being nothing to traverse rather than by sanitising a caller's string.

No HTTP, no printing - this sits in `avg/core` like everything else the
API layer reads through.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from avg.core.models import ProjectDoc
from avg.core.takes import take_root


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    key: str
    rel_path: str
    label: str
    media_type: str
    #: How the panel renders it. `image_dir` is a directory listing rather
    #: than a single file; everything else is one file.
    kind: Literal["json", "text", "audio", "image_dir", "video"]


#: Keyed by phase key (`avg.pipeline.registry`), in the order the panel
#: should show them.
#:
#: Filenames are what THIS app writes, verified against the phase modules -
#: not what the legacy PowerShell app wrote, which used different names for
#: most of them (`script_draft.txt`, `timeline.json`, `markers.json`, one
#: mp3 per scene). A project imported from `D:\\AI_Video_Gen` therefore has
#: artifacts this table does not name, which is correct: it names what the
#: phases in this repo produce.
ARTIFACTS: dict[str, tuple[ArtifactSpec, ...]] = {
    "brief": (
        ArtifactSpec("brief", "script/brief.json", "Brief", "application/json", "json"),
    ),
    "script": (
        ArtifactSpec("narration", "script/narration.txt", "Narration", "text/plain", "text"),
        ArtifactSpec("scenes", "script/scenes.json", "Scenes", "application/json", "json"),
    ),
    "director1": (
        ArtifactSpec("video_map", "script/video_map.json", "Image plan", "application/json", "json"),
    ),
    "visuals": (
        ArtifactSpec("images", "images", "Images", "image/png", "image_dir"),
    ),
    # Two files, not one per scene: the voice phase assembles the whole
    # narration and then a silence-trimmed version of it
    # (`avg/pipeline/phases/voice.py`).
    "voice": (
        ArtifactSpec("raw_voice", "audio/raw_voice.mp3", "Raw narration", "audio/mpeg", "audio"),
        ArtifactSpec("optimized_voice", "audio/optimized_voice.mp3", "Optimized narration", "audio/mpeg", "audio"),
    ),
    "director2": (
        ArtifactSpec("direction_plan", "render/direction_plan.json", "Motion plan", "application/json", "json"),
        # Written here WITHOUT timings; Screen Text rewrites the same file
        # in place to add them, which is why it appears under both phases.
        ArtifactSpec("card_plan", "render/card_plan.json", "Caption cards", "application/json", "json"),
    ),
    # The artifact KEYS below still say "captions" - they name the file
    # (`render/captions.ass`), not the phase, and renaming them would
    # change the URLs the panel fetches for no gain.
    "screen_text": (
        ArtifactSpec("card_plan", "render/card_plan.json", "Timed caption cards", "application/json", "json"),
        ArtifactSpec("captions", "render/captions.ass", "Subtitle file", "text/plain", "text"),
    ),
}

_BY_KEY: dict[str, ArtifactSpec] = {
    spec.key: spec for specs in ARTIFACTS.values() for spec in specs
}


def spec_for(key: str) -> ArtifactSpec | None:
    """The spec for one artifact key, or `None` if the table has no such key."""
    return _BY_KEY.get(key)


def resolve(project: ProjectDoc, take_id: int, key: str) -> Path | None:
    """The file (or directory) for `key` in `take_id`, or `None`.

    `None` covers every failure the same way: unknown key, unknown take,
    nothing on disk. The caller turns it into one 404 - which of the three
    it was is not something a file-serving route should tell a client.

    Where a take's files live is `take_root`'s decision, not this
    module's: take 1 of a project created after Slice 8 stores its
    artifacts at the project root, a cloned or migrated take owns
    `takes/<id>/`.
    """
    spec = _BY_KEY.get(key)
    if spec is None:
        return None
    if not any(t.id == take_id for t in project.takes):
        return None
    candidate = take_root(project, take_id) / spec.rel_path
    return candidate if candidate.exists() else None


def available(project: ProjectDoc, take_id: int) -> dict[str, int]:
    """Artifact key -> size in bytes, for the ones that exist.

    A directory reports the NUMBER OF FILES it holds rather than a byte
    count: the panel uses it for "11 images", and summing a folder here
    would cost a stat per file for a number nothing displays.
    """
    found: dict[str, int] = {}
    for specs in ARTIFACTS.values():
        for spec in specs:
            path = resolve(project, take_id, spec.key)
            if path is None:
                continue
            if path.is_dir():
                found[spec.key] = sum(1 for f in path.iterdir() if f.is_file())
            else:
                found[spec.key] = path.stat().st_size
    return found
