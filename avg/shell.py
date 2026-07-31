"""Native desktop shell for AVG Studio.

Wraps the FastAPI bridge (`avg.api.app.create_app`) in a `pywebview` window
so the app runs as a normal Windows desktop program rather than "open a
browser and hit localhost". The server and the window live in the same
process: uvicorn serves the ASGI app on a background daemon thread while
`webview.start()` blocks the main thread running the native window's
message loop.

The window opens first, on `splash_html()` - a page built from nothing, no
imports required. `main()` hands that straight to `webview.create_window`
and only then, inside `_boot` running on pywebview's own worker thread,
imports `fastapi`/`uvicorn`/`avg.api.app` (measured 2026-07-30 at ~1.6s
cold), builds the server, and points the window at the real URL. Before
this task those imports ran at module scope and the window did not exist
until they finished, so the app showed nothing at all for that 1.6s.

Two things here are load-bearing for safety, not just style:

- **Loopback only.** The server binds `HOST` (`127.0.0.1`), never
  `0.0.0.0`. Nothing outside this machine can ever reach it, regardless of
  firewall state - see `main`.
- **The token never touches disk.** `make_token()` generates a fresh
  `secrets.token_urlsafe(32)` every time `main()` runs. It lives only in
  this process's memory for the lifetime of this run; it is not written to
  a file, a config key, or an environment variable, and a new run always
  gets a new one.

Where projects live is decided by `resolve_output_root()`, not by a constant.
The legacy layout (`output_root = D:\\AI_Video_Gen\\output`, with
`config.json` derived from it by `create_app` as
`output_root.parent / "config" / "config.json"`) is right on the machine that
grew it, but this app ships as an installer meant to be carried to another
PC where that drive may not exist. See `resolve_output_root` for the order it
tries and why.
"""

from __future__ import annotations

import secrets
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import webview

if TYPE_CHECKING:
    # Only for the `started` annotation in `main` below - never imported at
    # runtime (that is the entire point of this task), so this costs mypy
    # nothing to check and the app nothing to run.
    import uvicorn

HOST = "127.0.0.1"


def resolve_output_root() -> Path:
    """Decide where this installation reads and writes projects.

    Task 11.5 removed the last guess here: once the user has been through
    the welcome screen, their choice is stored (`avg.config.settings`) and
    that stored value is authoritative - inference below is now only a
    fallback for the first run (before onboarding has happened) or a
    corrupt/unreadable settings file, never a substitute for an answer the
    user already gave.

    Order:
      1. `AVG_OUTPUT_ROOT` if set - the escape hatch for pointing a fresh
         install at an existing library (dev/test convenience).
      2. The stored settings document, if onboarding has completed.
      3. The legacy folder, if it actually exists - keeps the dev machine and
         anyone upgrading from the PowerShell app working before their first
         onboarding.
      4. `%USERPROFILE%/Videos/AVG Studio` - where a Windows user expects
         their videos to be, and created on demand.
    """
    # Imported here, not at module scope: `avg.config.settings` pulls in
    # pydantic, and this module's whole point is that nothing heavy runs
    # before the window is on screen. See `main`.
    from avg.config.settings import LEGACY_OUTPUT_ROOT, load_settings  # noqa: PLC0415

    override = os.environ.get("AVG_OUTPUT_ROOT", "").strip()
    if override:
        return Path(override)

    settings = load_settings()
    if settings.onboarded and settings.output_root:
        return Path(settings.output_root)

    if LEGACY_OUTPUT_ROOT.is_dir():
        return LEGACY_OUTPUT_ROOT
    fallback = Path.home() / "Videos" / "AVG Studio"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

_READY_TIMEOUT_S = 10.0
_READY_POLL_INTERVAL_S = 0.05


def pick_port() -> int:
    """Return a free ephemeral TCP port on the loopback interface.

    Binds `127.0.0.1:0` (port `0` asks the OS to assign any currently-free
    port), reads the assigned port back via `getsockname()`, then the `with`
    block closes the socket immediately - it is never left open past this
    function returning. There is an unavoidable race between that close and
    uvicorn's later bind to the same number (something else on the machine
    could grab it in between); that race is inherent to "ask the OS for a
    free port, then hand the number to a second process" and is accepted
    here, but this function does not widen it by holding the socket open any
    longer than it takes to read the port back.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


class _Api:
    """Exposed to the web UI as `window.pywebview.api` (see `webview.create_window`'s `js_api=`).

    The welcome screen's "Browse" needs a real Windows folder picker, not a
    text field pretending to be one - `create_file_dialog(FOLDER_DIALOG)` is
    pywebview's native dialog, and `js_api` is the only bridge from JS back
    into this process (the FastAPI/HTTP bridge has no reason to know about
    native dialogs at all, so this stays here rather than in `avg.api.app`).
    """

    def browse_folder(self) -> str | None:
        """Open a native folder picker and return the chosen path, or `None` if cancelled.

        `webview.windows[0]` is safe here because this app only ever opens
        one window (see `main`); `create_file_dialog` returns a tuple of
        paths (or `None` on cancel) even for a single-folder dialog, hence
        the `[0]`. `FileDialog.FOLDER` (not the deprecated module-level
        `FOLDER_DIALOG` alias, which this pywebview version warns on) is the
        same dialog under its current name.
        """
        result = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return None
        return result[0]


def make_token() -> str:
    """Generate a fresh, unguessable per-session token.

    `secrets.token_urlsafe(32)` - 32 random bytes, base64url-encoded, well
    above the 32-character floor `tests/test_shell.py` checks. Never
    persisted anywhere: it lives only in process memory for this run, and
    every run mints its own.
    """
    return secrets.token_urlsafe(32)


#: The splash's canvas colour, kept in step with `--canvas` in
#: `web/src/tokens.css`. Duplicated on purpose and duplicated nowhere else:
#: this page must render before anything is imported, so it cannot read a
#: stylesheet the server has not started serving yet.
SPLASH_CANVAS = "#1c1a16"
SPLASH_TEXT = "#f2ebdd"


def should_check_at_startup(values: dict[str, Any]) -> bool:
    """Whether to warm the component check while the library loads.

    Reads the `startup_check` app setting (`avg.core.settings_schema`).
    Anything that is not exactly `False` means yes: a malformed stored value
    is not a reason to silently skip a diagnostic, and the setting exists to
    save a moment, not to hide a problem.

    A pure function of the values dict so it can be called on the boot path
    without a server, an app instance, or a settings file - the same property
    `splash_html` has, and for the same reason.
    """
    return values.get("startup_check", True) is not False


def splash_html() -> str:
    """The page the window shows while the server is still being built.

    Inline HTML, not a file, and a pure function of nothing: it must render
    before `fastapi`, `uvicorn` and `avg.api.app` are imported (~1.6s cold,
    measured 2026-07-30), and a page that needed the server running to load
    would defeat its own purpose. That is also why there is no URL of any
    kind in it - a test asserts that.

    It carries the app's canvas colour, its name, and nothing that moves
    except a single slow pulse, so a slow machine does not show a spinner
    that outlives its welcome.
    """
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AVG Studio</title>
<style>
  html, body {{ height: 100%; margin: 0; }}
  body {{
    background: {SPLASH_CANVAS};
    color: {SPLASH_TEXT};
    display: grid;
    place-items: center;
    font-family: "Segoe UI", system-ui, sans-serif;
  }}
  .name {{ font-size: 30px; letter-spacing: 0.02em; animation: pulse 2.2s ease-in-out infinite; }}
  .sub {{ margin-top: 10px; font-size: 13px; opacity: 0.55; text-align: center; }}
  @keyframes pulse {{ 50% {{ opacity: 0.55; }} }}
  @media (prefers-reduced-motion: reduce) {{ .name {{ animation: none; }} }}
</style></head>
<body><div><div class="name">AVG Studio</div><div class="sub">Starting up…</div></div></body>
</html>"""


def _wait_until_ready(url: str, token: str, timeout: float = _READY_TIMEOUT_S) -> None:
    """Block until `url` answers authenticated with `token`, or raise.

    Starting uvicorn on a thread and immediately opening the webview window
    is a race: the window can start loading before the server has bound its
    socket and finished constructing the ASGI app, and would show a
    connection-error page instead of the app. Polling a real authenticated
    route (`GET /api/projects`, the same token the window itself will use)
    proves the whole stack - socket bound, ASGI app built, auth dependency
    wired - is actually answering, not just that something is listening on
    the port.

    Probes `/api/settings/schema` rather than `/api/projects` on purpose:
    the schema is a constant, while the project list waits for the startup
    library scan (see `avg.api.app`). Polling the list here would put that
    scan back on the critical path for opening the window, which is exactly
    what deferring it was for.

    Retries on any connection-level failure (nobody home yet) every
    `_READY_POLL_INTERVAL_S` seconds until `timeout` elapses, then raises a
    `RuntimeError` naming what never answered - never lets the window open
    against a dead server.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    request = urllib.request.Request(url, headers={"X-AVG-Token": token})
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        time.sleep(_READY_POLL_INTERVAL_S)
    raise RuntimeError(
        f"AVG API server did not answer {url!r} within {timeout}s"
    ) from last_error


def main() -> None:
    """Open the window, then build the server behind it.

    The order is the whole change. Importing `fastapi`, `uvicorn` and
    `avg.api.app` costs ~1.6s cold (measured 2026-07-30 with
    `python -X importtime`; `create_app` itself is 42ms), and all of it used
    to happen before pywebview was asked for a window - so the app showed
    nothing at all for that time. Now the window opens immediately on
    `splash_html()`, and `_boot` does the imports, the build and the server
    start on pywebview's own worker thread before pointing the window at the
    real URL.

    The imports still happen. They are what the server needs. They just
    happen behind a window that already looks like the app rather than
    behind nothing.
    """
    window = webview.create_window(
        "AVG Studio",
        html=splash_html(),
        js_api=_Api(),
        width=1280,
        height=820,
    )
    # `create_window` is typed as returning `Window | None`, but the only
    # way it returns `None` is if pywebview's GUI toolkit failed to
    # initialise at all - nothing this function has done by this point
    # could cause that, and if it happened there is no window to report the
    # failure through anyway. Asserting here rather than threading an
    # `Optional` through the rest of `main` and `_boot`.
    assert window is not None

    # Set by `_boot` so the teardown below can stop what it started. A list
    # rather than a `nonlocal` because `_boot` runs on another thread and
    # this is the simplest handoff that needs no lock: one writer, one
    # reader, and the reader only runs after `webview.start()` returns.
    started: list[tuple["uvicorn.Server", threading.Thread]] = []

    def _boot() -> None:
        # Everything below can fail - a corrupt settings file, a port that
        # vanishes between `pick_port` and the bind, `create_app` itself
        # raising - and every one of those failures has to reach the splash
        # rather than leave it on "Starting up…" forever with nothing
        # running and no way for the user to tell short of a task-manager
        # kill. Wrapping the whole body, not just `_wait_until_ready`'s wait,
        # is what makes that true regardless of which line fails.
        server: uvicorn.Server | None = None
        try:
            # Frozen builds ship their own ffmpeg/ffprobe (vendor/ffmpeg)
            # because a clean target machine has neither on PATH. This must
            # run before anything shells out - including a render child
            # process, which inherits this modified PATH from the
            # environment at the time it is spawned. No-op in development.
            # See avg.media.environment.
            #
            # Same reasoning for window flashes: this process warms
            # thumbnails by shelling out to ffprobe/ffmpeg, and a windowed
            # build has no console for them to inherit, so each call would
            # pop one.
            from avg.media.environment import prepend_to_path, suppress_console_windows  # noqa: PLC0415

            prepend_to_path()
            suppress_console_windows()

            # The expensive imports, deliberately here rather than at module
            # scope - this is the 1.6s the window no longer waits behind.
            import uvicorn  # noqa: PLC0415

            from avg.api.app import create_app  # noqa: PLC0415

            port = pick_port()
            token = make_token()
            app = create_app(token=token, output_root=resolve_output_root())

            server = uvicorn.Server(
                uvicorn.Config(app, host=HOST, port=port, log_level="warning")
            )
            server_thread = threading.Thread(
                target=server.run, daemon=True, name="avg-api-server"
            )
            server_thread.start()
            started.append((server, server_thread))

            _wait_until_ready(f"http://{HOST}:{port}/api/settings/schema", token)
        except Exception as exc:
            if server is not None:
                server.should_exit = True
            # Nothing below `avg/api/` may print, but this is the shell and
            # the window is the only surface there is: a failure here means
            # the app never started, and the splash must say so rather than
            # sit on "Starting up…" forever.
            window.load_html(
                splash_html().replace("Starting up…", f"Could not start: {exc}")
            )
            return

        window.load_url(f"http://{HOST}:{port}/?token={token}")

        from avg.config.settings import load_settings  # noqa: PLC0415

        if should_check_at_startup(load_settings().values):
            # Warms the five-minute component cache on the boot thread so
            # App health answers instantly the first time it is opened.
            # Never blocks the window: this runs after `load_url`.
            from avg.core.health import check_all  # noqa: PLC0415

            check_all()

    webview.start(_boot, private_mode=False)

    for server, server_thread in started:
        server.should_exit = True
        server_thread.join(timeout=5)


if __name__ == "__main__":
    # Required before anything else runs in a frozen build: avg.media.engine
    # spawns render children via multiprocessing's "spawn" start method
    # (the only one Windows has), which re-executes this same frozen exe
    # with special argv. Without freeze_support() that re-execution would
    # re-enter main() instead of the multiprocessing bootstrap and spawn
    # copies of the whole app recursively. It is a no-op in development and
    # a no-op on any platform other than Windows, so it is always safe here.
    import multiprocessing

    multiprocessing.freeze_support()
    main()
