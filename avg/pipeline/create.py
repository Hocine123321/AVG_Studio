"""Project creation - the other end of `avg.config.legacy_import` (which
reads an existing project folder into a `ProjectDoc`; this writes a brand
new one).

`new_project` is the only entry point. It creates the project's folder on
disk - including the input sub-folders the rest of this app already expects
a project to have (`avg.media.workspace.render_workspace`'s `_INPUT_DIRS`:
`images/`, `audio/`, `render/`, `script/`) - and returns a `ProjectDoc` with
an empty `phases` dict, i.e. every phase `'pending'`.

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

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from avg.config.channels import Channel
from avg.core.models import ProjectDoc
from avg.core.settings_schema import channel_defaults

# Sub-folders a fresh project needs before any phase can run, matching what
# `avg.media.workspace.render_workspace` already copies out of an existing
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


def _write_project_doc(doc: ProjectDoc) -> None:
    """Write `doc` as `<folder>/project.json`.

    A plain write, not the atomic temp-file-plus-`os.replace` dance
    `avg.config.channels`/`avg.config.settings` use for a document that gets
    repeatedly overwritten in place - `doc.folder` is freshly created by
    this same call and nothing else can be reading `project.json` from it
    yet, so there is no concurrent-reader/partial-write hazard to guard
    against here.
    """
    (doc.folder / "project.json").write_text(
        json.dumps(json.loads(doc.model_dump_json()), indent=2), encoding="utf-8"
    )


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
    _write_project_doc(doc)
    return doc
