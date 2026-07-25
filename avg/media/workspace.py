"""Isolates a render from the user's original project folder.

`render_project` writes `master_output.mp4` into the folder it is given and
reads its inputs (`images/`, `audio/`, `render/`) from that same folder. Point
it at a real project and a completed render silently overwrites the finished
video that project already holds — the user's actual work, with no undo.

Slice 1 re-renders existing projects specifically to compare the new output
against the original, so overwriting the original destroys the very thing
being compared against.

`render_workspace` gives the render a disposable copy to work in. The original
folder is opened read-only and never written to.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Copied into the workspace because the render reads them. Everything else in
# a project folder (previous outputs, logs, scratch) is not an input, so it is
# left behind rather than duplicated.
_INPUT_DIRS = ("images", "audio", "render", "script")
_INPUT_FILES = ("project.json", "pipeline_state.json")


@contextmanager
def render_workspace(project_folder: Path, keep: bool = False) -> Iterator[Path]:
    """Yield a disposable copy of `project_folder` for a render to write into.

    The copy carries only what the render reads. `master_output.mp4` is
    deliberately NOT copied: the workspace starts without one, so a file
    appearing there is unambiguously this render's output rather than a
    leftover that would make a failed render look successful.

    Set `keep=True` to leave the workspace on disk (for inspecting or
    comparing output); it is removed on exit otherwise, including on error.
    """
    workspace = Path(tempfile.mkdtemp(prefix="avg-render-"))
    try:
        for name in _INPUT_DIRS:
            source = project_folder / name
            if source.is_dir():
                shutil.copytree(source, workspace / name)
        for name in _INPUT_FILES:
            source = project_folder / name
            if source.is_file():
                shutil.copy2(source, workspace / name)
        yield workspace
    finally:
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)
