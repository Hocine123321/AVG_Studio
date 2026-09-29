"""Throwaway dev server for looking at the built UI against real project
data, with zero packaging step - this branch's equivalent of the Windows
build's tools/_dev_serve.py, which hardcodes a Windows-only D:\\... path
and isn't touched here (see docs/linux-port/WINDOWS_VS_LINUX.md - this
branch doesn't retrofit cross-platform branching into files that already
work fine for their own platform).

The point of this file: `tools/build_flatpak.ps1`'s "Build flatpak
package" always does a real rebuild of this app's own module, by
flatpak-builder's own design - `type: dir` sources are documented as
not supporting caching at all ("they will be rebuilt each time"),
unlike archive/git sources. That isn't a bug to chase away; it's what a
`dir` source *is*. The Windows build's fast dev loop was never
rebuilding its installer either - it runs this exact same script, no
PyInstaller, no Inno Setup, nothing to package. Use this file the same
way: for iterating on avg/ or web/ code, this starts in under a
second. Reserve `tools/build_flatpak.ps1`'s package option for what
only it can tell you - whether the actual sandboxed package
(permissions, the bundled Qt/krb5 resolution) still works - not for
every source change.

Normally launched via `tools/build_flatpak.ps1`'s "Launch from source"
option, which sets up (or reuses) a local editable-install dev venv and
runs this automatically. To run it by hand instead:
    python3 -m venv .venv-linux && . .venv-linux/bin/activate
    pip install -e ".[dev]"
    cd web && npm install && npm run build && cd ..
    python tools/dev_serve_linux.py
`.[dev]`, not `.[dev,gui]` or bare `.`: this only ever imports
avg.api.app, never avg.shell's pywebview/PySide6 layer (see
avg.shell's own docstring - its `import webview` is lazy specifically
so this file can import `resolve_output_root` from it without pulling
that in), so the `gui` extra's ~280-300MB of PySide6/PySide6-Addons is
never needed here at all.

Then open http://127.0.0.1:8756/?token=devtoken in a regular browser -
no native window, no pywebview, no Qt involved at all, which is exactly
why this starts so much faster than either the packaged app or a
`python -m avg.shell` run. For the real native window without Flatpak's
sandbox in the way (still no packaging step, but needs the `gui` extra
too: `pip install -e ".[dev,gui]"`): `python -m avg.shell`.

Not part of the shipped app; avg.shell owns the real launch path.
"""

import uvicorn

from avg.api.app import create_app
from avg.shell import resolve_output_root

TOKEN = "devtoken"

# resolve_output_root(), not a hardcoded path: it already does the right
# thing here (checks AVG_OUTPUT_ROOT, then settings.json, then falls back
# to $XDG_VIDEOS_DIR/AVG Studio - see avg/shell.py) rather than this file
# needing its own copy of that logic.
app = create_app(token=TOKEN, output_root=resolve_output_root())

if __name__ == "__main__":
    print(f"http://127.0.0.1:8756/?token={TOKEN}")
    uvicorn.run(app, host="127.0.0.1", port=8756, log_level="info")
