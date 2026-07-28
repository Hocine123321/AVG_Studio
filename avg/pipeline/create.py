"""Project creation - the other end of `avg.config.legacy_import` (which
reads an existing project folder into a `ProjectDoc`; this writes a brand
new one) - and, since Slice 5 Task 3.5, the home of `save_project`, the one
function anywhere in this codebase allowed to write `project.json`.

`new_project` is the only entry point for creating a project. It creates the
project's folder on disk - including the input sub-folders the rest of this
app already expects a project to have (`avg.media.environment.render_workspace`'s
`_INPUT_DIRS`: `images/`, `audio/`, `render/`, `script/`) - and returns a
`ProjectDoc` with an empty `phases` dict, i.e. every phase `'pending'`.
`save_project` (below) is what actually persists it, and every later mutation
of that same document (a phase completing, a take being created/deleted/
renamed/selected) goes through the same function - see its own docstring.

That empty dict is the whole story behind this slice's "new-video start
phase is 0" invariant: `avg.pipeline.registry.next_phase_id` reads it as
"nothing done yet" and resolves to phase 0 (Brief) without this module
having to say so itself, or maintain any separate "start phase" field the
old app's `meta.phase` was. The old PowerShell app defaulted a video's start
phase to 1 and silently skipped the creative brief for every single run -
there is no analogous default to get wrong here, because there is no
separate field to default.
"""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from avg.config.channels import Channel
from avg.core.models import ProjectDoc
from avg.core.settings_schema import channel_defaults

# Sub-folders a fresh project needs before any phase can run, matching what
# `avg.media.environment.render_workspace` already copies out of an existing
# project (`_INPUT_DIRS`) - a new project must look the same shape from day
# one rather than growing folders ad hoc as each phase first writes to one.
_PROJECT_DIRS: tuple[str, ...] = ("script", "images", "audio", "render")


def _slugify(text: str) -> str:
    """Lowercase, ASCII-ish slug of `text`; never empty.

    Mirrors `avg.config.channels._slugify` (same rule: collapse anything
    non-alphanumeric to a single underscore, trim, fall back to a fixed
    placeholder for a symbol-only input) - kept as a private copy here
    rather than imported, since channel ids and project folder names are
    two independent namespaces that happen to want the same rule, not one
    shared concept.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "video"


def _unique_folder_name(slug: str, output_root: Path, now: datetime) -> str:
    """`<timestamp>_<slug>`, deduplicated against folders already on disk.

    Timestamps are second-resolution, so two projects created in the same
    second from the same topic (a real possibility for an automated/batch
    caller) would otherwise collide and one would silently adopt the
    other's folder.
    """
    base = f"{now.strftime('%Y%m%d_%H%M%S')}_{slug}"
    if not (output_root / base).exists():
        return base
    n = 2
    while (output_root / f"{base}_{n}").exists():
        n += 1
    return f"{base}_{n}"


def save_project(project: ProjectDoc) -> None:
    """Write `project` as `<project.folder>/project.json` - the ONE function
    in this codebase allowed to write that file (Slice 5, Task 3.5).

    Before takes, `project.json` was written exactly once, here, at creation,
    and never touched again - the pipeline's own artifacts on disk were
    enough for `avg.config.legacy_import` to re-derive phase state on the
    next launch. A take list can't be re-derived that way (which take
    changed what, its label, which one is current, are facts about history,
    not about files in a folder), so this is now called on every mutation
    that matters: a phase completing (`avg.pipeline.runner.run`'s
    `on_phase_change` hook - see that module's docstring for why the hook
    lives there rather than this function being called directly from inside
    the runner), and every take create/delete/rename/select
    (`avg.api.routes_takes`).

    Same discipline as `avg.config.settings.save_settings`: a temp file in
    `project.folder` itself, then `os.replace` over the real path - so
    `project.json` always holds either its complete old contents or its
    complete new contents, never a half-written file, even if the process
    is killed between the two calls (`os.replace` is atomic within one
    volume, which a temp file created via `dir=project.folder` guarantees -
    see `tempfile.mkstemp`'s `dir` argument). The temp file is removed on
    any failure so a crash never litters the project folder.
    """
    folder = Path(project.folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "project.json"

    fd, tmp_name = tempfile.mkstemp(dir=folder, prefix=".project-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(project.model_dump_json(indent=2))
        os.replace(tmp_name, target)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


def new_project(topic: str, channel: Channel, output_root: Path) -> ProjectDoc:
    """Create a new project's folder and document for `topic` under
    `output_root`, using `channel`'s creative settings.

    `phases` starts empty (every phase implicitly `'pending'`) and
    `master_output` starts `None` - a brand new project has produced
    nothing yet. `aspect_ratio` comes from the active channel's own
    `settings["aspect_ratio"]` (falling back to the settings catalogue's
    default, `avg.core.settings_schema.channel_defaults()`, if the channel
    hasn't set one) - creative settings live on the channel and nowhere
    else (see `avg.config.channels`'s module docstring). `short_edge` has
    no channel setting of its own yet, so it takes the same default
    `ProjectDoc` itself declares.
    """
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    folder_name = _unique_folder_name(_slugify(topic), output_root, now)
    folder = output_root / folder_name
    for sub in _PROJECT_DIRS:
        (folder / sub).mkdir(parents=True, exist_ok=True)

    aspect_ratio = channel.settings.get("aspect_ratio") or channel_defaults()["aspect_ratio"]

    doc = ProjectDoc(
        id=folder_name,
        title=topic,
        folder=folder,
        aspect_ratio=aspect_ratio,
        created=now,
        phases={},
    )
    save_project(doc)
    return doc
